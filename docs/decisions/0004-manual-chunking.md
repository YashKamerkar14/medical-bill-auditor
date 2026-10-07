# 0004: Manual chunking and indexing

- Status: accepted
- Date: 2026-10-06
- Milestone: 0

## Context

The retrieval corpus is the 2026 NCCI Policy Manual, Chapter I (General Correct Coding Policies, 36 pages) and Chapter XI (Medicine and E&M, 40 pages). Retrieved passages are later quoted in reports and the verify step string-matches each quote against its source, so chunks must be traceable to a section and must not alter the text.

Observed in the PDFs (pdfplumber 0.11.10): a cover page, a table-of-contents page, then the body; every page ends with `Revision Date (Medicare): 1/1/2026` and a page label (`I-3`, `XI-3`); headings are lettered A–W, sometimes without a space (`A.Introduction`), and long titles wrap; the body contains many `A.`/`1.`/`a.` list items; quotes are curly (U+201C/U+201D).

## Decision

- **Section boundaries come from the table of contents**, matched in order against the body. A free heading regex would turn list items into sections. Any TOC entry whose heading is not found stops the build.
- **Citeable ids**: section `I.E`, chunk `I.E#6`; page labels are the manual's own (`I-14`).
- **Chunks never cross sections.** Inside a section, blocks start at numbered rules, lettered sub-items and bullets; blocks are packed up to 1,500 characters (bge-small truncates at 512 tokens, roughly 2,000 characters). Oversized blocks are split at sentence ends first, so an intro sentence ("Some examples follow:") stays with its list. No overlap between chunks.
- **Lossless**: concatenating a section's chunks reproduces the section text exactly (whitespace aside). This is a unit test, and it caught a bug that dropped closing quotes.
- **Contextual chunk headers**: the embedded text is prefixed with the chapter and section title; the stored and quoted text is verbatim.
- **Keyword index**: SQLite FTS5, external content over `manual_chunks`, `porter unicode61` tokenizer, BM25 with section title weighted 2x. User text is turned into quoted terms joined with OR, so FTS5 operators can never be injected.
- **Vector index**: Chroma persistent collection, cosine space, vectors passed in explicitly (no Chroma default embedding function), anonymous telemetry off. Embeddings: `BAAI/bge-small-en-v1.5` via fastembed (384 dimensions, local ONNX). Queries get the bge instruction prefix; passages do not. The model name is stored on the collection and a query with a different model is refused.

Result on the 2026 files: 46 sections (2 empty "Reserved"), 188 chunks, 210 to 1,499 characters (median about 1,200).

## Trade-offs and alternatives

- Fixed-size token windows would be simpler but cut rules in half and make section citations unreliable.
- No overlap keeps chunks lossless and citations clean; the contextual header supplies the context overlap would. Revisit if Milestone 4 recall@5 shows boundary misses.
- The query prefix is the model card's recommendation, not yet measured here; it is a Milestone 4 variable.
- Minimum-relevance "insufficient evidence" thresholds belong to the corrective-RAG step (Milestone 3) and are calibrated in Milestone 4, not set here.
