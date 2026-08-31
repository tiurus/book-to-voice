from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import urllib.request
import wave
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.schemas import FileSpeechRequest, SpeechRequest, Speed
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
                timeout=3600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConversionError(f"{error_label}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip()[-500:] or "неизвестная ошибка FFmpeg"
            raise ConversionError(f"{error_label}: {detail}")

    def _apply_model(self, text: str, request: SpeechRequest | FileSpeechRequest):
        import torch

        kwargs = {
            "speaker": request.voice,
            "sample_rate": request.sample_rate,
            "text": text,
            "put_accent": request.auto_stress,
            "put_yo": request.auto_stress,
        }
        audio = self.model.apply_tts(**kwargs).detach().flatten().cpu().float()
        fade_size = max(1, int(request.sample_rate * 0.005))
        if audio.numel() >= fade_size * 2:
            audio[:fade_size] *= torch.linspace(0, 1, fade_size)
            audio[-fade_size:] *= torch.linspace(1, 0, fade_size)
        return audio

    def _finish_audio(
        self,
        base_path: Path,
        wav_path: Path,
        mp3_path: Path,
        speed: Speed,
        progress: Callable[[str, int], None] | None = None,
    ) -> None:
        speed_factor = SPEED_FACTORS[speed]
        if progress:
            progress("merging", 90)
        if speed_factor == 1.0:
            os.replace(base_path, wav_path)
        else:
            self._run_ffmpeg(
                [
                    "-i",
                    str(base_path),
                    "-af",
                    f"atempo={speed_factor}",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                "Не удалось изменить скорость WAV",
            )
            base_path.unlink(missing_ok=True)
        if progress:
            progress("converting", 95)
        self._run_ffmpeg(
            ["-i", str(wav_path), "-c:a", "libmp3lame", "-q:a", "3", str(mp3_path)],
            "Не удалось создать MP3",
        )

    def _metadata(self, file_id: str, wav_path: Path, mp3_path: Path) -> StoredAudio:
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
        try:
            with torch.inference_mode():
                for index, chunk in enumerate(chunks):
                    if request.ssml:
                        kwargs = {
                            "speaker": request.voice,
                            "sample_rate": request.sample_rate,
                        }
                        kwargs["ssml_text"] = chunk
                        audio = self.model.apply_tts(**kwargs).detach().flatten().cpu().float()
                    else:
                        audio = self._apply_model(chunk, request)
                    if index:
                        audio_parts.append(pause)
                    audio_parts.append(audio)

            combined = torch.cat(audio_parts)
            self._write_wav(combined, base_path, request.sample_rate)
            self._finish_audio(base_path, wav_path, mp3_path, request.speed)
            return self._metadata(file_id, wav_path, mp3_path)
        except Exception:
            for path in (base_path, wav_path, mp3_path):
                path.unlink(missing_ok=True)
            raise

    def synthesize_document(
        self,
        request: FileSpeechRequest,
        update: Callable[[int, int, str, int], None],
    ) -> StoredAudio:
        if self.model is None:
            raise ModelNotReady(self.load_error or "Модель ещё не загружена")

        import torch

        chunks = split_text(request.text, self.settings.max_chunk_length)
        total = len(chunks)
        file_id = uuid4().hex
        base_path = self.store.directory / f"{file_id}.base.wav"
        wav_path = self.store.path(file_id, "wav")
        mp3_path = self.store.path(file_id, "mp3")
        update(0, total, "synthesizing", 1)

        try:
            with tempfile.TemporaryDirectory(
                prefix=f"{file_id}-", suffix=".tmp", dir=self.store.directory
            ) as temp_name:
                temp_dir = Path(temp_name).resolve()
                fragment_paths: list[Path] = []
                pause = torch.zeros(int(request.sample_rate * 0.12), dtype=torch.float32)
                with torch.inference_mode():
                    for index, chunk in enumerate(chunks, 1):
                        audio = self._apply_model(chunk, request)
                        if index < total:
                            audio = torch.cat((audio, pause))
                        fragment = temp_dir / f"fragment-{index:06d}.wav"
                        self._write_wav(audio, fragment, request.sample_rate)
                        fragment_paths.append(fragment)
                        update(index, total, "synthesizing", max(1, round(index / total * 85)))

                concat_file = temp_dir / "concat.txt"
                concat_file.write_text(
                    "".join(f"file '{path.as_posix()}'\n" for path in fragment_paths),
                    encoding="utf-8",
                )
                update(total, total, "merging", 90)
                self._run_ffmpeg(
                    [
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_file),
                        "-c:a",
                        "pcm_s16le",
                        str(base_path),
                    ],
                    "Не удалось объединить WAV-фрагменты",
                )

            self._finish_audio(
                base_path,
                wav_path,
                mp3_path,
                request.speed,
                lambda stage, value: update(total, total, stage, value),
            )
            return self._metadata(file_id, wav_path, mp3_path)
        except Exception:
            for path in (base_path, wav_path, mp3_path):
                path.unlink(missing_ok=True)
            raise
