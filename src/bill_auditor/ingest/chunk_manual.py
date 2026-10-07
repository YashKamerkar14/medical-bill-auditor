"""Turn NCCI Policy Manual chapters into sections and retrieval chunks.

Design (see docs/decisions/0004-manual-chunking.md):
- Section boundaries come from the chapter's own table of contents, matched in
  order against the body text. Body text is full of "A." and "11." list items,
  so a free-standing heading regex would invent sections.
- Section ids are citeable: "I.E" is chapter I, section E.
- A chunk never crosses a section. Inside a section the text is split into
  blocks at numbered rules and bullets, and blocks are packed into chunks of
  at most MAX_CHUNK_CHARS. A block that is too long is split at sentences.
- Text is kept verbatim (curly quotes included) so that quotes can later be
  verified against the source.

File facts checked on the 2026 Chapter I and XI PDFs:
- Page 1 is a cover, then a "Table of Contents" page, then the body.
- Every page ends with "Revision Date (Medicare): 1/1/2026" and a page label
  such as "I-3" or "XI-3".
- Headings sometimes lose the space after the letter ("A.Introduction"), and
  long titles wrap onto a second line.
"""

import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHUNK_CHARS = 1500
BLOCK_SEPARATOR = "\n\n"

_FOOTER_REVISION = re.compile(r"^Revision Date \(Medicare\):")
_PAGE_LABEL = re.compile(r"^([IVXL]+)-(\d+)$")
_TOC_ENTRY_START = re.compile(r"^([A-Z])\.\s*(.*)$")
_TOC_PAGE_REF = re.compile(r"[IVXL]+-\d+\s*$")
_TOC_LEADER = re.compile(r"[.…]{2,}.*$")  # dot leaders and everything after them
# A block starts at "1. ", "c.Modifier" (lettered sub-item; a capital must follow so
# "e.g." is not one), a bullet, or "(a) ".
_BLOCK_START = re.compile(r"^(\d+\.\s|[a-z]\.\s?(?=[A-Z“\"])|•\s|\([a-z0-9]+\)\s)")
# Whitespace after ".", "!" or "?" (optionally followed by a closing quote) and
# before a likely sentence start. Only the whitespace is consumed, so splitting
# never drops a character.
_SENTENCE_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][”\"]))\s+(?=[A-Z0-9“\"(])")


class ManualFormatError(ValueError):
    """The PDF text does not have the structure this chunker expects."""


@dataclass(frozen=True)
class Line:
    text: str
    page: str  # the manual's own page label, e.g. "I-25"


@dataclass(frozen=True)
class Block:
    text: str
    page_start: str
    page_end: str


@dataclass(frozen=True)
class TocEntry:
    letter: str
    title: str


@dataclass(frozen=True)
class Section:
    section_id: str
    chapter: str
    chapter_title: str
    title: str
    page_start: str
    page_end: str
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    section_id: str
    ordinal: int
    text: str
    page_start: str
    page_end: str


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_footer(page_lines: list[str]) -> tuple[list[str], str]:
    """Remove the revision-date and page-label footer; return (lines, page label)."""
    lines = [line.rstrip() for line in page_lines if line.strip()]
    if len(lines) < 2 or not _PAGE_LABEL.match(lines[-1]) or not _FOOTER_REVISION.match(lines[-2]):
        raise ManualFormatError(f"Page footer not found; last lines were {lines[-2:]}")
    return lines[:-2], lines[-1]


def parse_toc(toc_lines: list[str]) -> tuple[str, str, list[TocEntry]]:
    """Return (chapter numeral, chapter title, lettered sections) from the TOC page.

    The first lines are "Chapter XI ... XI-3" and the chapter title; then one
    entry per lettered section. An entry whose title wraps has its page
    reference on the next line, so lines are joined until one is found.
    """
    if not toc_lines or toc_lines[0].strip() != "Table of Contents":
        raise ManualFormatError("Table of contents page not recognised")
    chapter_match = re.match(r"^Chapter ([IVXL]+)\b", toc_lines[1])
    if not chapter_match:
        raise ManualFormatError(f"Chapter line not recognised: {toc_lines[1]!r}")
    chapter = chapter_match.group(1)
    chapter_title = _TOC_LEADER.sub("", toc_lines[2]).strip()

    entries: list[TocEntry] = []
    pending = ""
    for line in toc_lines[3:]:
        pending = f"{pending} {line}".strip() if pending else line.strip()
        if not _TOC_ENTRY_START.match(pending):
            raise ManualFormatError(f"Unexpected table of contents line: {line!r}")
        if not _TOC_PAGE_REF.search(pending):
            continue  # title wraps onto the next line
        letter, rest = _TOC_ENTRY_START.match(pending).groups()
        entries.append(TocEntry(letter=letter, title=_TOC_LEADER.sub("", rest).strip()))
        pending = ""

    if pending:
        raise ManualFormatError(f"Unfinished table of contents entry: {pending!r}")
    letters = [e.letter for e in entries]
    if letters != sorted(letters) or len(set(letters)) != len(letters):
        raise ManualFormatError(f"Section letters out of order: {letters}")
    return chapter, chapter_title, entries


