"""Synthetic bill + EOB generator with planted errors and an exact answer key.

How it works (docs/decisions/0006-evaluation-design.md):
1. Build a CLEAN bill from a seeded random generator: real, commonly billed
   codes priced at 1.3-2.5x Medicare, at most one Z-code (so no code-pair edit
   can happen by accident), fake personal details, and for insured bills an EOB
   whose numbers all agree with the bill.
2. Apply PLANTERS. Each planter changes the bill in one known way and records
   the matching answer-key entry (lines, dollar amount, reason).
3. Some cases are TRAPS: things that look like errors but are not.

Scope of the generated set (hand-written hard cases cover the rest):
- provider-liability errors are planted on insured bills; every other error on
  self-pay bills, so planted errors never interact with EOB adjustments.
- each case has one planted error, sometimes two on different lines.

Generated cases contain real codes and Medicare amounts, so they are written
under DATA_DIR, never committed. Only this code and the seed are committed.
"""

import csv
import json
import random
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from bill_auditor.checks.settings import PRICE_BENCHMARK_RATIO
from bill_auditor.evaluation.schemas import Case, ExpectedFlag
from bill_auditor.ingest.sources import SYNTHETIC_TABLE_DIR
from bill_auditor.parsing.schemas import PLACE_OF_SERVICE_SETTING, Bill, Eob
from bill_auditor.tables.fee_schedule import get_fee_schedule_amount

SEED = 20261007
LOCALITIES = ("0111205", "1320201", "0441299", "1011200", "0610216")  # SF, Manhattan, rest of TX, AL, Chicago
WINDOW_START, WINDOW_END = date(2026, 10, 1), date(2026, 12, 20)
UNKNOWN_CODE = "Y9001"  # no real or synthetic code starts with Y
CLEAN_PRICE_RANGE = (1.3, 2.5)
OVERPRICE_RANGE = (4.0, 8.0)

# How many cases of each kind (300 in total).
SCHEDULE = {
    "clean": 75,
    "trap:modifier_bypass": 5, "trap:repeat_modifier_76": 5, "trap:mai1_split_lines": 5,
    "trap:deleted_edit": 5, "trap:deductible": 5,
    "cannot_check:institutional": 5, "cannot_check:outside_release": 5, "cannot_check:unknown_code": 5,
    "error:balance_billing": 14, "error:coinsurance_on_charge": 13, "error:denied_billed_to_patient": 13,
    "error:duplicate": 28,
    "error:arithmetic_line": 12, "error:arithmetic_total": 10, "error:arithmetic_balance": 10,
    "error:quantity": 28, "error:unbundling": 28, "error:price": 29,
}
INSURED_KINDS = {"error:balance_billing", "error:coinsurance_on_charge", "error:denied_billed_to_patient", "trap:deductible"}
SECOND_ERROR_CASES = 20  # self-pay error cases that get a second, independent error
DEV_SHARE = 0.7


# --- reference data -------------------------------------------------------------

@dataclass(frozen=True)
class RealCode:
    code: str
    description: str
    medicare_cents: dict[tuple[str, str], int]  # (locality_id, setting) -> amount


@dataclass(frozen=True)
class SyntheticRules:
    """Read straight from synthetic_tables/*.csv, independent of the lookup code."""

    descriptions: dict[str, str]
    ptp: list[dict[str, str]]
    mue: dict[str, tuple[int, str]]  # code -> (limit, mai)

    def pair_rows(self, a: str, b: str) -> list[dict[str, str]]:
        return [r for r in self.ptp if {r["column1"], r["column2"]} == {a, b}]

    def any_pair(self, code: str, others: set[str]) -> bool:
        return any(self.pair_rows(code, other) for other in others)


def load_synthetic_rules(table_dir: Path = SYNTHETIC_TABLE_DIR) -> SyntheticRules:
    def rows(name):
        with (table_dir / name).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    return SyntheticRules(
        descriptions={r["code"]: r["description"] for r in rows("codes.csv")},
        ptp=rows("ptp_edits.csv"),
        mue={r["code"]: (int(r["mue_value"]), r["mai"]) for r in rows("mue_limits.csv")},
    )


