"""Streamlit app: audit a synthetic bill, read the report, draft a letter after a human review.

    uv run streamlit run src/bill_auditor/ui/app.py

Runs on this machine only (no hosted demo: CLAUDE.md). Synthetic bills only:
there is no switch to accept a real bill. The letter step is the human-review
step the guardrails require: nothing is drafted until the person ticks the
items and confirms they checked them.
"""

import json
from pathlib import Path

import streamlit as st

from bill_auditor.config import PROJECT_ROOT
from bill_auditor.letter.letter import LetterNotAllowed, build_letter, eligible_flags
from bill_auditor.parsing.schemas import Bill
from bill_auditor.report.report import NOTICE, render_markdown
from bill_auditor.ui.core import InputError, load_document, make_auditor

EXAMPLES = PROJECT_ROOT / "examples"

st.set_page_config(page_title="Medical Bill Auditor", layout="wide")
st.title("Medical Bill Auditor")
st.warning(f"{NOTICE} Synthetic test bills only: never upload a real bill.")


@st.cache_resource(show_spinner="Loading the reference tables...")
def auditor(mode: str):
    return make_auditor(mode)


def example_choices() -> dict[str, dict]:
    index = EXAMPLES / "index.json"
    return {f"{e['case']}: {e['about']}": e for e in json.loads(index.read_text(encoding="utf-8"))} if index.exists() else {}


with st.sidebar:
    st.header("Bill")
    source = st.radio("Source", ["Example", "Upload"], horizontal=True)
    bill_raw = eob_raw = None
    error = None
    try:
        if source == "Example":
            choices = example_choices()
            if not choices:
                st.info("No examples found: run `uv run python scripts/make_examples.py`.")
            else:
                chosen = choices[st.selectbox("Example", list(choices))]
                fmt = st.radio("Format", ["pdf", "json"], horizontal=True)
                bill_file = EXAMPLES / f"{chosen['case']}-bill.{fmt}"
                bill_raw = load_document(bill_file.name, bill_file.read_bytes(), "bill")
                eob_file = EXAMPLES / f"{chosen['case']}-eob.{fmt}"
                if eob_file.exists():
                    eob_raw = load_document(eob_file.name, eob_file.read_bytes(), "eob")
                st.caption("Answer key: " + "; ".join(chosen["expected"] or ["no errors (clean bill)"]))
        else:
            bill_up = st.file_uploader("Bill (.json or .pdf)", type=["json", "pdf"])
            eob_up = st.file_uploader("EOB, if insured (.json or .pdf)", type=["json", "pdf"])
            if bill_up is not None:
                bill_raw = load_document(bill_up.name, bill_up.getvalue(), "bill")
            if eob_up is not None:
                eob_raw = load_document(eob_up.name, eob_up.getvalue(), "eob")
    except InputError as exc:
        error = str(exc)
    st.header("System")
    mode = st.radio("Mode", ["rules", "full"], format_func=lambda m: {"rules": "Rules only (no model, free)",
                    "full": "Full system (free-tier Gemini for matching and explanations)"}[m])
    run_clicked = st.button("Audit", type="primary", disabled=bill_raw is None)

if error:
    st.error(error)

if run_clicked:
    try:
        engine = auditor(mode)
        with st.spinner("Auditing..."):
            run = engine.run(bill_raw, eob_raw)
        st.session_state.update(report=run.report, bill_raw=bill_raw, calls=engine.model_calls,
                                notes=engine.notes, reviewed=False)
    except InputError as exc:
        st.error(str(exc))

report = st.session_state.get("report")
if report is not None:
    tab_report, tab_letter = st.tabs(["Report", "Draft letter"])
    with tab_report:
        for note in st.session_state.get("notes", []):
            st.caption(note)
        st.caption(st.session_state.get("calls", ""))
        if report.status == "completed":
            total = sum(f.amount_cents for f in report.errors) / 100
            c1, c2, c3 = st.columns(3)
            c1.metric("Possible errors to verify", len(report.errors))
            c2.metric("Amount in question", f"${total:,.2f}")
            c3.metric("Could not check", len(report.cannot_check))
        for warning in report.input_warnings:
            st.warning(warning)
        st.markdown(render_markdown(report))

    with tab_letter:
        if report.status != "completed":
            st.info("No letter: the bill was not audited.")
        else:
            flags = eligible_flags(report)
            st.write("A letter may use only verified, high-confidence errors. Price benchmarks and findings that need "
                     "a person to confirm them (medium confidence) are left out.")
            if not flags:
                st.info("No eligible items on this bill.")
            else:
                picked = [f.flag_id for f in flags if st.checkbox(
                    f"{f.check}, line(s) {', '.join(map(str, f.bill_lines)) or 'totals'}: ${f.amount_cents / 100:,.2f}",
                    key=f"pick-{f.flag_id}", help=f.message)]
                reviewed = st.checkbox("I checked each chosen item against my bill and EOB myself.", key="reviewed")
                if st.button("Draft letter", disabled=not (picked and reviewed)):
                    try:
                        letter = build_letter(report, Bill.model_validate(st.session_state["bill_raw"]), picked, reviewed)
                        st.session_state["letter"] = letter.text
                    except LetterNotAllowed as exc:
                        st.error(str(exc))
                if st.session_state.get("letter"):
                    text = st.text_area("Draft (edit before sending)", st.session_state["letter"], height=420)
                    st.download_button("Download draft", text, file_name=f"draft-letter-{report.bill_id}.txt")
