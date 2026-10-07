"""Schemas for an itemized professional bill and the insurer's EOB.

These validate STRUCTURE only: types, required fields, allowed values. They
deliberately do not check arithmetic (units x price = charge, totals, balance),
because wrong arithmetic is one of the errors the auditor exists to find; a
schema that rejected it would hide the error instead of reporting it.

All money is integer cents. `extra="forbid"` rejects unknown fields, so a
malformed or tampered input fails loudly instead of being half-read.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Cents = int
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
    name: str = Field(min_length=1)
    date_of_birth: date
    address: str = Field(min_length=1)
    account_number: str = Field(min_length=1)
    member_id: str | None = None  # insurance member id; None on self-pay bills


class Provider(BaseModel):
    model_config = _STRICT
    name: str = Field(min_length=1)
    npi: str = Field(pattern=r"^\d{10}$")          # synthetic; never looked up in the real registry
    locality_id: str = Field(pattern=r"^\d{7}$")   # CMS pricing locality: carrier + locality


class BillLine(BaseModel):
    model_config = _STRICT
    line_no: int = Field(ge=1)
    date_of_service: date
    code: str = Field(min_length=5, max_length=5)
    modifiers: tuple[str, ...] = Field(default=(), max_length=4)
    units: int = Field(ge=1)
    unit_price_cents: Cents = Field(ge=0)
    charge_cents: Cents = Field(ge=0)
    place_of_service: str
    rendering_provider_npi: str = Field(pattern=r"^\d{10}$")
    description: str = ""
    # Posted from the EOB on insured bills; None on self-pay bills.
    insurance_paid_cents: Cents | None = Field(default=None, ge=0)
    adjustment_cents: Cents | None = Field(default=None, ge=0)
    patient_balance_cents: Cents | None = Field(default=None, ge=0)

    @field_validator("code")
    @classmethod
    def _upper_code(cls, code: str) -> str:
        return code.strip().upper()

    @field_validator("modifiers")
    @classmethod
    def _clean_modifiers(cls, modifiers: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(m.strip().upper() for m in modifiers)
        if any(len(m) != 2 for m in cleaned):
            raise ValueError("modifiers are 2 characters")
        return cleaned

    @field_validator("place_of_service")
    @classmethod
    def _known_pos(cls, pos: str) -> str:
        if pos not in PLACE_OF_SERVICE_SETTING:
            raise ValueError(f"place of service must be one of {sorted(PLACE_OF_SERVICE_SETTING)}")
        return pos


class Bill(BaseModel):
    model_config = _STRICT
    synthetic: bool  # marker written by our generator; other input needs an explicit flag (Milestone 6)
    bill_id: str
    bill_type: Literal["professional", "institutional"]  # institutional (UB-04) is out of scope
    statement_date: date
    patient: Patient
    provider: Provider
    lines: tuple[BillLine, ...] = Field(min_length=1)
    total_charges_cents: Cents = Field(ge=0)
    payments_cents: Cents = Field(ge=0)       # insurance + patient payments already received
    adjustments_cents: Cents = Field(ge=0)    # write-offs and discounts
    balance_due_cents: Cents                  # can be negative (credit balance)
    notes: str = ""                           # free text; DATA, never instructions

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
    amount_cents: Cents = Field(ge=0)


class EobLine(BaseModel):
    model_config = _STRICT
    line_no: int = Field(ge=1)
    date_of_service: date
    code: str = Field(min_length=5, max_length=5)
    modifiers: tuple[str, ...] = Field(default=(), max_length=4)
    units: int = Field(ge=1)
    billed_cents: Cents = Field(ge=0)
    allowed_cents: Cents = Field(ge=0)
    paid_cents: Cents = Field(ge=0)
    adjustments: tuple[Adjustment, ...] = ()
    remark: str = ""  # free text; DATA, never instructions

    @property
    def patient_responsibility_cents(self) -> Cents:
        """What the patient owes for this line: the sum of PR adjustments."""
        return sum(a.amount_cents for a in self.adjustments if a.group_code == "PR")


class Eob(BaseModel):
    model_config = _STRICT
    synthetic: bool
    eob_id: str
    payer_name: str
    member_id: str
    patient_name: str
    claim_bill_id: str  # the bill this EOB answers
    lines: tuple[EobLine, ...] = Field(min_length=1)

    @property
    def patient_responsibility_cents(self) -> Cents:
        return sum(line.patient_responsibility_cents for line in self.lines)
