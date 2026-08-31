from __future__ import annotations

import shutil
import wave

import pytest

from app.config import Settings
from app.schemas import FileSpeechRequest
from app.storage import AudioStore
from app.synthesizer import SileroSynthesizer


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_document_synthesis_streams_fragments_and_converts(tmp_path) -> None:
    import torch

    class FakeModel:
        def apply_tts(self, **kwargs):
            return torch.linspace(-0.1, 0.1, kwargs["sample_rate"] // 50)

    settings = Settings(data_dir=tmp_path, max_chunk_length=35, load_model=False)
    store = AudioStore(settings.audio_dir)
    engine = SileroSynthesizer(settings, store)
    engine.model = FakeModel()
    updates: list[tuple[int, int, str, int]] = []
    request = FileSpeechRequest(
        text="Первое довольно длинное предложение. Второе длинное предложение. Третье.",
        filename="book.txt",
        sample_rate=24_000,
    )

    item = engine.synthesize_document(request, lambda *values: updates.append(values))

    with wave.open(str(store.path(item.file_id, "wav")), "rb") as output:
        assert output.getframerate() == 24_000
        assert output.getnframes() > 0
    assert store.path(item.file_id, "mp3").stat().st_size > 0
    assert updates[0][2:] == ("synthesizing", 1)
    assert any(stage == "merging" for _, _, stage, _ in updates)
    assert updates[-1][2:] == ("converting", 95)
    assert not list(store.directory.glob("*.tmp"))