def _heading_lines(lines: list[Line], start: int, entry: TocEntry) -> int | None:
    """If a heading for `entry` begins at lines[start], return how many lines it spans."""
    match = _TOC_ENTRY_START.match(lines[start].text)
    if not match or match.group(1) != entry.letter:
        return None
    title = _normalise(entry.title)
    seen = _normalise(match.group(2))
    if not seen or not title.startswith(seen[: min(len(seen), 25)]):
        return None
    span = 1
    # A long title can wrap: keep consuming lines while they continue the title.
    while len(seen) < len(title) and start + span < len(lines):
        candidate = _normalise(f"{seen} {lines[start + span].text}")
        if not title.startswith(candidate):
            break
        seen = candidate
        span += 1
    return span


def split_sections(
    body: list[Line], chapter: str, chapter_title: str, toc: list[TocEntry]
) -> list[tuple[Section, list[Block]]]:
    """Cut the body into one Section (with its blocks) per TOC entry, finding headings in order."""
    starts: list[tuple[int, int]] = []  # (line index of heading, lines the heading spans)
    position = 0
    for entry in toc:
        for index in range(position, len(body)):
            span = _heading_lines(body, index, entry)
            if span:
                starts.append((index, span))
                position = index + span
                break
        else:
            raise ManualFormatError(f"Heading for section {chapter}.{entry.letter} ({entry.title}) not found")

    sections = []
    for number, (entry, (index, span)) in enumerate(zip(toc, starts)):
        end = starts[number + 1][0] if number + 1 < len(starts) else len(body)
        content = body[index + span : end]
        blocks = make_blocks(content)
        section = Section(
            section_id=f"{chapter}.{entry.letter}",
            chapter=chapter,
            chapter_title=chapter_title,
            title=entry.title,
            page_start=body[index].page,
            page_end=body[end - 1].page,
            text=BLOCK_SEPARATOR.join(block.text for block in blocks),
        )
        sections.append((section, blocks))
    return sections


def _join(lines: list[str]) -> str:
    """Join wrapped lines. A line ending in '-' is joined without a space, keeping the hyphen."""
    text = ""
    for line in lines:
        line = line.strip()
        if not text:
            text = line
        elif text.endswith("-"):
            text += line
        else:
            text += " " + line
    return text


def make_blocks(content: list[Line]) -> list[Block]:
    """Group lines into blocks; a new block starts at a numbered rule or a bullet."""
    groups: list[list[Line]] = []
    for line in content:
        if not groups or _BLOCK_START.match(line.text):
            groups.append([])
        groups[-1].append(line)
    return [Block(_join([l.text for l in group]), group[0].page, group[-1].page) for group in groups]


def _split_long(text: str, limit: int) -> list[str]:
    """Split text at sentence ends (or, failing that, at spaces) into pieces <= limit."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > limit:  # a single very long sentence
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > limit and current:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_section(section: Section, blocks: list[Block], limit: int = MAX_CHUNK_CHARS) -> list[Chunk]:
    """Pack a section's blocks into chunks of at most `limit` characters."""
    # Oversized blocks are split into sentence pieces first, so the last piece
    # can still share a chunk with the blocks after it (e.g. "Some examples
    # follow:" stays with the list it introduces).
    pieces = [
        Block(piece, block.page_start, block.page_end)
        for block in blocks
        for piece in (_split_long(block.text, limit) if len(block.text) > limit else [block.text])
    ]

    packed: list[Block] = []
    current: list[Block] = []

    def flush() -> None:
        if current:
            text = BLOCK_SEPARATOR.join(block.text for block in current)
            packed.append(Block(text, current[0].page_start, current[-1].page_end))
            current.clear()

    for block in pieces:
        if current and len(BLOCK_SEPARATOR.join(b.text for b in [*current, block])) > limit:
            flush()
        current.append(block)
    flush()

    return [
        Chunk(
            chunk_id=f"{section.section_id}#{ordinal}",
            section_id=section.section_id,
            ordinal=ordinal,
            text=block.text,
            page_start=block.page_start,
            page_end=block.page_end,
        )
        for ordinal, block in enumerate(packed, start=1)
    ]


def parse_chapter(pages: list[list[str]], limit: int = MAX_CHUNK_CHARS) -> tuple[list[Section], list[Chunk]]:
    """Sections and chunks for one chapter, from the text lines of each page."""
    toc_index = next(
        (i for i, page in enumerate(pages) if page and page[0].strip() == "Table of Contents"), None
    )
    if toc_index is None:
        raise ManualFormatError("No table of contents page found")
    toc_lines, _ = strip_footer(pages[toc_index])
    chapter, chapter_title, toc = parse_toc(toc_lines)

    body: list[Line] = []
    for page in pages[toc_index + 1 :]:
        lines, label = strip_footer(page)
        body.extend(Line(text=line, page=label) for line in lines)

    sections: list[Section] = []
    chunks: list[Chunk] = []
    for section, blocks in split_sections(body, chapter, chapter_title, toc):
        sections.append(section)
        chunks.extend(chunk_section(section, blocks, limit))
    return sections, chunks


def read_pdf_pages(path: Path) -> list[list[str]]:
    """Text lines of every page, as extracted by pdfplumber."""
    import pdfplumber  # imported here so the pure functions above need no PDF library

    with pdfplumber.open(path) as pdf:
        return [(page.extract_text() or "").splitlines() for page in pdf.pages]
