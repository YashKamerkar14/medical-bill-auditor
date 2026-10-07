"""Quote verification: a quote must appear verbatim in the manual section it cites.

"Verbatim" after normalising only differences that do not change meaning:
Unicode compatibility forms (PDF ligatures such as "fi"), curly vs straight
quote marks, dash variants, hyphens (PDF line breaks split words with them),
letter case and whitespace. An ellipsis splits a quote into pieces that must
all appear, in order. Quotes shorter than MIN_QUOTE_CHARS are rejected so a
trivial fragment can never count as evidence.
"""

import re
import sqlite3
import unicodedata

MIN_QUOTE_CHARS = 20
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‐": "-", "‑": "-"})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = text.replace("-", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip("\"'").strip().casefold()


def quote_in_text(quote: str, source: str) -> bool:
    pieces = [normalise(p) for p in re.split(r"\.\.\.|…", quote)]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < MIN_QUOTE_CHARS:
        return False
    haystack = normalise(source)
    position = 0
    for piece in pieces:
        found = haystack.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True


def section_for_chunk(conn: sqlite3.Connection, chunk_id: str) -> tuple[str, str] | None:
    """(section_id, full section text) for a chunk id, or None if the chunk does not exist."""
    row = conn.execute(
        """SELECT s.section_id, s.text FROM manual_chunks c JOIN manual_sections s USING (section_id)
           WHERE c.chunk_id = ?""", (chunk_id,)).fetchone()
    return (row["section_id"], row["text"]) if row else None


def verify_quote(conn: sqlite3.Connection, chunk_id: str, quote: str) -> str | None:
    """None if the quote is verified against its chunk's section, else the reason it is not."""
    found = section_for_chunk(conn, chunk_id)
    if found is None:
        return f"chunk {chunk_id!r} does not exist"
    if not quote_in_text(quote, found[1]):
        return f"quote not found verbatim in section {found[0]}"
    return None
