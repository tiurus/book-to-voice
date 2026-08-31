from __future__ import annotations

import logging
import os
import subprocess
import urllib.request
import wave
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.schemas import SpeechRequest, Speed
from app.storage import AudioStore, StoredAudio
from app.text import split_text

logger = logging.getLogger(__name__)
SPEED_FACTORS = {Speed.slow: 0.85, Speed.normal: 1.0, Speed.fast: 1.15}


class ModelNotReady(RuntimeError):
    pass


class ConversionError(RuntimeError):
    pass


class SileroSynthesizer:
    def __init__(self, settings: Settings, store: AudioStore) -> None:
        self.settings = settings
        self.store = store
        self.model = None
        self.load_error: str | None = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        if not self.settings.load_model:
            self.load_error = "Загрузка модели отключена настройкой TTS_LOAD_MODEL"
            return
        try:
            import torch

            torch.set_num_threads(self.settings.torch_threads)
            self.settings.model_dir.mkdir(parents=True, exist_ok=True)
            model_path = self.settings.model_path
            if not model_path.exists():
                temporary = model_path.with_suffix(".pt.tmp")
                logger.info("Downloading Silero model %s", self.settings.model_id)
                urllib.request.urlretrieve(self.settings.model_url, temporary)
                temporary.replace(model_path)
            importer = torch.package.PackageImporter(str(model_path))
            self.model = importer.load_pickle("tts_models", "model")
            self.model.to(torch.device("cpu"))
            self.load_error = None
            logger.info("Silero model %s is ready", self.settings.model_id)
        except Exception as exc:
            self.load_error = str(exc)
            logger.exception("Unable to load Silero model")

    def _write_wav(self, samples, path: Path, sample_rate: int) -> None:
        pcm = (
            samples.detach()
            .flatten()
            .clamp(-1.0, 1.0)
            .mul(32767)
            .to(dtype=__import__("torch").int16)
            .cpu()
            .numpy()
        )
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm.tobytes())

    def _run_ffmpeg(self, arguments: list[str], error_label: str) -> None:
        command = [self.settings.ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y"]
        try:
            result = subprocess.run(
                [*command, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConversionError(f"{error_label}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip()[-500:] or "неизвестная ошибка FFmpeg"
            raise ConversionError(f"{error_label}: {detail}")

    def synthesize(self, request: SpeechRequest) -> StoredAudio:
        if self.model is None:
            raise ModelNotReady(self.load_error or "Модель ещё не загружена")

        import torch

        file_id = uuid4().hex
        base_path = self.store.directory / f"{file_id}.base.tmp"
        wav_path = self.store.path(file_id, "wav")
        mp3_path = self.store.path(file_id, "mp3")

        if request.ssml:
            chunks = [request.text]
        else:
            chunks = split_text(request.text, self.settings.max_chunk_length)

        audio_parts = []
        pause = torch.zeros(int(request.sample_rate * 0.12), dtype=torch.float32)
        fade_size = max(1, int(request.sample_rate * 0.005))
        try:
            with torch.inference_mode():
                for index, chunk in enumerate(chunks):
                    kwargs = {
                        "speaker": request.voice,
                        "sample_rate": request.sample_rate,
                    }
                    if request.ssml:
                        kwargs["ssml_text"] = chunk
                    else:
                        kwargs["text"] = chunk
                        kwargs["put_accent"] = request.auto_stress
                        kwargs["put_yo"] = request.auto_stress
                    audio = self.model.apply_tts(**kwargs).detach().flatten().cpu().float()
                    if audio.numel() >= fade_size * 2:
                        audio[:fade_size] *= torch.linspace(0, 1, fade_size)
                        audio[-fade_size:] *= torch.linspace(1, 0, fade_size)
                    if index:
                        audio_parts.append(pause)
                    audio_parts.append(audio)

            combined = torch.cat(audio_parts)
            self._write_wav(combined, base_path, request.sample_rate)
            speed = SPEED_FACTORS[request.speed]
            if speed == 1.0:
                os.replace(base_path, wav_path)
            else:
                self._run_ffmpeg(
                    [
                        "-i",
                        str(base_path),
                        "-af",
                        f"atempo={speed}",
                        "-c:a",
                        "pcm_s16le",
                        str(wav_path),
                    ],
                    "Не удалось изменить скорость WAV",
                )
                base_path.unlink(missing_ok=True)
            self._run_ffmpeg(
                ["-i", str(wav_path), "-c:a", "libmp3lame", "-q:a", "3", str(mp3_path)],
                "Не удалось создать MP3",
            )
            with wave.open(str(wav_path), "rb") as wav_file:
                duration = wav_file.getnframes() / wav_file.getframerate()
            item = StoredAudio(
                file_id=file_id,
                duration_seconds=round(duration, 3),
                wav_size=wav_path.stat().st_size,
                mp3_size=mp3_path.stat().st_size,
                created_at=__import__("time").time(),
            )
            self.store.save_metadata(item)
            return item
        except Exception:
            for path in (base_path, wav_path, mp3_path):
                path.unlink(missing_ok=True)
            raise