def load_code_pool(conn, size: int = 60) -> list[RealCode]:
    """The most frequently billed simple physician services, priced in every test locality.

    'Simple' = status A (paid), PC/TC indicator 0 (no professional/technical
    split), and at least $10 in every locality and setting, so a 1.3-2.5x
    markup can never round past the price-benchmark threshold.
    """
    candidates = conn.execute(
        """SELECT c.code, c.description FROM charge_benchmarks c
           WHERE c.geo_level = 'National' AND c.place_of_service = 'O'
           ORDER BY c.total_services DESC LIMIT 1000"""  # many top codes are labs/drugs, not on the PFS
    ).fetchall()
    pool = []
    for row in candidates:
        amounts = {}
        for locality in LOCALITIES:
            for setting in ("nonfacility", "facility"):
                result = get_fee_schedule_amount(conn, row["code"], None, locality, setting, WINDOW_START)
                if result.status != "found" or result.status_code != "A" or result.pctc_indicator != "0":
                    break
                amounts[(locality, setting)] = result.amount_cents
            else:
                continue
            break
        if len(amounts) == len(LOCALITIES) * 2 and min(amounts.values()) >= 1000:
            pool.append(RealCode(row["code"], row["description"], amounts))
        if len(pool) == size:
            break
    if len(pool) < size:
        raise RuntimeError(f"Only {len(pool)} usable codes found; is the reference database built?")
    return pool


# --- the working draft of one case ------------------------------------------------

class Draft:
    """A mutable bill + EOB being built; validated into Bill/Eob at the end."""

    def __init__(self, rng: random.Random, fake: Faker, kind: str, insured: bool):
        self.rng, self.fake, self.kind, self.insured = rng, fake, kind, insured
        self.lines: list[dict] = []
        self.eob_lines: list[dict] = []
        self.expected: list[ExpectedFlag] = []
        self.traps: list[tuple[str, tuple[int, ...]]] = []
        self.bill_type = "professional"
        self.locality = rng.choice(LOCALITIES)
        self.npi = "9" + "".join(rng.choice("0123456789") for _ in range(9))
        self.dates = self._dates()
        self.payments = self.adjustments = self.balance = self.total = 0
        self.used_lines: set[int] = set()  # lines already touched by a planter

    def _dates(self) -> list[date]:
        first = WINDOW_START + timedelta(days=self.rng.randrange((WINDOW_END - WINDOW_START).days))
        if self.rng.random() < 0.3:
            return [first, min(first + timedelta(days=self.rng.randint(1, 7)), WINDOW_END)]
        return [first]

    def next_line_no(self) -> int:
        return max((line["line_no"] for line in self.lines), default=0) + 1

    def add_line(self, code: str, description: str, unit_price: int, units: int = 1,
                 modifiers: tuple[str, ...] = (), dos: date | None = None, pos: str | None = None) -> dict:
        line = {
            "line_no": self.next_line_no(), "date_of_service": dos or self.rng.choice(self.dates),
            "code": code, "modifiers": modifiers, "units": units,
            "unit_price_cents": unit_price, "charge_cents": unit_price * units,
            "place_of_service": pos or ("11" if self.rng.random() < 0.8 else "22"),
            "rendering_provider_npi": self.npi, "description": description,
        }
        self.lines.append(line)
        return line

    def line(self, line_no: int) -> dict:
        return next(line for line in self.lines if line["line_no"] == line_no)

    def z_codes(self) -> set[str]:
        return {line["code"] for line in self.lines if line["code"].startswith("Z")}

    def expect(self, **fields) -> None:
        self.expected.append(ExpectedFlag(**fields))


def setting_of(line: dict) -> str:
    return PLACE_OF_SERVICE_SETTING[line["place_of_service"]]


def z_unit_price(code: str) -> int:
    """A stable made-up price for a Z-code: $40 to $399, whole dollars."""
    return (40 + zlib.crc32(code.encode()) % 360) * 100


def clean_unit_price(rng: random.Random, medicare_cents: int) -> int:
    return round(medicare_cents * rng.uniform(*CLEAN_PRICE_RANGE) / 100) * 100


# --- building a clean case ---------------------------------------------------------

