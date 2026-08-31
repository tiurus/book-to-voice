from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.jobs import Job, JobManager, QueueFull
from app.schemas import VOICES, AudioInfo, JobResponse, SpeechRequest
from app.storage import AudioStore
from app.synthesizer import SileroSynthesizer
from app.text import InvalidSSML, validate_ssml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def _audio_info(file_id: str, file_format: str, size: int, duration: float) -> AudioInfo:
    url = f"/api/audio/{file_id}?format={file_format}"
    return AudioInfo(
        url=url,
        download_url=f"{url}&download=true",
        format=file_format,  # type: ignore[arg-type]
        size_bytes=size,
        duration_seconds=duration,
    )


def _serialize_job(job: Job, manager: JobManager) -> JobResponse:
    response = JobResponse(
        job_id=job.job_id,
        state=job.state,
        position=manager.position(job.job_id),
        error_code=job.error_code,
        error=job.error,
    )
    if job.result:
        item = job.result
        response.file_id = item.file_id
        response.audio = {
            "wav": _audio_info(item.file_id, "wav", item.wav_size, item.duration_seconds),
            "mp3": _audio_info(item.file_id, "mp3", item.mp3_size, item.duration_seconds),
        }
    return response


async def _cleanup_loop(store: AudioStore, retention_hours: int) -> None:
    while True:
        await asyncio.sleep(3600)
        deleted = await asyncio.to_thread(store.cleanup, retention_hours)
        if deleted:
            logger.info("Removed %d expired audio result(s)", deleted)


def create_app(
    settings: Settings | None = None,
    synthesizer: SileroSynthesizer | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = synthesizer.store if synthesizer else AudioStore(settings.audio_dir)
    synthesizer = synthesizer or SileroSynthesizer(settings, store)
    manager = JobManager(synthesizer, settings.queue_size)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store.cleanup(settings.retention_hours)
        await asyncio.to_thread(synthesizer.load)
        manager.start()
        cleanup_task = asyncio.create_task(
            _cleanup_loop(store, settings.retention_hours), name="audio-cleanup"
        )
        yield
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await manager.stop()

    app = FastAPI(
        title="Книжный голос",
        description="Локальный сервис русской озвучки на Silero TTS",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.synthesizer = synthesizer
    app.state.manager = manager

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        engine = request.app.state.synthesizer
        return {
            "status": "ready" if engine.ready else "degraded",
            "model_ready": engine.ready,
            "model": settings.model_id,
            "error": engine.load_error,
            "queue_size": manager.queue.qsize(),
        }

    @app.get("/api/voices")
    async def voices() -> dict[str, object]:
        return {
            "model": settings.model_id,
            "voices": list(VOICES),
            "sample_rates": [8000, 24000, 48000],
            "speeds": ["slow", "normal", "fast"],
            "supports_auto_stress": True,
            "supports_ssml": True,
            "max_text_length": settings.max_text_length,
        }

    @app.post("/api/speech", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
    async def speech(payload: SpeechRequest) -> JobResponse:
        if len(payload.text) > settings.max_text_length:
            raise HTTPException(
                status_code=422,
                detail=f"Текст не должен быть длиннее {settings.max_text_length} символов",
            )
        if payload.ssml:
            try:
                validate_ssml(payload.text)
            except InvalidSSML as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not synthesizer.ready:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "model_load_error",
                    "message": synthesizer.load_error or "Модель ещё загружается",
                },
            )
        try:
            job = manager.enqueue(payload)
        except QueueFull as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return _serialize_job(job, manager)

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    async def job_status(job_id: str) -> JobResponse:
        try:
            return _serialize_job(manager.get(job_id), manager)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/audio/{file_id}")
    async def audio(
        file_id: str,
        format: str = Query("wav", pattern="^(wav|mp3)$"),
        download: bool = False,
    ) -> FileResponse:
        try:
            store.get(file_id)
            path = store.path(file_id, format)
            if not path.is_file():
                raise FileNotFoundError(file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Аудиофайл не найден") from exc
        media_type = "audio/wav" if format == "wav" else "audio/mpeg"
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"speech-{file_id[:8]}.{format}" if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.delete(
        "/api/audio/{file_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
    )
    async def delete_audio(file_id: str) -> Response:
        try:
            removed = store.delete(file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Аудиофайл не найден") from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Аудиофайл не найден")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
