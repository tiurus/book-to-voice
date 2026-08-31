from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import AudioStore, StoredAudio


class FakeSynthesizer:
    def __init__(self, store: AudioStore, delay: float = 0.0) -> None:
        self.store = store
        self.ready = True
        self.load_error = None
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def load(self) -> None:
        return None

    def synthesize(self, request) -> StoredAudio:
        from uuid import uuid4

        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            file_id = uuid4().hex
            wav_path = self.store.path(file_id, "wav")
            mp3_path = self.store.path(file_id, "mp3")
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(request.sample_rate)
                output.writeframes(b"\0\0" * (request.sample_rate // 20))
            mp3_path.write_bytes(b"ID3" + b"\0" * 128)
            item = StoredAudio(
                file_id=file_id,
                duration_seconds=0.05,
                wav_size=wav_path.stat().st_size,
                mp3_size=mp3_path.stat().st_size,
                created_at=time.time(),
            )
            self.store.save_metadata(item)
            return item
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def app_factory(tmp_path: Path):
    clients: list[TestClient] = []

    def factory(delay: float = 0.0):
        settings = Settings(data_dir=tmp_path, load_model=False, queue_size=4)
        store = AudioStore(settings.audio_dir)
        synthesizer = FakeSynthesizer(store, delay)
        client = TestClient(create_app(settings, synthesizer))
        clients.append(client)
        return client, synthesizer

    yield factory
    for client in clients:
        client.close()
