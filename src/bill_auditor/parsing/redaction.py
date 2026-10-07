"""Redact personal details before anything reaches a model or a log.

Produces a JSON-ready view of the bill and EOB in which the patient's name,
date of birth, address, account number and member id (and the provider's name)
are replaced by placeholders such as [PATIENT_NAME]. The same values are also
scrubbed from free text (notes, descriptions, EOB remarks), where they could
appear by accident or by design. The placeholder -> value mapping never leaves
this machine; `restore` puts the values back into text written by a model.

Codes, amounts, dates of service and the (synthetic) provider NPI are kept:
the checks need them and they identify no patient.
"""

import re
from dataclasses import dataclass, field

from bill_auditor.parsing.schemas import Bill, Eob


@dataclass
class Redacted:
    bill: dict
    eob: dict | None
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original value


def _secrets(bill: Bill, eob: Eob | None) -> dict[str, str]:
    """Placeholder -> value, most specific first so longer values are replaced before their parts."""
    p = bill.patient
    secrets = {
        "[PATIENT_NAME]": p.name,
        "[PATIENT_ADDRESS]": p.address,
        "[ACCOUNT_NUMBER]": p.account_number,
        "[DATE_OF_BIRTH]": p.date_of_birth.isoformat(),
        "[DATE_OF_BIRTH_US]": p.date_of_birth.strftime("%m/%d/%Y"),
        "[PROVIDER_NAME]": bill.provider.name,
    }
    if p.member_id:
        secrets["[MEMBER_ID]"] = p.member_id
    if eob is not None and eob.member_id and eob.member_id != p.member_id:
        secrets["[EOB_MEMBER_ID]"] = eob.member_id
    if eob is not None and eob.patient_name != p.name:
        secrets["[EOB_PATIENT_NAME]"] = eob.patient_name
    last_name = p.name.split()[-1]
    if len(last_name) >= 3:  # surname alone, e.g. "Mr. Smith" in a note
        secrets["[PATIENT_LAST_NAME]"] = last_name
    return dict(sorted(secrets.items(), key=lambda item: -len(item[1])))


def _whole(value: str) -> re.Pattern:
    """`value` as a whole word, optionally followed by "s" (plural / possessive).

    "Tate" matches "Mr. Tate", "Tate's" and "the Tates'", but not "statement":
    a name inside a longer word is not that name.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}s?(?![A-Za-z0-9])", re.IGNORECASE)


def contains_value(text: str, value: str) -> bool:
    """True if `value` appears in `text` as a whole word (used by the pre-send safety check)."""
    return bool(_whole(value).search(text))


def _scrub(text: str, secrets: dict[str, str]) -> str:
    for placeholder, value in secrets.items():
        text = _whole(value).sub(placeholder, text)
    return text


def redact(bill: Bill, eob: Eob | None = None) -> Redacted:
    secrets = _secrets(bill, eob)

    bill_view = bill.model_dump(mode="json")
    bill_view["patient"] = {
        "name": "[PATIENT_NAME]", "date_of_birth": "[DATE_OF_BIRTH]", "address": "[PATIENT_ADDRESS]",
        "account_number": "[ACCOUNT_NUMBER]", "member_id": "[MEMBER_ID]" if bill.patient.member_id else None,
    }
    bill_view["provider"]["name"] = "[PROVIDER_NAME]"
    bill_view["notes"] = _scrub(bill.notes, secrets)
    for line in bill_view["lines"]:
        line["description"] = _scrub(line["description"], secrets)

    eob_view = None
    if eob is not None:
        eob_view = eob.model_dump(mode="json")
        eob_view["member_id"] = "[MEMBER_ID]" if eob.member_id == bill.patient.member_id else "[EOB_MEMBER_ID]"
        eob_view["patient_name"] = "[PATIENT_NAME]" if eob.patient_name == bill.patient.name else "[EOB_PATIENT_NAME]"
        for line in eob_view["lines"]:
            line["remark"] = _scrub(line["remark"], secrets)

    return Redacted(bill=bill_view, eob=eob_view, mapping=secrets)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put the original values back into text that contains placeholders (done locally, after the model)."""
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text
