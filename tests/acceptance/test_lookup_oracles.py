"""Exact lookups against the real tables, checked against hand-written oracles.

Oracles live in DATA_DIR/eval/oracles/lookups.json (not committed: they hold
real codes and amounts). Each one was written from a source independent of our
loader, such as the CMS Physician Fee Schedule Look-Up Tool or the raw file
opened by hand, and before the table was loaded. Format:
eval/oracles/lookups.template.json.
"""

import json
from datetime import date

import pytest

from bill_auditor.config import ConfigError, load_paths
from bill_auditor.tables.charges import get_charge_benchmark
from bill_auditor.tables.code_pairs import check_code_pair
from bill_auditor.tables.fee_schedule import get_fee_schedule_amount
from bill_auditor.tables.unit_limits import get_unit_limit

pytestmark = pytest.mark.acceptance

# Oracle "tool" name -> lookup function.
LOOKUPS = {
    "fee_schedule": get_fee_schedule_amount,
    "charge_benchmark": get_charge_benchmark,
    "code_pair": check_code_pair,
    "unit_limit": get_unit_limit,
}


def _load_oracles() -> list[dict]:
    try:
        path = load_paths().eval_dir / "oracles" / "lookups.json"
    except ConfigError:
        return []
    return json.loads(path.read_text(encoding="utf-8"))["oracles"] if path.exists() else []


ORACLES = _load_oracles()


def test_ten_lookup_oracles_are_written():
    if not ORACLES:
        pytest.skip("No oracle file at DATA_DIR/eval/oracles/lookups.json")
    assert len(ORACLES) >= 10, f"Milestone 0 needs 10 lookup oracles, found {len(ORACLES)}"
    assert len({o["id"] for o in ORACLES}) == len(ORACLES), "Oracle ids must be unique"
    assert all(o.get("source") for o in ORACLES), "Every oracle must say where its expected value came from"


@pytest.mark.parametrize("oracle", ORACLES, ids=[o["id"] for o in ORACLES])
def test_lookup_matches_oracle_exactly(real_db, oracle):
    if oracle["tool"] not in LOOKUPS:
        pytest.skip(f"Lookup {oracle['tool']!r} not built yet")
    args = dict(oracle["args"])
    args["date_of_service"] = date.fromisoformat(args["date_of_service"])
    result = LOOKUPS[oracle["tool"]](real_db, **args)
    actual = {field: getattr(result, field) for field in oracle["expect"]}
    assert actual == oracle["expect"], result.message
