from __future__ import annotations

import re
import xml.etree.ElementTree as ET

SENTENCE_END = re.compile(r"(?<=[.!?…])(?:[\"»)]*)\s+")
ALLOWED_SSML_TAGS = {"speak", "p", "s", "break", "prosody"}
ALLOWED_SSML_ATTRIBUTES = {
    "break": {"time", "strength"},
    "prosody": {"rate", "pitch"},
}


class InvalidSSML(ValueError):
    pass


def validate_ssml(value: str) -> None:
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise InvalidSSML(f"Некорректный SSML: {exc}") from exc
    if root.tag != "speak":
        raise InvalidSSML("SSML должен иметь корневой тег <speak>")
    for element in root.iter():
        if element.tag not in ALLOWED_SSML_TAGS:
            raise InvalidSSML(f"SSML-тег <{element.tag}> не поддерживается")
        allowed = ALLOWED_SSML_ATTRIBUTES.get(element.tag, set())
        unknown = set(element.attrib) - allowed
        if unknown:
            raise InvalidSSML(
                f"Атрибут {sorted(unknown)[0]!r} тега <{element.tag}> не поддерживается"
            )


def _split_oversized(value: str, limit: int) -> list[str]:
    pieces: list[str] = []
    remainder = value.strip()
    while len(remainder) > limit:
        boundary = max(
            remainder.rfind(";", 0, limit + 1),
            remainder.rfind(",", 0, limit + 1),
            remainder.rfind(" ", 0, limit + 1),
        )
        if boundary < limit // 2:
            boundary = limit
        pieces.append(remainder[:boundary].strip())
        remainder = remainder[boundary:].strip()
    if remainder:
        pieces.append(remainder)
    return pieces


def split_text(text: str, limit: int = 900) -> list[str]:
    """Split on paragraphs/sentences first, then on punctuation or words."""
    normalized = re.sub(r"[ \t]+", " ", text.strip())
    paragraphs = [part.strip() for part in re.split(r"\n+", normalized) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(part.strip() for part in SENTENCE_END.split(paragraph) if part.strip())

    chunks: list[str] = []
    current = ""
    for unit in units:
        for piece in _split_oversized(unit, limit):
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks
