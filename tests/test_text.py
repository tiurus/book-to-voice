import pytest

from app.text import InvalidSSML, split_text, validate_ssml


def test_split_text_preserves_content_and_limits_chunks() -> None:
    source = (
        "Это первое предложение. Это второе предложение с буквой ё! "
        "А это очень длинная часть, " + "слово " * 80 + "конец."
    )
    chunks = split_text(source, limit=120)

    assert len(chunks) > 2
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert "ё" in " ".join(chunks)
    assert "первое предложение." in chunks[0]


def test_valid_ssml() -> None:
    validate_ssml('<speak>Привет. <break time="300ms"/> Мир!</speak>')


@pytest.mark.parametrize(
    "value",
    [
        "Просто текст",
        "<speak><audio src='outside.mp3'/></speak>",
        "<speak><break onclick='bad'/></speak>",
    ],
)
def test_invalid_ssml(value: str) -> None:
    with pytest.raises(InvalidSSML):
        validate_ssml(value)