def build_clean(draft: Draft, pool: list[RealCode], rules: SyntheticRules) -> None:
    rng = draft.rng
    for code in rng.sample(pool, rng.randint(2, 5)):
        line = draft.add_line(code.code, code.description, 0)
        price = clean_unit_price(rng, code.medicare_cents[(draft.locality, setting_of(line))])
        line["unit_price_cents"] = line["charge_cents"] = price
    if rng.random() < 0.4:  # at most one Z-code: a single Z-code can never form a code pair
        code = rng.choice(sorted(rules.descriptions))
        limit = rules.mue.get(code, (1, "2"))[0]
        units = rng.randint(1, limit) if code in ("Z1060", "Z1070") else 1
        draft.add_line(code, rules.descriptions[code], z_unit_price(code), units=units)


def build_eob(draft: Draft, pool_by_code: dict[str, RealCode], deductible_left: int | None = None) -> None:
    """Price every line as an insurer would and post the result onto the bill lines."""
    rng = draft.rng
    remaining = deductible_left if deductible_left is not None else rng.choice([0, 0, 0, rng.randint(50, 400) * 100])
    draft.eob_lines = []
    for line in draft.lines:
        charge = line["charge_cents"]
        if line["code"] in pool_by_code:
            medicare = pool_by_code[line["code"]].medicare_cents[(draft.locality, setting_of(line))]
            allowed = min(charge, round(medicare * line["units"] * rng.uniform(1.0, 1.6)))
        else:
            allowed = round(charge * rng.uniform(0.5, 0.75))
        deductible = min(remaining, allowed)
        remaining -= deductible
        coinsurance = round((allowed - deductible) * 0.2)
        contractual = charge - allowed
        adjustments = []
        if contractual:
            adjustments.append({"group_code": "CO", "reason_code": "45", "amount_cents": contractual})
        if deductible:
            adjustments.append({"group_code": "PR", "reason_code": "1", "amount_cents": deductible})
        if coinsurance:
            adjustments.append({"group_code": "PR", "reason_code": "2", "amount_cents": coinsurance})
        patient = deductible + coinsurance
        paid = allowed - patient
        draft.eob_lines.append({
            "line_no": line["line_no"], "date_of_service": line["date_of_service"], "code": line["code"],
            "modifiers": line["modifiers"], "units": line["units"], "billed_cents": charge,
            "allowed_cents": allowed, "paid_cents": paid, "adjustments": adjustments, "remark": "",
        })
        line.update(insurance_paid_cents=paid, adjustment_cents=contractual, patient_balance_cents=patient)


