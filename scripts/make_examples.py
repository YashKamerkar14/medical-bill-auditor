"""Write sample bills and EOBs (JSON and PDF) to examples/ for the README, the CLI and the app.

Only hand-written hard cases are used: fake Z-codes and fake people, no CMS data,
so the files are safe to commit. Deterministic: re-running gives the same files.

    uv run python scripts/make_examples.py
"""

import json
from pathlib import Path

from bill_auditor.evaluation.hard_cases import build_hard_cases
from bill_auditor.parsing.pdf import render_bill_pdf, render_eob_pdf

EXAMPLES = {
    "hard-t02": "Synthetic code-pair edit: two codes that are not billed together (unbundling)",
    "hard-i01": "Same service billed twice, with a prompt-injection attempt in the notes",
    "hard-i02": "Units x price does not equal the charge (arithmetic)",
    "hard-m01": "Insured: the patient is billed a contractual write-off (EOB lines in a different order)",
    "hard-m09": "Insured: two identical lines told apart only by text (needs the full system to pair them)",
    "hard-c01": "Hospital (UB-04) bill: out of scope, reported as 'cannot check'",
}
OUT = Path(__file__).resolve().parents[1] / "examples"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cases = {c.case_id: c for c in build_hard_cases()}
    index = []
    for case_id, about in EXAMPLES.items():
        case = cases[case_id]
        (OUT / f"{case_id}-bill.json").write_text(json.dumps(case.bill.model_dump(mode="json"), indent=1) + "\n",
                                                  encoding="utf-8")
        (OUT / f"{case_id}-bill.pdf").write_bytes(render_bill_pdf(case.bill))
        files = [f"{case_id}-bill.json", f"{case_id}-bill.pdf"]
        if case.eob is not None:
            (OUT / f"{case_id}-eob.json").write_text(json.dumps(case.eob.model_dump(mode="json"), indent=1) + "\n",
                                                     encoding="utf-8")
            (OUT / f"{case_id}-eob.pdf").write_bytes(render_eob_pdf(case.eob))
            files += [f"{case_id}-eob.json", f"{case_id}-eob.pdf"]
        expected = [f"{e.check} on lines {list(e.bill_lines)}: ${e.amount_cents / 100:,.2f}" if e.kind == "error"
                    else f"cannot check: {e.reason}" for e in case.expected]
        index.append({"case": case_id, "about": about, "files": files, "expected": expected})
    (OUT / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {sum(len(i['files']) for i in index)} files for {len(index)} examples to {OUT}")


if __name__ == "__main__":
    main()
