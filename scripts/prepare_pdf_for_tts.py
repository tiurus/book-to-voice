#!/usr/bin/env python3
"""Extract a prose PDF into a cleaned text and sentence-safe TTS chunks."""

from __future__ import annotations

import argparse
import re
import shutil
import unicodedata
from pathlib import Path

import pdfplumber

SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?…])\s+|(?<=[.!?…]["»”])\s+|(?<=[.!?…]["»”][)])\s+')
TERMINAL_PUNCTUATION = re.compile(r"[.!?…:;»”)]$")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\u00a0", " ").replace("\u00ad", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return value.strip()


def is_heading(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and len(value) <= 100 and all(char.isupper() for char in letters)


def extract_paragraphs(page) -> list[str]:
    lines = page.extract_text_lines(x_tolerance=2, y_tolerance=3)
    paragraphs: list[str] = []
    current = ""
    previous_bottom: float | None = None

    for item in lines:
        line = normalize(item["text"])
        if not line:
            continue
        vertical_gap = None if previous_bottom is None else item["top"] - previous_bottom
        starts_paragraph = vertical_gap is not None and vertical_gap > 9
        if starts_paragraph and current:
            paragraphs.append(normalize(current))
            current = line
        else:
            current = f"{current} {line}".strip()
        previous_bottom = item["bottom"]

    if current:
        paragraphs.append(normalize(current))
    return paragraphs


def merge_page(paragraphs: list[str], page_paragraphs: list[str]) -> None:
    if not page_paragraphs:
        return
    first = page_paragraphs[0]
    if paragraphs and not TERMINAL_PUNCTUATION.search(paragraphs[-1]) and not is_heading(first):
        paragraphs[-1] = normalize(f"{paragraphs[-1]} {first}")
        page_paragraphs = page_paragraphs[1:]
    paragraphs.extend(page_paragraphs)


def split_oversized(value: str, limit: int) -> list[str]:
    result: list[str] = []
    remainder = value.strip()
    while len(remainder) > limit:
        boundary = max(
            remainder.rfind(";", 0, limit + 1),
            remainder.rfind(",", 0, limit + 1),
            remainder.rfind(" ", 0, limit + 1),
        )
        if boundary < limit // 2:
            boundary = limit
        result.append(remainder[:boundary].strip())
        remainder = remainder[boundary:].strip()
    if remainder:
        result.append(remainder)
    return result


def split_for_tts(paragraphs: list[str], limit: int) -> list[str]:
    units: list[tuple[str, bool]] = []
    for paragraph in paragraphs:
        if is_heading(paragraph):
            units.append((paragraph, True))
            continue
        sentences = [part.strip() for part in SENTENCE_BOUNDARY.split(paragraph) if part.strip()]
        first_unit = True
        for sentence in sentences:
            for piece in split_oversized(sentence, limit):
                units.append((piece, first_unit))
                first_unit = False

    chunks: list[str] = []
    current = ""
    for unit, starts_paragraph in units:
        separator = "\n\n" if starts_paragraph else " "
        candidate = f"{current}{separator if current else ''}{unit}".strip()
        if current and len(candidate) > limit:
            chunks.append(current.strip())
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start-page", type=int, default=1, help="First 1-based page")
    parser.add_argument("--end-page", type=int, help="Last 1-based page")
    parser.add_argument("--title", required=True)
    parser.add_argument("--author", required=True)
    parser.add_argument("--chunk-limit", type=int, default=4800)
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"PDF not found: {args.input}")
    if args.chunk_limit > 5000:
        raise SystemExit("Chunk limit must not exceed the service limit of 5000")

    paragraphs = [args.title, args.author]
    with pdfplumber.open(args.input) as pdf:
        end_page = args.end_page or len(pdf.pages)
        if not 1 <= args.start_page <= end_page <= len(pdf.pages):
            raise SystemExit("Invalid page range")
        for page in pdf.pages[args.start_page - 1 : end_page]:
            merge_page(paragraphs, extract_paragraphs(page))

    paragraphs = [normalize(paragraph) for paragraph in paragraphs if normalize(paragraph)]
    full_text = "\n\n".join(paragraphs).strip() + "\n"
    chunks = split_for_tts(paragraphs, args.chunk_limit)
    source_linear = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()
    chunks_linear = re.sub(r"\s+", " ", " ".join(chunks)).strip()
    if source_linear != chunks_linear:
        raise RuntimeError("Chunking changed the extracted text")

    if args.output.exists():
        shutil.rmtree(args.output)
    chunks_dir = args.output / "chunks"
    chunks_dir.mkdir(parents=True)

    (args.output / "full_text_cleaned.txt").write_text(full_text, encoding="utf-8")
    for index, chunk in enumerate(chunks, 1):
        (chunks_dir / f"fragment-{index:03d}.txt").write_text(chunk + "\n", encoding="utf-8")

    readme = (
        f"{args.title}\n{args.author}\n\n"
        f"Источник: {args.input.name}\n"
        f"Обработанные страницы PDF: {args.start_page}-{args.end_page or 'последняя'}\n"
        f"Фрагментов: {len(chunks)}\n"
        f"Ограничение фрагмента: {args.chunk_limit} символов\n\n"
        "Файлы в папке chunks нужно озвучивать строго по номеру. "
        "Служебных заголовков внутри файлов нет, поэтому они не попадут в аудио.\n"
    )
    (args.output / "README.txt").write_text(readme, encoding="utf-8")
    archive = shutil.make_archive(str(args.output), "zip", root_dir=args.output)

    print(f"pages={args.start_page}-{args.end_page or 'last'}")
    print(f"paragraphs={len(paragraphs)}")
    print(f"characters={len(full_text)}")
    print(f"chunks={len(chunks)}")
    print(f"min_chunk={min(map(len, chunks))} max_chunk={max(map(len, chunks))}")
    print(f"archive={archive}")


if __name__ == "__main__":
    main()
