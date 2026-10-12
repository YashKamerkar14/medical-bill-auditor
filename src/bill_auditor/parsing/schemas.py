"""Schemas for an itemized professional bill and the insurer's EOB.

These validate STRUCTURE only: types, required fields, allowed values. They
deliberately do not check arithmetic (units x price = charge, totals, balance),
because wrong arithmetic is one of the errors the auditor exists to find; a
schema that rejected it would hide the error instead of reporting it.

All money is integer cents. `extra="forbid"` rejects unknown fields, so a
malformed or tampered input fails loudly instead of being half-read.

Size and character limits (Milestone 6, guardrails): every text field has a
maximum length and may not contain invisible control or formatting characters
(zero-width spaces, right-to-left overrides and the like can hide instructions
from a person reviewing the bill); codes and modifiers are letters and digits;
units and amounts have sane upper bounds; a bill has at most MAX_LINES lines.
Limits stop prompt stuffing and runaway cost, and they are far above any real
professional bill.
"""

import unicodedata
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_LINES = 50
MAX_CENTS = 1_000_000_000   # $10 million: above any single professional bill
MAX_UNITS = 9_999
_ALLOWED_CONTROL = {"\n", "\t"}


def _visible(text: str) -> str:
    """Reject invisible control (Cc) and formatting (Cf) characters, e.g. U+200B zero-width space, U+202E RLO."""
    bad = sorted({f"U+{ord(ch):04X}" for ch in text
                  if unicodedata.category(ch) in ("Cc", "Cf") and ch not in _ALLOWED_CONTROL})
    if bad:
        raise ValueError(f"contains invisible control or formatting characters ({', '.join(bad[:5])})")
    return text


def Text(max_length: int, min_length: int = 0):  # noqa: N802 - a type factory
    return Annotated[str, Field(min_length=min_length, max_length=max_length), AfterValidator(_visible)]


Cents = Annotated[int, Field(ge=0, le=MAX_CENTS)]
SignedCents = Annotated[int, Field(ge=-MAX_CENTS, le=MAX_CENTS)]
Identifier = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
_STRICT = ConfigDict(extra="forbid", frozen=True)

# Place-of-service codes used in this project (CMS POS code set). Office is a
# non-facility setting; hospital settings are facility settings, which changes
# which fee schedule amount applies.
PLACE_OF_SERVICE_SETTING = {
    "11": "nonfacility",  # office
    "22": "facility",     # on-campus outpatient hospital
    "21": "facility",     # inpatient hospital
}


class Patient(BaseModel):
    """Personal details. Always fake (synthetic bills only) and always redacted before a model sees them."""

    model_config = _STRICT
    name: Text(100, min_length=1)
    date_of_birth: date
    address: Text(200, min_length=1)
    account_number: Identifier
    member_id: Identifier | None = None  # insurance member id; None on self-pay bills


class Provider(BaseModel):
    model_config = _STRICT
    name: Text(100, min_length=1)
    npi: str = Field(pattern=r"^\d{10}$")          # synthetic; never looked up in the real registry
    locality_id: str = Field(pattern=r"^\d{7}$")   # CMS pricing locality: carrier + locality


def _clean_code(code: str) -> str:
    code = code.strip().upper()
    if not (len(code) == 5 and code.isascii() and code.isalnum()):
        raise ValueError("codes are 5 letters or digits")
    return code


def _clean_mods(modifiers: tuple[str, ...]) -> tuple[str, ...]:
    cleaned = tuple(m.strip().upper() for m in modifiers)
    if any(not (len(m) == 2 and m.isascii() and m.isalnum()) for m in cleaned):
        raise ValueError("modifiers are 2 letters or digits")
    return cleaned


