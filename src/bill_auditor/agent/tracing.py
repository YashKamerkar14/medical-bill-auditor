"""Local tracing: one JSON line per node update, written to a file on this machine.

Free and private (no tracing service). Traces record WHAT each node produced
(flag ids, rules, amounts, counts, reasons, timings), never the bill itself, so
no personal details can end up in them (CLAUDE.md, Operations).
"""

import json
import time
from pathlib import Path


def _summarise(update: dict) -> dict:
    """A trace-safe summary of one node's output."""
    out = {}
    for key, value in update.items():
        if key == "intake":
            out[key] = {"accepted": value.accepted, "in_scope": value.in_scope, "table_checks": value.table_checks,
                        "errors": value.errors, "releases": value.releases}
        elif key == "matches":
            out[key] = [(m.bill_line, m.eob_line, m.method) for m in value]
        elif key in ("candidate_flags", "verified_flags"):
            out[key] = [{"id": f.flag_id, "rule": f.rule, "amount_cents": f.amount_cents, "confidence": f.confidence}
                        for f in value]
        elif key == "dropped_flags":
            out[key] = [{"id": d.flag.flag_id, "reason": d.reason} for d in value]
        elif key == "cannot_check":
            out[key] = [{"check": c.check, "target": c.target, "lines": list(c.bill_lines), "reason": c.reason}
                        for c in value]
        elif key == "report":
            out[key] = {"status": value.status, "errors": len(value.errors), "benchmarks": len(value.benchmarks),
                        "cannot_check": len(value.cannot_check), "dropped": len(value.dropped)}
        elif key == "explanations":
            out[key] = {fid: {"status": e.status, "sections": e.sections, "queries": e.queries, "rewrites": e.rewrites,
                              "writes": e.writes, "quotes": f"{e.quotes_verified}/{e.quotes_proposed}", "note": e.note}
                        for fid, e in value.items()}
        elif key in ("steps_used", "model_failures"):
            out[key] = value
        # raw_bill / raw_eob are never written
    return out


class Tracer:
    def __init__(self, path: Path | None):
        self.path = path
        self.events: list[dict] = []
        self._start = time.perf_counter()

    def event(self, name: str, data: dict) -> None:
        record = {"t_ms": round((time.perf_counter() - self._start) * 1000, 1), "node": name, **data}
        self.events.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")

    def run(self, graph, inputs: dict, config: dict) -> dict:
        """Stream the graph: record each node's update, return the final state."""
        final = None
        for mode, chunk in graph.stream(inputs, config=config, stream_mode=["updates", "values"]):
            if mode == "values":
                final = chunk
            else:
                for node, update in chunk.items():
                    self.event(node, _summarise(update or {}))
        return final
