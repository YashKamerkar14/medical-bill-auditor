"""Milestone 7: the Streamlit app and the command line on the committed examples (real tables, no model)."""

import pytest
from streamlit.testing.v1 import AppTest

from bill_auditor.audit import main as audit_main
from bill_auditor.config import PROJECT_ROOT

pytestmark = pytest.mark.acceptance
EXAMPLES = PROJECT_ROOT / "examples"


def test_app_audits_an_example_and_drafts_a_letter_only_after_review(paths):
    at = AppTest.from_file(str(PROJECT_ROOT / "src" / "bill_auditor" / "ui" / "app.py"), default_timeout=120).run()
    assert not at.exception
    at.sidebar.selectbox[0].select(next(o for o in at.sidebar.selectbox[0].options if o.startswith("hard-t02"))).run()
    at.sidebar.button[0].click().run()
    assert [(m.label, m.value) for m in at.metric][:2] == [("Possible errors to verify", "1"), ("Amount in question", "$500.00")]
    draft = next(b for b in at.button if b.label == "Draft letter")
    assert draft.disabled  # nothing chosen, nothing reviewed
    at.checkbox[0].check().run()
    assert next(b for b in at.button if b.label == "Draft letter").disabled  # chosen but not reviewed
    at.checkbox(key="reviewed").check().run()
    next(b for b in at.button if b.label == "Draft letter").click().run()
    assert at.text_area[0].value.startswith("DRAFT - REVIEW BEFORE SENDING") and not at.exception


def test_cli_reads_pdf_and_refuses_an_unreviewed_letter(paths, tmp_path, capsys):
    assert audit_main([str(EXAMPLES / "hard-m01-bill.pdf"), "--eob", str(EXAMPLES / "hard-m01-eob.pdf")]) == 0
    assert "provider_liability" in capsys.readouterr().out
    letter = tmp_path / "letter.txt"
    assert audit_main([str(EXAMPLES / "hard-t02-bill.json"), "--letter", str(letter)]) == 1 and not letter.exists()
    assert audit_main([str(EXAMPLES / "hard-t02-bill.json"), "--letter", str(letter), "--i-have-reviewed"]) == 0
    assert letter.read_text(encoding="utf-8").startswith("DRAFT")


def test_cli_rejects_an_out_of_scope_and_a_non_pdf_file(paths, tmp_path, capsys):
    assert audit_main([str(EXAMPLES / "hard-c01-bill.json")]) == 0
    assert "out of scope" in capsys.readouterr().out
    bad = tmp_path / "bill.txt"
    bad.write_text("hello")
    assert audit_main([str(bad)]) == 2
