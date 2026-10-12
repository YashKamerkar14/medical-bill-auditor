"""What the command line and the Streamlit app share: loading a bill or EOB, and running an audit.

Two modes:
- "rules": the deterministic graph, no model, no key, no cost;
- "full": the full system with the free-tier Gemini model for ambiguous matching
  and manual-grounded explanations (needs GOOGLE_API_KEY; capped per session and
  per day; answers are cached). Only synthetic bills are accepted, and nothing
  personal is sent (redaction and the pre-send gate).
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from bill_auditor.agent.graph import AuditRun, build_graph, run_audit
from bill_auditor.config import load_paths
from bill_auditor.parsing.pdf import PdfParseError, parse_bill_pdf, parse_eob_pdf

SESSION_MAX_CALLS = 40
DAILY_CAP = 450


class InputError(ValueError):
    """The uploaded file could not be read (the message says why)."""


def load_document(name: str, data: bytes, kind: str):
    """A raw bill or EOB dict from a .json or .pdf file; validation happens at intake, like any input."""
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InputError(f"{name}: not valid JSON ({error})") from error
    if suffix == ".pdf":
        try:
            return parse_bill_pdf(data) if kind == "bill" else parse_eob_pdf(data)
        except PdfParseError as error:
            raise InputError(f"{name}: {error}") from error
    raise InputError(f"{name}: only .json and .pdf files are accepted")


@dataclass
class Auditor:
    mode: str
    graph: object
    llm: object = None
    notes: list[str] = field(default_factory=list)

    def run(self, raw_bill, raw_eob=None) -> AuditRun:
        # allow_non_synthetic is never offered: the app and the command line accept synthetic bills only.
        return run_audit(self.graph, raw_bill, raw_eob)

    @property
    def model_calls(self) -> str:
        if self.llm is None:
            return "no model used"
        return f"{self.llm.calls} new model calls, {self.llm.cache_hits} from cache ({self.llm.used_today()} today)"


def make_demo_auditor() -> Auditor:
    """The public demo: rules only, on a database built from the committed synthetic tables (no CMS data)."""
    import tempfile

    from bill_auditor.demo import build_demo_db

    db = build_demo_db(Path(tempfile.gettempdir()) / "bill-auditor-demo.sqlite")
    return Auditor("rules", build_graph(db), notes=[
        "Public demo: rules only, synthetic code-pair and unit-limit tables; the Medicare fee schedule and the "
        "model (AI matching, manual explanations) run only in a local install."])


def make_auditor(mode: str = "rules") -> Auditor:
    paths = load_paths()
    if not paths.sqlite_path.exists():
        raise InputError(f"No reference database at {paths.sqlite_path}: run `uv run python scripts/build_data.py` first.")
    if mode == "rules":
        return Auditor("rules", build_graph(paths.sqlite_path))
    if mode != "full":
        raise ValueError(f"unknown mode {mode!r}")
    if not os.environ.get("GOOGLE_API_KEY"):
        raise InputError("The full system needs GOOGLE_API_KEY in .env (Google AI Studio, free tier). "
                         "Rules-only mode needs no key.")
    from bill_auditor.ingest.index_manual import open_chroma
    from bill_auditor.llm.gemini import DEFAULT_MODEL, GeminiClient
    from bill_auditor.llm.guarded import GuardedLLM
    from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    llm = GuardedLLM(GeminiClient(model), cache_dir=paths.eval_dir / "llm_cache" / model,
                     usage_dir=paths.data_dir / "llm_usage", max_calls=SESSION_MAX_CALLS, daily_cap=DAILY_CAP,
                     min_interval_s=5)
    graph = build_graph(paths.sqlite_path, llm=llm, chroma=open_chroma(paths.chroma_dir),
                        embedder=FastEmbedEmbedder(paths.models_dir))
    return Auditor("full", graph, llm, [f"Model: {model} (free tier), at most {SESSION_MAX_CALLS} new calls per session."])
