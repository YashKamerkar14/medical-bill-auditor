"""The full system (Milestone 3) with a scripted fake model: no network, no quota.

The fake model behaves like a careful one by default: it pairs lines by the
words in descriptions and remarks, grades the first passage relevant, and
quotes the first ten words of a passage exactly. Variants script the failure
paths: no relevant passage (rewrite, then insufficient evidence), a misquote
(regenerated once), and a model that always misquotes (explanation dropped).
"""

import re
from datetime import date

import pytest

from bill_auditor.agent.ai_match import MatchAnswer, ProposedPair, model_match
from bill_auditor.agent.explain import Grade, Rewrite, Written
from bill_auditor.agent.graph import STEP_BUDGET, build_graph, run_audit
from bill_auditor.agent.tracing import Tracer
from bill_auditor.checks.matching import match_lines
from bill_auditor.db import connect
from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.guardrails.quotes import normalise, quote_in_text
from bill_auditor.ingest.index_manual import index_manual, open_chroma
from bill_auditor.ingest.releases import ReleaseSpec
from bill_auditor.llm.gemini import StructuredResult
from bill_auditor.report.report import render_markdown
from fake_manual import FAKE_CHAPTER
from test_manual_search import HashingEmbedder

CASES = {c.case_id: c for c in build_hard_cases()}
WORDS = ("right", "left", "chest", "abdomen")


class FakeLLM:
    model = "fake-model"

    def __init__(self, grade="first", write="exact", match="text"):
        self.grade_mode, self.write_mode, self.match_mode = grade, write, match
        self.calls = []

    def structured(self, system, user, schema):
        self.calls.append(schema.__name__)
        if schema is MatchAnswer:
            return self._result(self._match(user))
        if schema is Grade:
            ids = re.findall(r"^\[([^\]]+)\]", user, flags=re.M)
            return self._result(Grade(relevant_passage_ids=ids[:1] if self.grade_mode == "first" else []))
        if schema is Rewrite:
            return self._result(Rewrite(query="procedure reporting rule"))
        if schema is Written:
            return self._result(self._write(user))
        raise AssertionError(schema)

    def _result(self, parsed):
        return StructuredResult(parsed, parsed.model_dump_json(), {"input_tokens": 1, "output_tokens": 1})

    def _match(self, user):
        import json
        data = json.loads(user.split("\n\n", 1)[1])
        pairs = []
        for b in data["bill_lines"]:
            word = next((w for w in WORDS if w in b["description"].lower()), None)
            hit = next((e["line_no"] for e in data["eob_lines"] if word and word in e["remark"].lower()), None)
            pairs.append(ProposedPair(bill_line=b["line_no"], eob_line=hit if self.match_mode == "text" else 1))
        return MatchAnswer(pairs=pairs)

    def _write(self, user):
        pid, text = re.search(r"^\[([^\]]+)\] \(section [^)]*\)\n(.+)$", user, flags=re.M).groups()
        quote = " ".join(text.split()[:10])
        bad = self.write_mode == "always_bad" or (self.write_mode == "bad_then_good" and "rejected" not in user)
        if bad:
            quote = "This sentence does not appear anywhere in the manual at all."
        return Written(explanation="This may be a billing error to verify under the manual's rule.",
                       quotes=[{"passage_id": pid, "text": quote}])


@pytest.fixture
def manual_index(audit_db_path, tmp_path, monkeypatch):
    """Index the fake chapter IX into the audit database and a temporary Chroma."""
    pdf = tmp_path / "09-fake.pdf"
    pdf.write_bytes(b"fake")
    conn = connect(audit_db_path)
    chroma = open_chroma(tmp_path / "chroma")
    index_manual(conn, chroma, {"09": pdf}, ReleaseSpec("manual", "Manual (test)", date(2026, 1, 1), date(2026, 12, 31)),
                 HashingEmbedder(), read_pages=lambda _: FAKE_CHAPTER)
    conn.close()
    monkeypatch.setattr("bill_auditor.retrieval.hybrid.MIN_TOP_SIMILARITY", -1.0)  # hashing vectors are not calibrated
    return chroma


def full(audit_db_path, manual_index, llm):
    return build_graph(audit_db_path, llm=llm, chroma=manual_index, embedder=HashingEmbedder())


# --- quotes --------------------------------------------------------------------------

def test_quote_normalisation_tolerates_only_cosmetic_differences():
    source = "A CCMI of “0” indicates that NCCI PTP-associated modifiers cannot be used to bypass the edit."
    assert quote_in_text('A CCMI of "0" indicates that NCCI PTPassociated  modifiers cannot be used', source)
    assert quote_in_text("a ccmi of “0” indicates ... cannot be used to bypass the edit", source)
    assert not quote_in_text("A CCMI of 1 indicates that NCCI PTP-associated modifiers cannot be used", source)
    assert not quote_in_text("bypass the edit", source)  # too short to count as evidence
    assert normalise("ﬁle") == "file"  # PDF ligature


# --- model-assisted matching ----------------------------------------------------------

