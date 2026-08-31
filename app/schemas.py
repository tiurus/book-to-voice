from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, field_validator

VOICES = ("aidar", "baya", "kseniya", "xenia", "eugene")
SAMPLE_RATES = (8_000, 24_000, 48_000)


class Speed(str, Enum):
    slow = "slow"
    normal = "normal"
    fast = "fast"


class SpeechRequest(BaseModel):
    text: str
    voice: Literal["aidar", "baya", "kseniya", "xenia", "eugene"] = "xenia"
    sample_rate: Literal[8000, 24000, 48000] = 48_000
    speed: Speed = Speed.normal
    auto_stress: bool = True
    ssml: bool = False

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Введите текст для озвучки")
        if len(value) > 5_000:
            raise ValueError("Текст не должен быть длиннее 5 000 символов")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Текст содержит недопустимые управляющие символы")
        return value


class JobState(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class AudioInfo(BaseModel):
    url: str
    download_url: str
    format: Literal["wav", "mp3"]
    size_bytes: int
    duration_seconds: float


class JobResponse(BaseModel):
    job_id: str
    state: JobState
    position: int | None = None
    file_id: str | None = None
    audio: dict[str, AudioInfo] | None = None
    error_code: str | None = None
    error: str | None = None