class BillLine(BaseModel):
    model_config = _STRICT
    line_no: int = Field(ge=1)
    date_of_service: date
    code: str = Field(min_length=5, max_length=5)
    modifiers: tuple[str, ...] = Field(default=(), max_length=4)
    units: int = Field(ge=1, le=MAX_UNITS)
    unit_price_cents: Cents
    charge_cents: Cents
    place_of_service: str = Field(max_length=2)
    rendering_provider_npi: str = Field(pattern=r"^\d{10}$")
    description: Text(300) = ""
    # Posted from the EOB on insured bills; None on self-pay bills.
    insurance_paid_cents: Cents | None = None
    adjustment_cents: Cents | None = None
    patient_balance_cents: Cents | None = None

    @field_validator("code")
    @classmethod
    def _upper_code(cls, code: str) -> str:
        return _clean_code(code)

    @field_validator("modifiers")
    @classmethod
    def _clean_modifiers(cls, modifiers: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_mods(modifiers)

    @field_validator("place_of_service")
    @classmethod
    def _known_pos(cls, pos: str) -> str:
        if pos not in PLACE_OF_SERVICE_SETTING:
            raise ValueError(f"place of service must be one of {sorted(PLACE_OF_SERVICE_SETTING)}")
        return pos


class Bill(BaseModel):
    model_config = _STRICT
    synthetic: bool  # marker written by our generator; other input needs an explicit flag (Milestone 6)
    bill_id: Identifier
    bill_type: Literal["professional", "institutional"]  # institutional (UB-04) is out of scope
    statement_date: date
    patient: Patient
    provider: Provider
    lines: tuple[BillLine, ...] = Field(min_length=1, max_length=MAX_LINES)
    total_charges_cents: Cents
    payments_cents: Cents                     # insurance + patient payments already received
    adjustments_cents: Cents                  # write-offs and discounts
    balance_due_cents: SignedCents            # can be negative (credit balance)
    notes: Text(2000) = ""                    # free text; DATA, never instructions

    @model_validator(mode="after")
    def _unique_line_numbers(self) -> "Bill":
        numbers = [line.line_no for line in self.lines]
        if len(set(numbers)) != len(numbers):
            raise ValueError("bill line numbers must be unique")
        return self

    @property
    def is_insured(self) -> bool:
        return self.patient.member_id is not None


GroupCode = Literal["CO", "PR", "OA", "PI"]  # contractual, patient responsibility, other, payer initiated


class Adjustment(BaseModel):
    model_config = _STRICT
    group_code: GroupCode
    reason_code: str = Field(pattern=r"^[A-Z]?\d{1,3}$")  # CARC, e.g. "45", "1", "97"
    amount_cents: Cents


class EobLine(BaseModel):
    model_config = _STRICT
    line_no: int = Field(ge=1)
    date_of_service: date
    code: str = Field(min_length=5, max_length=5)
    modifiers: tuple[str, ...] = Field(default=(), max_length=4)
    units: int = Field(ge=1, le=MAX_UNITS)
    billed_cents: Cents
    allowed_cents: Cents
    paid_cents: Cents
    adjustments: tuple[Adjustment, ...] = Field(default=(), max_length=10)
    remark: Text(500) = ""  # free text; DATA, never instructions

    @field_validator("code")
    @classmethod
    def _upper_code(cls, code: str) -> str:
        return _clean_code(code)

    @field_validator("modifiers")
    @classmethod
    def _clean_modifiers(cls, modifiers: tuple[str, ...]) -> tuple[str, ...]:
        return _clean_mods(modifiers)

    @property
    def patient_responsibility_cents(self) -> Cents:
        """What the patient owes for this line: the sum of PR adjustments."""
        return sum(a.amount_cents for a in self.adjustments if a.group_code == "PR")


class Eob(BaseModel):
    model_config = _STRICT
    synthetic: bool
    eob_id: Identifier
    payer_name: Text(100, min_length=1)
    member_id: Identifier
    patient_name: Text(100, min_length=1)
    claim_bill_id: Identifier  # the bill this EOB answers
    lines: tuple[EobLine, ...] = Field(min_length=1, max_length=MAX_LINES)

    @property
    def patient_responsibility_cents(self) -> Cents:
        return sum(line.patient_responsibility_cents for line in self.lines)
