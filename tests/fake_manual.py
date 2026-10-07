"""A fake manual chapter (chapter IX) that copies the real PDFs' quirks. No CMS text."""

FOOTER = "Revision Date (Medicare): 1/1/2026"

COVER = ["CHAPTER IX", "FAKE CHAPTER FOR TESTS", FOOTER, "IX-1"]
TOC = [
    "Table of Contents",
    "Chapter IX ........................................ IX-3",
    "Fake Chapter Title ................................ IX-3",
    "A. Introduction .................................... IX-3",
    "B. Reserved for future use ......................... IX-3",
    "C.Long Title That Wraps Onto A Second",
    "Line In The Table…………..  ......................... IX-3",
    "D. Reserved for future use ......................... IX-4",
    FOOTER,
    "IX-2",
]
PAGE_3 = [
    "Chapter IX",                      # running header before the first section
    "Fake Chapter Title",
    "A.Introduction",                  # no space after the letter, as in the real PDFs
    "The principles of correct coding apply to these fake codes. Providers shall not",
    "unbundle services. Some examples follow:",
    "1. First rule about ZZ001 and a non-",
    "programmable pump.",
    "2. Second rule about ZZ002.",
    "B. Reserved for future use",
    "C. Long Title That Wraps Onto A",
    "Second Line In The Table",        # heading continues; must not leak into the body
    "Body of section C starts here.",
    "A. A lettered list item inside section C, which is not a section heading.",
    FOOTER,
    "IX-3",
]
PAGE_4 = [
    "More of section C on the next page.",
    "D. Reserved for future use",
    FOOTER,
    "IX-4",
]
FAKE_CHAPTER = [COVER, TOC, PAGE_3, PAGE_4]
