"""Chunker tests on a fake chapter that copies the real manual's quirks."""

import re

import pytest

from bill_auditor.ingest.chunk_manual import (
    Block,
    ManualFormatError,
    Section,
    _split_long,
    chunk_section,
    parse_chapter,
    parse_toc,
    strip_footer,
)
from fake_manual import COVER, FAKE_CHAPTER, PAGE_3, PAGE_4, TOC


@pytest.fixture
def parsed():
    return parse_chapter(FAKE_CHAPTER)


def by_id(sections):
    return {s.section_id: s for s in sections}


def test_strip_footer_returns_lines_and_page_label():
    lines, label = strip_footer(PAGE_4)
    assert label == "IX-4"
    assert lines == ["More of section C on the next page.", "D. Reserved for future use"]


def test_missing_footer_is_an_error():
    with pytest.raises(ManualFormatError, match="footer"):
        strip_footer(["Some text", "More text"])


def test_toc_gives_chapter_and_sections_including_wrapped_title():
    chapter, title, entries = parse_toc(strip_footer(TOC)[0])
    assert (chapter, title) == ("IX", "Fake Chapter Title")
    assert [e.letter for e in entries] == ["A", "B", "C", "D"]
    assert entries[2].title == "Long Title That Wraps Onto A Second Line In The Table"


def test_one_section_per_toc_entry_with_citeable_ids(parsed):
    sections, _ = parsed
    assert [s.section_id for s in sections] == ["IX.A", "IX.B", "IX.C", "IX.D"]
    assert by_id(sections)["IX.D"].title == "Reserved for future use"


def test_running_header_before_first_section_is_dropped(parsed):
    sections, _ = parsed
    assert by_id(sections)["IX.A"].text.startswith("The principles of correct coding")


def test_wrapped_heading_does_not_leak_into_body(parsed):
    sections, _ = parsed
    assert by_id(sections)["IX.C"].text.startswith("Body of section C starts here.")


def test_lettered_list_item_is_not_mistaken_for_a_section(parsed):
    sections, _ = parsed
    assert "A lettered list item inside section C" in by_id(sections)["IX.C"].text


def test_section_page_ranges_use_manual_page_labels(parsed):
    sections, _ = parsed
    section_c = by_id(sections)["IX.C"]
    assert (section_c.page_start, section_c.page_end) == ("IX-3", "IX-4")


def test_numbered_rules_start_new_blocks_and_hyphens_are_kept(parsed):
    sections, _ = parsed
    text = by_id(sections)["IX.A"].text
    assert "\n\n1. First rule about ZZ001 and a non-programmable pump." in text
    assert "\n\n2. Second rule" in text


def test_empty_sections_produce_no_chunks(parsed):
    _, chunks = parsed
    assert not [c for c in chunks if c.section_id in ("IX.B", "IX.D")]


def test_missing_heading_is_an_error():
    broken_page = [line for line in PAGE_3 if not line.startswith("C. Long Title")]
    with pytest.raises(ManualFormatError, match="IX.C"):
        parse_chapter([COVER, TOC, broken_page, PAGE_4])


def test_chunks_are_lossless_and_never_cross_sections(parsed):
    sections, chunks = parsed
    for section in sections:
        own = [c for c in chunks if c.section_id == section.section_id]
        assert re.sub(r"\s+", "", "".join(c.text for c in own)) == re.sub(r"\s+", "", section.text)


def _section(text="") -> Section:
    return Section("IX.Z", "IX", "Fake", "Fake section", "IX-5", "IX-6", text)


def test_chunks_respect_the_size_limit_and_are_numbered():
    blocks = [Block(f"{n}. " + "word " * 30, "IX-5", "IX-6") for n in range(1, 11)]
    chunks = chunk_section(_section(), blocks, limit=400)
    assert all(len(c.text) <= 400 for c in chunks)
    assert [c.chunk_id for c in chunks] == [f"IX.Z#{n}" for n in range(1, len(chunks) + 1)]
    assert len(chunks) > 1


def test_intro_sentence_stays_with_the_list_it_introduces():
    long_paragraph = "This sentence is filler text. " * 12 + "Some examples follow:"
    blocks = [Block(long_paragraph, "IX-5", "IX-5"), Block("• First example.", "IX-5", "IX-5")]
    chunks = chunk_section(_section(), blocks, limit=200)
    assert chunks[-1].text.endswith("Some examples follow:\n\n• First example.")


def test_chunk_pages_span_their_blocks():
    blocks = [Block("1. First.", "IX-5", "IX-5"), Block("2. Second.", "IX-5", "IX-6")]
    (chunk,) = chunk_section(_section(), blocks, limit=1000)
    assert (chunk.page_start, chunk.page_end) == ("IX-5", "IX-6")


def test_sentence_split_keeps_closing_quotes():
    text = "The code is a “separate procedure.” It may be reported. " * 5
    pieces = _split_long(text.strip(), limit=60)
    assert all(len(p) <= 60 for p in pieces)
    assert "".join(pieces).count("”") == 5
