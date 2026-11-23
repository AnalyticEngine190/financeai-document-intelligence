
"""Clean and chunk financial reports with traceable metadata."""

import re
import unicodedata


def clean_text(text: str) -> str:
    """Normalize whitespace while preserving line breaks."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")

    lines = []

    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


def chunk_report(
    report: dict,
    chunk_size: int = 1200,
    overlap: int = 200,
) -> list[dict]:
    """Split PDF pages into overlapping, source-traceable chunks."""

    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("Invalid chunk size or overlap")

    chunks = []

    for page in report["pages"]:
        text = clean_text(page["text"])

        if not text:
            continue

        # Locate whole words without destroying line breaks.
        words = list(re.finditer(r"\S+", text))
        start_idx = 0
        chunk_number = 0

        while start_idx < len(words):
            end_idx = start_idx + 1

            # Add words while the chunk stays within our target.
            while end_idx < len(words):
                candidate_length = (
                    words[end_idx].end()
                    - words[start_idx].start()
                )

                if candidate_length > chunk_size:
                    break

                end_idx += 1

            start_char = words[start_idx].start()
            end_char = words[end_idx - 1].end()

            chunk_text = text[start_char:end_char]

            chunks.append({
                "id": (
                    f'{report["sha256"]}'
                    f'_p{page["page_number"]}'
                    f'_c{chunk_number}'
                ),
                "document_id": report["sha256"],
                "filename": report["filename"],
                "page_number": page["page_number"],
                "chunk_index": chunk_number,
                "start_char": start_char,
                "end_char": end_char,
                "text": chunk_text,
            })

            if end_idx == len(words):
                break

            # Reuse some words for context in the next chunk.
            next_idx = end_idx - 1

            while next_idx > start_idx:
                previous_idx = next_idx - 1

                overlap_length = (
                    end_char - words[previous_idx].start()
                )

                if overlap_length > overlap:
                    break

                next_idx = previous_idx

            # Always advance to prevent infinite loops.
            start_idx = max(start_idx + 1, next_idx)
            chunk_number += 1

    return chunks
