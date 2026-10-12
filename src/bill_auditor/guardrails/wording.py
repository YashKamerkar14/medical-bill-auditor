"""Neutral wording (CLAUDE.md, Output): "possible error to verify", never accusations or care opinions.

Three kinds of wording are not allowed in anything the system reports:
- accusations (fraud, illegal, scam, upcoding, kickback, deliberate ...): the
  system finds possible errors, it does not judge intent;
- opinions on whether care was necessary or appropriate: that needs clinical
  records the system does not have;
- legal advice (sue, lawsuit, attorney): the report is not legal advice.
"""

import re

BANNED_WORDS = re.compile(r"\b(fraud\w*|illegal\w*|scam\w*|criminal|crime|theft|steal\w*)\b", re.IGNORECASE)

NOT_NEUTRAL = {
    "accusation": re.compile(
        r"\b(fraud\w*|illegal\w*|scam\w*|criminal|crime|theft|steal\w*|kickbacks?|upcod\w*|cheat\w*|swindl\w*"
        r"|rip(ped)?[- ]off|deliberate(ly)?|intentional(ly)?|on purpose|abus(e|ive) of billing|billing abuse)\b",
        re.IGNORECASE),
    "care_opinion": re.compile(
        r"\b((medically )?(un)?necessary (care|service|procedure|test|treatment|visit|surgery)s?"
        r"|medically (un)?necessary|not medically (needed|indicated|justified)|overtreat\w*"
        r"|should not have been (performed|done|ordered)|did not need (this|the|that) (care|service|procedure|test))\b",
        re.IGNORECASE),
    "legal_advice": re.compile(r"\b(sue|suing|lawsuit|legal action|attorney|lawyer|small claims court)\b", re.IGNORECASE),
}


def wording_problem(text: str) -> str | None:
    """'kind: phrase' for the first non-neutral phrase in `text`, or None."""
    for kind, pattern in NOT_NEUTRAL.items():
        found = pattern.search(text)
        if found:
            return f"{kind}: {found.group(0)!r}"
    return None
