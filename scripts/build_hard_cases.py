"""Write the hand-written hard cases to eval/hard_cases/cases.jsonl (committed).

    uv run python scripts/build_hard_cases.py
"""

from collections import Counter

from bill_auditor.config import PROJECT_ROOT
from bill_auditor.evaluation.generator import write_cases
from bill_auditor.evaluation.hard_cases import build_hard_cases

OUT_DIR = PROJECT_ROOT / "eval" / "hard_cases"


def main() -> None:
    cases = build_hard_cases()
    path = write_cases(cases, OUT_DIR)
    groups = Counter(c.tags[0].split(":")[0] for c in cases)
    print(f"{len(cases)} hard cases -> {path} ({dict(groups)})")


if __name__ == "__main__":
    main()
