from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    data_dir: Path = Path("data")
    model_id: str = "v5_5_ru"
    model_url: str = "https://models.silero.ai/models/tts/ru/v5_5_ru.pt"
    max_text_length: int = 5_000
    max_chunk_length: int = 900
    queue_size: int = 8
    retention_hours: int = 24
    torch_threads: int = 4
    load_model: bool = True
    ffmpeg_binary: str = "ffmpeg"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def model_path(self) -> Path:
        return self.model_dir / f"{self.model_id}.pt"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.getenv("TTS_DATA_DIR", "data")),
            model_id=os.getenv("TTS_MODEL_ID", "v5_5_ru"),
            model_url=os.getenv(
                "TTS_MODEL_URL",
                "https://models.silero.ai/models/tts/ru/v5_5_ru.pt",
            ),
            max_text_length=int(os.getenv("TTS_MAX_TEXT_LENGTH", "5000")),
            max_chunk_length=int(os.getenv("TTS_MAX_CHUNK_LENGTH", "900")),
            queue_size=int(os.getenv("TTS_QUEUE_SIZE", "8")),
            retention_hours=int(os.getenv("TTS_RETENTION_HOURS", "24")),
            torch_threads=int(os.getenv("TTS_TORCH_THREADS", "4")),
            load_model=_as_bool(os.getenv("TTS_LOAD_MODEL", "true")),
            ffmpeg_binary=os.getenv("TTS_FFMPEG_BINARY", "ffmpeg"),
        )