def finalize_totals(draft: Draft) -> None:
    draft.total = sum(line["charge_cents"] for line in draft.lines)
    if draft.insured:
        draft.payments = sum(line["insurance_paid_cents"] for line in draft.lines)
        draft.adjustments = sum(line["adjustment_cents"] for line in draft.lines)
    else:
        rng = draft.rng
        draft.payments = rng.choice([0, 0, rng.randint(1, max(1, draft.total // 400)) * 100])
        draft.adjustments = rng.choice([0, 0, round(draft.total * rng.choice([0.1, 0.15, 0.2]))])
    draft.balance = draft.total - draft.payments - draft.adjustments


# --- planters: errors --------------------------------------------------------------

def _free_line(draft: Draft, real_only: bool = False, pool_codes: set[str] | None = None) -> dict:
    options = [
        line for line in draft.lines
        if line["line_no"] not in draft.used_lines and (not real_only or line["code"] in pool_codes)
    ]
    line = draft.rng.choice(options)
    draft.used_lines.add(line["line_no"])
    return line


def plant_balance_billing(draft: Draft, **_) -> None:
    """Provider did not write off the contractual (CO-45) amount and billed it to the patient."""
    line = draft.rng.choice([l for l in draft.lines if l["adjustment_cents"] > 0 and l["line_no"] not in draft.used_lines])
    draft.used_lines.add(line["line_no"])
    contractual = line["adjustment_cents"]
    line["adjustment_cents"] = 0
    line["patient_balance_cents"] += contractual
    draft.expect(check="provider_liability", kind="error", bill_lines=(line["line_no"],), amount_cents=contractual,
                 reason="Contractual adjustment (CO-45) billed to the patient instead of written off")


def plant_coinsurance_on_charge(draft: Draft, **_) -> None:
    """Coinsurance computed on the full charge instead of on the allowed amount."""
    line = draft.rng.choice([l for l in draft.lines if l["adjustment_cents"] > 0 and l["line_no"] not in draft.used_lines])
    draft.used_lines.add(line["line_no"])
    eob = next(e for e in draft.eob_lines if e["line_no"] == line["line_no"])
    deductible = sum(a["amount_cents"] for a in eob["adjustments"] if a["reason_code"] == "1")
    owed = line["patient_balance_cents"]
    wrong = deductible + round((line["charge_cents"] - deductible) * 0.2)
    line["adjustment_cents"] -= wrong - owed          # keeps the line's own arithmetic consistent
    line["patient_balance_cents"] = wrong
    draft.expect(check="provider_liability", kind="error", bill_lines=(line["line_no"],), amount_cents=wrong - owed,
                 reason="Coinsurance computed on the billed charge instead of the allowed amount")


def plant_denied_billed_to_patient(draft: Draft, **_) -> None:
    """The insurer denied the line as provider liability (CO), but the full charge was billed to the patient."""
    line = _free_line(draft)
    eob = next(e for e in draft.eob_lines if e["line_no"] == line["line_no"])
    eob.update(allowed_cents=0, paid_cents=0, remark="Not covered under the provider's contract; patient may not be billed",
               adjustments=[{"group_code": "CO", "reason_code": "96", "amount_cents": line["charge_cents"]}])
    line.update(insurance_paid_cents=0, adjustment_cents=0, patient_balance_cents=line["charge_cents"])
    draft.expect(check="provider_liability", kind="error", bill_lines=(line["line_no"],), amount_cents=line["charge_cents"],
                 reason="Line denied as provider liability (CO-96) but billed to the patient")


def plant_duplicate(draft: Draft, pool_codes: set[str], **_) -> None:
    original = _free_line(draft, real_only=True, pool_codes=pool_codes)
    copy = dict(original, line_no=draft.next_line_no())
    draft.lines.append(copy)
    draft.used_lines.add(copy["line_no"])
    draft.expect(check="duplicate", kind="error", bill_lines=(original["line_no"], copy["line_no"]),
                 amount_cents=copy["charge_cents"], reason="Same code, date, provider and modifiers billed twice")


def plant_arithmetic_line(draft: Draft, **_) -> None:
    line = _free_line(draft)
    correct = line["unit_price_cents"] * line["units"]
    if draft.rng.random() < 0.5:
        line["charge_cents"] = line["unit_price_cents"] * (line["units"] + 1)
    else:
        line["charge_cents"] = correct + draft.rng.choice([1000, 5000, 10000])
    draft.expect(check="arithmetic", kind="error", target="line", bill_lines=(line["line_no"],),
                 amount_cents=abs(line["charge_cents"] - correct), reason="Line charge is not units x unit price")


def plant_arithmetic_total(draft: Draft, **_) -> None:  # runs after finalize_totals
    delta = draft.rng.choice([1000, 2500, 10000, -5000])
    draft.total += delta
    draft.balance = draft.total - draft.payments - draft.adjustments  # balance agrees with the stated total
    draft.expect(check="arithmetic", kind="error", target="total_charges", amount_cents=abs(delta),
                 reason="Total charges do not equal the sum of the line charges")


def plant_arithmetic_balance(draft: Draft, **_) -> None:  # runs after finalize_totals
    delta = draft.rng.choice([2000, 5000, 15000])
    draft.balance += delta
    draft.expect(check="arithmetic", kind="error", target="balance_due", amount_cents=delta,
                 reason="Balance due does not equal charges minus payments minus adjustments")


QUANTITY_CHOICES = (("Z1060", (5, 7)), ("Z1001", (2, 2)), ("Z1020", (5, 6)), ("Z1050", (4, 5)), ("Z1010", (3, 3)))


def plant_quantity(draft: Draft, rules: SyntheticRules, **_) -> None:
    present = draft.z_codes()
    choices = [(c, r) for c, r in QUANTITY_CHOICES if c not in present and not rules.any_pair(c, present)]
    code, (low, high) = draft.rng.choice(choices)
    limit, mai = rules.mue[code]
    units = draft.rng.randint(low, high)
    line = draft.add_line(code, rules.descriptions[code], z_unit_price(code), units=units)
    draft.used_lines.add(line["line_no"])
    draft.expect(check="quantity", kind="error", bill_lines=(line["line_no"],),
                 amount_cents=(units - limit) * line["unit_price_cents"],
                 reason=f"{units} units of {code}; synthetic limit is {limit} (MAI {mai})")


def active_pairs(rules: SyntheticRules, dos: date) -> list[dict[str, str]]:
    day = dos.isoformat()
    return [
        r for r in rules.ptp
        if r["modifier_indicator"] in ("0", "1") and r["effective_date"] <= day
        and (not r["deletion_date"] or day < r["deletion_date"])
    ]


def _add_pair(draft: Draft, rules: SyntheticRules, indicators: tuple[str, ...], modifiers2: tuple[str, ...] = ()):
    dos = draft.rng.choice(draft.dates)
    present = draft.z_codes()
    rows = [
        r for r in active_pairs(rules, dos)
        if r["modifier_indicator"] in indicators
        and not ({r["column1"], r["column2"]} & present)
        and not rules.any_pair(r["column1"], present) and not rules.any_pair(r["column2"], present)
    ]
    row = draft.rng.choice(rows)
    first = draft.add_line(row["column1"], rules.descriptions[row["column1"]], z_unit_price(row["column1"]), dos=dos)
    second = draft.add_line(row["column2"], rules.descriptions[row["column2"]], z_unit_price(row["column2"]),
                            modifiers=modifiers2, dos=dos, pos=first["place_of_service"])
    draft.used_lines |= {first["line_no"], second["line_no"]}
    return row, first, second


def plant_unbundling(draft: Draft, rules: SyntheticRules, **_) -> None:
    row, first, second = _add_pair(draft, rules, ("0", "1"))
    draft.expect(check="unbundling", kind="error", bill_lines=(first["line_no"], second["line_no"]),
                 amount_cents=second["charge_cents"],
                 reason=f"{row['column2']} billed with {row['column1']}; synthetic code-pair edit, indicator {row['modifier_indicator']}, no modifier")


def plant_price(draft: Draft, pool_by_code: dict[str, RealCode], **_) -> None:
    line = _free_line(draft, real_only=True, pool_codes=set(pool_by_code))
    medicare = pool_by_code[line["code"]].medicare_cents[(draft.locality, setting_of(line))]
    line["unit_price_cents"] = round(medicare * draft.rng.uniform(*OVERPRICE_RANGE) / 100) * 100
    line["charge_cents"] = line["unit_price_cents"] * line["units"]
    assert line["unit_price_cents"] >= PRICE_BENCHMARK_RATIO * medicare
    draft.expect(check="price_benchmark", kind="benchmark", bill_lines=(line["line_no"],),
                 amount_cents=line["charge_cents"] - medicare * line["units"],
                 reason="Charge is several times the Medicare amount (a benchmark, not an error)")


LINE_PLANTERS = {
    "error:balance_billing": plant_balance_billing,
    "error:coinsurance_on_charge": plant_coinsurance_on_charge,
    "error:denied_billed_to_patient": plant_denied_billed_to_patient,
    "error:duplicate": plant_duplicate,
    "error:arithmetic_line": plant_arithmetic_line,
    "error:quantity": plant_quantity,
    "error:unbundling": plant_unbundling,
    "error:price": plant_price,
}
TOTAL_PLANTERS = {"error:arithmetic_total": plant_arithmetic_total, "error:arithmetic_balance": plant_arithmetic_balance}
SECOND_ERROR_OPTIONS = ("error:duplicate", "error:price", "error:quantity", "error:unbundling")


# --- traps and cannot-check cases ----------------------------------------------------

def trap_modifier_bypass(draft: Draft, rules: SyntheticRules, **_) -> None:
    _, first, second = _add_pair(draft, rules, ("1",), modifiers2=(draft.rng.choice(["59", "XS"]),))
    draft.traps.append(("unbundling", (first["line_no"], second["line_no"])))


def trap_repeat_modifier_76(draft: Draft, pool_codes: set[str], **_) -> None:
    original = _free_line(draft, real_only=True, pool_codes=pool_codes)
    repeat = dict(original, line_no=draft.next_line_no(), modifiers=("76",))
    draft.lines.append(repeat)
    draft.traps.append(("duplicate", (original["line_no"], repeat["line_no"])))


def trap_mai1_split_lines(draft: Draft, rules: SyntheticRules, **_) -> None:
    for line in [l for l in draft.lines if l["code"].startswith("Z")]:  # make room: no other Z-code
        draft.lines.remove(line)
    dos = draft.rng.choice(draft.dates)
    left = draft.add_line("Z1010", rules.descriptions["Z1010"], z_unit_price("Z1010"), units=2, modifiers=("LT",), dos=dos)
    right = draft.add_line("Z1010", rules.descriptions["Z1010"], z_unit_price("Z1010"), units=2, modifiers=("RT",), dos=dos)
    draft.traps.append(("quantity", (left["line_no"], right["line_no"])))


def trap_deleted_edit(draft: Draft, rules: SyntheticRules, **_) -> None:
    for line in [l for l in draft.lines if l["code"].startswith("Z")]:
        draft.lines.remove(line)
    dos = date(2026, 11, 15) + timedelta(days=draft.rng.randrange(35))  # on/after the edit's deletion date
    first = draft.add_line("Z1060", rules.descriptions["Z1060"], z_unit_price("Z1060"), units=2, dos=dos)
    second = draft.add_line("Z1061", rules.descriptions["Z1061"], z_unit_price("Z1061"), dos=dos)
    draft.traps.append(("unbundling", (first["line_no"], second["line_no"])))


def make_unknown_code(draft: Draft, **_) -> None:
    line = draft.add_line(UNKNOWN_CODE, "Miscellaneous service", 15000)
    draft.expect(check=None, kind="cannot_check", target="line", bill_lines=(line["line_no"],),
                 reason="Code is not in any loaded table")


# --- assembling a case -----------------------------------------------------------------

def _patient(fake: Faker, rng: random.Random, insured: bool) -> dict:
    return {
        "name": fake.name(),
        "date_of_birth": fake.date_between(start_date=date(1940, 1, 1), end_date=date(2005, 12, 31)),
        "address": fake.address().replace("\n", ", "),
        "account_number": f"ACCT-{rng.randint(10**7, 10**8 - 1)}",
        "member_id": f"MBR{rng.randint(10**8, 10**9 - 1)}" if insured else None,
    }


def build_case(index: int, kind: str, pool: list[RealCode], rules: SyntheticRules, second_error: str | None) -> Case:
    rng = random.Random(SEED * 1000 + index)
    fake = Faker("en_US")
    fake.seed_instance(SEED + index)
    insured = kind in INSURED_KINDS or (kind == "clean" and rng.random() < 0.45)
    draft = Draft(rng, fake, kind, insured)
    pool_by_code = {c.code: c for c in pool}
    context = dict(rules=rules, pool_by_code=pool_by_code, pool_codes=set(pool_by_code))

    build_clean(draft, pool, rules)

    if kind == "cannot_check:outside_release":
        start = date(2026, 9, 1) + timedelta(days=rng.randrange(25))
        draft.dates = [start]
        for line in draft.lines:
            line["date_of_service"] = start
        draft.expect(check=None, kind="cannot_check", target="bill",
                     reason="Dates of service are before the loaded table releases (2026-10-01)")
    elif kind == "cannot_check:institutional":
        draft.bill_type = "institutional"
        draft.expect(check=None, kind="cannot_check", target="bill",
                     reason="Institutional (UB-04) bills are out of scope")
    elif kind == "cannot_check:unknown_code":
        make_unknown_code(draft)
    elif kind == "trap:modifier_bypass":
        trap_modifier_bypass(draft, **context)
    elif kind == "trap:repeat_modifier_76":
        trap_repeat_modifier_76(draft, **context)
    elif kind == "trap:mai1_split_lines":
        trap_mai1_split_lines(draft, **context)
    elif kind == "trap:deleted_edit":
        trap_deleted_edit(draft, **context)

    for line_kind in (kind, second_error):
        if line_kind in LINE_PLANTERS and line_kind not in INSURED_KINDS:
            LINE_PLANTERS[line_kind](draft, **context)

    if insured:
        large = rng.randint(800, 1500) * 100 if kind == "trap:deductible" else None
        build_eob(draft, pool_by_code, deductible_left=large)
        if kind in LINE_PLANTERS:
            LINE_PLANTERS[kind](draft, **context)
        if kind == "trap:deductible":
            draft.traps.append(("provider_liability", tuple(l["line_no"] for l in draft.lines)))

    finalize_totals(draft)
    if kind in TOTAL_PLANTERS:
        TOTAL_PLANTERS[kind](draft, **context)

    last_dos = max(line["date_of_service"] for line in draft.lines)
    case_id = f"gen-{index:04d}"
    bill = Bill(
        synthetic=True, bill_id=f"BILL-{case_id}", bill_type=draft.bill_type,
        statement_date=last_dos + timedelta(days=rng.randint(10, 30)),
        patient=_patient(fake, rng, insured),
        provider={"name": f"{fake.last_name()} {rng.choice(['Family Medicine', 'Medical Group', 'Clinic', 'Health Partners'])}",
                  "npi": draft.npi, "locality_id": draft.locality},
        lines=draft.lines, total_charges_cents=draft.total, payments_cents=draft.payments,
        adjustments_cents=draft.adjustments, balance_due_cents=draft.balance,
        notes=rng.choice(["", "", "Thank you for choosing our practice.", "Payment due within 30 days."]),
    )
    eob = None
    if insured:
        eob = Eob(synthetic=True, eob_id=f"EOB-{case_id}", payer_name="Synthetic Health Plan",
                  member_id=bill.patient.member_id, patient_name=bill.patient.name,
                  claim_bill_id=bill.bill_id, lines=draft.eob_lines)
    tags = tuple(t for t in (kind, second_error) if t)
    return Case(case_id=case_id, source="generated", split="dev", tags=tags, bill=bill, eob=eob,
                expected=tuple(draft.expected), traps=tuple(draft.traps))


def generate(conn, seed: int = SEED, pool: list[RealCode] | None = None) -> list[Case]:
    """All cases for the schedule. `pool` can be passed in (tests use fake codes)."""
    rules = load_synthetic_rules()
    pool = pool if pool is not None else load_code_pool(conn)
    kinds = [kind for kind, count in SCHEDULE.items() for _ in range(count)]
    rng = random.Random(seed)
    rng.shuffle(kinds)

    self_pay_errors = [i for i, k in enumerate(kinds) if k.startswith("error:") and k not in INSURED_KINDS
                       and k not in TOTAL_PLANTERS]
    second = {i: None for i in range(len(kinds))}
    for i in rng.sample(self_pay_errors, SECOND_ERROR_CASES):
        second[i] = rng.choice([k for k in SECOND_ERROR_OPTIONS if k != kinds[i]])

    cases = [build_case(i + 1, kind, pool, rules, second[i]) for i, kind in enumerate(kinds)]

    order = list(range(len(cases)))
    rng.shuffle(order)
    dev = set(order[: round(len(cases) * DEV_SHARE)])
    return [c.model_copy(update={"split": "dev" if i in dev else "heldout"}) for i, c in enumerate(cases)]


def write_cases(cases: list[Case], out_dir: Path, seed: int = SEED) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cases.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(case.model_dump_json() + "\n")
    manifest = {
        "seed": seed,
        "cases": len(cases),
        "splits": dict(Counter(c.split for c in cases)),
        "kinds": dict(Counter(tag for c in cases for tag in c.tags)),
        "expected_flags": dict(Counter(f"{f.kind}:{f.check}" for c in cases for f in c.expected)),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def read_cases(path: Path) -> list[Case]:
    with path.open(encoding="utf-8") as handle:
        return [Case.model_validate_json(line) for line in handle if line.strip()]
