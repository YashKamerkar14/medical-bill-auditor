"""The hosted public demo: built only from committed files, accepts no uploads, gives the right answers."""

import json

from streamlit.testing.v1 import AppTest

from bill_auditor.agent.graph import build_graph, run_audit
from bill_auditor.config import PROJECT_ROOT
from bill_auditor.db import connect
from bill_auditor.demo import build_demo_db, is_demo_db

EXAMPLES = PROJECT_ROOT / "examples"


def test_demo_db_has_only_synthetic_rules_and_labelled_empty_cms_releases(tmp_path):
    db = build_demo_db(tmp_path / "demo.sqlite")
    conn = connect(db)
    assert is_demo_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM pfs_amounts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM charge_benchmarks").fetchone()[0] == 0
    labels = {r["dataset"]: r["label"] for r in conn.execute("SELECT dataset, label FROM releases")}
    assert labels["pfs"].startswith("DEMO ONLY") and labels["ptp"].startswith("SYNTHETIC")
    conn.close()


def test_every_example_gives_its_answer_key_in_the_demo(tmp_path):
    graph = build_graph(build_demo_db(tmp_path / "demo.sqlite"))
    for example in json.loads((EXAMPLES / "index.json").read_text(encoding="utf-8")):
        bill = json.loads((EXAMPLES / f"{example['case']}-bill.json").read_text(encoding="utf-8"))
        eob_file = EXAMPLES / f"{example['case']}-eob.json"
        eob = json.loads(eob_file.read_text(encoding="utf-8")) if eob_file.exists() else None
        report = run_audit(graph, bill, eob).report
        got = [f"{f.check} on lines {list(f.bill_lines)}: ${f.amount_cents / 100:,.2f}" for f in report.errors]
        expected = [e for e in example["expected"] if not e.startswith("cannot check")]
        if example["case"] == "hard-m09":  # needs the model to pair the lines: rules only declines, never guesses
            assert got == [] and report.cannot_check
        else:
            assert got == expected, example["case"]


def test_demo_app_has_no_upload_and_no_model():
    at = AppTest.from_file(str(PROJECT_ROOT / "demo" / "streamlit_app.py"), default_timeout=120).run()
    assert not at.exception
    assert len(at.get("file_uploader")) == 0
    assert [r.label for r in at.sidebar.radio] == ["Format"]  # no "Source" (upload) and no "Mode" (model) choice
    at.sidebar.selectbox[0].select(next(o for o in at.sidebar.selectbox[0].options if o.startswith("hard-t02"))).run()
    at.sidebar.button[0].click().run()
    assert [(m.label, m.value) for m in at.metric][:2] == [("Possible errors to verify", "1"), ("Amount in question", "$500.00")]
