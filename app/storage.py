from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID


@dataclass(slots=True)
class StoredAudio:
    file_id: str
    duration_seconds: float
    wav_size: int
    mp3_size: int
    created_at: float


class AudioStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_id(file_id: str) -> str:
        try:
            return UUID(file_id).hex
        except ValueError as exc:
            raise FileNotFoundError(file_id) from exc

    def path(self, file_id: str, file_format: str) -> Path:
        normalized = self.validate_id(file_id)
        if file_format not in {"wav", "mp3"}:
            raise FileNotFoundError(file_format)
        return self.directory / f"{normalized}.{file_format}"

    def metadata_path(self, file_id: str) -> Path:
        return self.directory / f"{self.validate_id(file_id)}.json"

    def save_metadata(self, item: StoredAudio) -> None:
        path = self.metadata_path(item.file_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(item)), encoding="utf-8")
        temporary.replace(path)

    def get(self, file_id: str) -> StoredAudio:
        path = self.metadata_path(file_id)
        try:
            return StoredAudio(**json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError, TypeError) as exc:
            raise FileNotFoundError(file_id) from exc

    def delete(self, file_id: str) -> bool:
        removed = False
        for suffix in ("wav", "mp3", "json"):
            path = self.directory / f"{self.validate_id(file_id)}.{suffix}"
            if path.exists():
                path.unlink()
                removed = True
        return removed

    def cleanup(self, retention_hours: int) -> int:
        cutoff = time.time() - retention_hours * 3600
        deleted = 0
        for metadata in self.directory.glob("*.json"):
            try:
                item = StoredAudio(**json.loads(metadata.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError):
                continue
            if item.created_at < cutoff and self.delete(item.file_id):
                deleted += 1
        for temporary in self.directory.glob("*.tmp"):
            if temporary.stat().st_mtime < cutoff:
                if temporary.is_dir():
                    shutil.rmtree(temporary)
                else:
                    temporary.unlink(missing_ok=True)
        return deleted