def test_model_pairs_ambiguous_lines_by_their_text():
    case = CASES["hard-m09"]
    matches = model_match(case.bill, case.eob, match_lines(case.bill, case.eob), FakeLLM())
    assert [(m.bill_line, m.eob_line, m.method) for m in matches] == [(1, 2, "model"), (2, 1, "model")]


def test_code_rejects_model_pairs_that_do_not_validate():
    case = CASES["hard-m09"]
    matches = model_match(case.bill, case.eob, match_lines(case.bill, case.eob), FakeLLM(match="always_line_1"))
    # both lines proposed EOB line 1: only the first can have it, and only once
    assert [m.method for m in matches] == ["model", "ambiguous"]


def test_model_saying_cannot_tell_leaves_lines_ambiguous():
    case = CASES["hard-m12"]
    matches = model_match(case.bill, case.eob, match_lines(case.bill, case.eob), FakeLLM())
    assert [m.method for m in matches] == ["ambiguous", "ambiguous"]


def test_matching_prompt_is_redacted():
    case = CASES["hard-m11"]
    llm = FakeLLM()
    captured = []
    original = llm.structured
    llm.structured = lambda system, user, schema: (captured.append(user), original(system, user, schema))[1]
    model_match(case.bill, case.eob, match_lines(case.bill, case.eob), llm)
    assert case.bill.patient.name not in captured[0] and case.bill.patient.member_id not in captured[0]


# --- the whole graph ---------------------------------------------------------------------

def test_full_system_solves_the_ambiguous_cases(audit_db_path, manual_index):
    graph = full(audit_db_path, manual_index, FakeLLM())
    for cid, expected in (("hard-m09", [((1,), 9000)]), ("hard-m11", [((2,), 30000)]), ("hard-m10", [])):
        report = run_audit(graph, CASES[cid].bill, CASES[cid].eob).report
        assert [(f.bill_lines, f.amount_cents) for f in report.errors] == expected, cid


def test_flags_are_explained_with_verified_quotes(audit_db_path, manual_index, tmp_path):
    tracer = Tracer(tmp_path / "t.jsonl")
    case = CASES["hard-p03"]  # two code-pair flags
    run = run_audit(full(audit_db_path, manual_index, FakeLLM()), case.bill, case.eob, tracer=tracer)
    report = run.report
    assert len(report.errors) == 2
    for flag in report.errors:
        explanation = report.explanations[flag.flag_id]
        assert explanation.status == "explained" and explanation.quotes_verified == 1 and explanation.sections
    nodes = [e["node"] for e in tracer.events]
    assert nodes.count("explain") == 2 and nodes[-2:] == ["verify_quotes", "report"]
    assert "Why (NCCI Policy Manual" in render_markdown(report)
    assert report.steps_used <= STEP_BUDGET


def test_arithmetic_and_liability_flags_are_not_sent_to_the_model(audit_db_path, manual_index):
    llm = FakeLLM()
    report = run_audit(full(audit_db_path, manual_index, llm), CASES["hard-a05"].bill).report
    assert len(report.errors) == 1 and report.explanations == {} and llm.calls == []


def test_misquote_is_regenerated_once(audit_db_path, manual_index):
    report = run_audit(full(audit_db_path, manual_index, FakeLLM(write="bad_then_good")), CASES["hard-t02"].bill).report
    (explanation,) = report.explanations.values()
    assert explanation.status == "explained" and explanation.writes == 2 and explanation.quotes_proposed == 2


def test_persistent_misquote_drops_the_explanation_but_keeps_the_flag(audit_db_path, manual_index):
    report = run_audit(full(audit_db_path, manual_index, FakeLLM(write="always_bad")), CASES["hard-t02"].bill).report
    assert len(report.errors) == 1  # the flag is proven by code and stays (owner's decision a)
    (explanation,) = report.explanations.values()
    assert explanation.status == "unverified" and explanation.text == "" and explanation.writes == 2
    assert "No verified manual passage" in render_markdown(report)


def test_no_relevant_passage_rewrites_twice_then_reports_insufficient_evidence(audit_db_path, manual_index):
    llm = FakeLLM(grade="none")
    report = run_audit(full(audit_db_path, manual_index, llm), CASES["hard-t02"].bill).report
    (explanation,) = report.explanations.values()
    assert explanation.status == "insufficient_evidence" and explanation.rewrites == 2
    assert llm.calls.count("Rewrite") == 2 and llm.calls.count("Grade") == 3 and "Written" not in llm.calls


def test_model_failure_never_loses_a_flag(audit_db_path, manual_index):
    from bill_auditor.llm.guarded import BudgetExhausted

    class Exhausted(FakeLLM):
        def structured(self, system, user, schema):
            raise BudgetExhausted("daily cap reached")

    run = run_audit(full(audit_db_path, manual_index, Exhausted()), CASES["hard-t02"].bill)
    assert len(run.report.errors) == 1
    assert list(run.report.explanations.values())[0].status == "not_explained"
    assert run.state["model_failures"]
