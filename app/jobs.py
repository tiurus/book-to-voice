from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from uuid import uuid4

from app.schemas import JobState, SpeechRequest
from app.storage import StoredAudio
from app.synthesizer import ConversionError, ModelNotReady, SileroSynthesizer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Job:
    job_id: str
    request: SpeechRequest
    state: JobState = JobState.queued
    result: StoredAudio | None = None
    error_code: str | None = None
    error: str | None = None


class QueueFull(RuntimeError):
    pass


class JobManager:
    def __init__(self, synthesizer: SileroSynthesizer, queue_size: int) -> None:
        self.synthesizer = synthesizer
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self.jobs: dict[str, Job] = {}
        self.worker_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.worker_task = asyncio.create_task(self._worker(), name="tts-worker")

    async def stop(self) -> None:
        if self.worker_task:
            self.worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker_task

    def enqueue(self, request: SpeechRequest) -> Job:
        job = Job(job_id=uuid4().hex, request=request)
        try:
            self.queue.put_nowait(job.job_id)
        except asyncio.QueueFull as exc:
            raise QueueFull("Очередь заполнена. Повторите попытку позже") from exc
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise KeyError("Задание не найдено") from exc

    def position(self, job_id: str) -> int | None:
        job = self.get(job_id)
        if job.state is not JobState.queued:
            return None
        queued = list(self.queue._queue)  # asyncio.Queue intentionally exposes no snapshot API
        try:
            return queued.index(job_id) + 1
        except ValueError:
            return 1

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.jobs[job_id]
            job.state = JobState.processing
            try:
                job.result = await asyncio.to_thread(self.synthesizer.synthesize, job.request)
                job.state = JobState.completed
            except ModelNotReady as exc:
                job.state = JobState.failed
                job.error_code = "model_load_error"
                job.error = str(exc)
            except ConversionError as exc:
                job.state = JobState.failed
                job.error_code = "mp3_conversion_error"
                job.error = str(exc)
            except Exception:
                logger.exception("Synthesis failed for job %s", job_id)
                job.state = JobState.failed
                job.error_code = "synthesis_error"
                job.error = "Не удалось озвучить текст"
            finally:
                self.queue.task_done()
