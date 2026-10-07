"""Generate the synthetic test set into DATA_DIR/eval/generated/ (needs the built database).

Deterministic: the same seed and tables give byte-identical cases.

    uv run python scripts/generate_cases.py
"""

from bill_auditor.config import load_paths
from bill_auditor.db import connect
from bill_auditor.evaluation.generator import SEED, generate, write_cases


def main() -> None:
    paths = load_paths()
    with connect(paths.sqlite_path) as conn:
        cases = generate(conn, SEED)
    path = write_cases(cases, paths.eval_dir / "generated", SEED)
    print(f"{len(cases)} cases (seed {SEED}) -> {path}")


if __name__ == "__main__":
    main()
