"""Pair bill lines with EOB lines, deterministically, most certain first.

EOB lines carry their own numbering and can come in any order, so they are
matched on what they describe:
1. exact: same code, date of service, modifiers (any order) and units, one
   candidate on each side;
2. ordered: several identical lines on both sides, same count, paired in claim
   order (the usual convention; it lets a denied repeat line be matched). Only
   when the bill lines are truly identical, including description, provider
   and charge: lines that differ ("chest" vs "abdomen") could be crossed by
   order alone, so they are left ambiguous for model-assisted matching;
3. code_date: a unique match on code and date of service only;
4. leftovers are "unmatched" (no candidate) or "ambiguous" (several candidates).
Ambiguous pairs are where model-assisted matching helps (Milestone 3); until
then their patient balance is reported as "cannot check".
"""

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from bill_auditor.parsing.schemas import Bill, Eob

Method = Literal["exact", "ordered", "code_date", "model", "unmatched", "ambiguous"]


class LineMatch(BaseModel):
    bill_line: int
    eob_line: int | None
    method: Method


def _full_key(line) -> tuple:
    return (line.code, line.date_of_service, tuple(sorted(line.modifiers)), line.units)


def _short_key(line) -> tuple:
    return (line.code, line.date_of_service)


def match_lines(bill: Bill, eob: Eob) -> list[LineMatch]:
    matched: dict[int, LineMatch] = {}
    free_eob = {e.line_no: e for e in eob.lines}

    # Rounds 1 and 2: identical description.
    bill_groups, eob_groups = defaultdict(list), defaultdict(list)
    for line in bill.lines:
        bill_groups[_full_key(line)].append(line)
    for line in eob.lines:
        eob_groups[_full_key(line)].append(line)
    for key, bill_lines in bill_groups.items():
        eob_lines = eob_groups.get(key, [])
        truly_identical = len({(l.description, l.rendering_provider_npi, l.charge_cents) for l in bill_lines}) == 1
        if eob_lines and len(eob_lines) == len(bill_lines) and (len(bill_lines) == 1 or truly_identical):
            method = "exact" if len(bill_lines) == 1 else "ordered"
            for b, e in zip(bill_lines, eob_lines):  # both lists are in claim order
                matched[b.line_no] = LineMatch(bill_line=b.line_no, eob_line=e.line_no, method=method)
                free_eob.pop(e.line_no)

    # Round 3: unique code + date among what is left.
    left = [line for line in bill.lines if line.line_no not in matched]
    for line in left:
        bill_same = [b for b in left if _short_key(b) == _short_key(line)]
        eob_same = [e for e in free_eob.values() if _short_key(e) == _short_key(line)]
        if len(bill_same) == 1 and len(eob_same) == 1:
            matched[line.line_no] = LineMatch(bill_line=line.line_no, eob_line=eob_same[0].line_no, method="code_date")
            free_eob.pop(eob_same[0].line_no)

    # Round 4: leftovers.
    for line in bill.lines:
        if line.line_no not in matched:
            candidates = [e for e in free_eob.values() if _short_key(e) == _short_key(line)]
            method = "ambiguous" if candidates else "unmatched"
            matched[line.line_no] = LineMatch(bill_line=line.line_no, eob_line=None, method=method)
    return [matched[line.line_no] for line in bill.lines]
