"""What every check receives, and what it returns."""

import sqlite3
from dataclasses import dataclass, field

from bill_auditor.checks.matching import LineMatch
from bill_auditor.checks.models import CannotCheck, Flag
from bill_auditor.parsing.schemas import Bill, Eob


@dataclass(frozen=True)
class CheckContext:
    conn: sqlite3.Connection
    bill: Bill
    eob: Eob | None
    matches: list[LineMatch]
    skip_lines: frozenset[int]   # unknown code or no release for the date: no check looks at them
    table_checks: bool           # False when no table release covers the bill's dates

    def lines(self):
        return [line for line in self.bill.lines if line.line_no not in self.skip_lines]


@dataclass
class CheckResult:
    flags: list[Flag] = field(default_factory=list)
    cannot: list[CannotCheck] = field(default_factory=list)


def dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"
