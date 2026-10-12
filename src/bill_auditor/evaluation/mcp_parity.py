"""Milestone 5 done-when, part 1: tool outputs are byte-identical through MCP and direct calls.

The lookups compared are the ones that matter: every lookup the checks make on
every bill (recorded during a rules-only run), the Milestone 0 lookup oracles,
every labelled retrieval query in each search mode, and every manual section.
Each is made directly and through MCP; the direct result's JSON and the MCP
structured content exactly as it came off the wire are compared as canonical
JSON bytes. Resources are compared with the section text the same way.
"""

import threading
from datetime import date

from bill_auditor.mcp_client import canonical_json

ORACLE_TOOLS = {"fee_schedule": "get_fee_schedule_amount", "code_pair": "check_code_pair",
                "unit_limit": "get_unit_limit", "charge_benchmark": "get_charge_benchmark"}
METHODS = ("get_fee_schedule_amount", "check_code_pair", "get_unit_limit", "get_charge_benchmark", "search_rules",
           "get_manual_section")


class RecordingLookups:
    """Forwards every lookup and records (method, args) once, in first-seen order. Thread-safe."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple[str, tuple]] = []
        self._seen: set = set()
        self._lock = threading.Lock()

    def __getattr__(self, method):
        if method not in METHODS:
            raise AttributeError(method)

        def call(*args):
            key = (method, tuple(tuple(a) if isinstance(a, (list, tuple)) else a for a in args))
            with self._lock:
                if key not in self._seen:
                    self._seen.add(key)
                    self.calls.append(key)
            return getattr(self.inner, method)(*args)
        return call


def oracle_calls(oracles: list[dict]) -> list[tuple[str, tuple]]:
    calls = []
    for o in oracles:
        method, a = ORACLE_TOOLS[o["tool"]], dict(o["args"])
        if "state_fips" in a:
            continue  # not exposed as a tool
        day = date.fromisoformat(a["date_of_service"])
        if method == "get_fee_schedule_amount":
            calls.append((method, (a["code"], a.get("modifier"), a["locality_id"], a["setting"], day)))
        elif method == "check_code_pair":
            calls.append((method, (a["code_a"], a["code_b"], day, tuple(a.get("modifiers_a", ())),
                                   tuple(a.get("modifiers_b", ())))))
        elif method == "get_unit_limit":
            calls.append((method, (a["code"], day)))
        else:
            calls.append((method, (a["code"], a["setting"], day)))
    return calls


def search_calls(queries: list[str], modes=("keyword", "vector", "hybrid"), ks=(5,)) -> list[tuple[str, tuple]]:
    return [("search_rules", (q, mode, k)) for q in queries for mode in modes for k in ks]


def differences(a, b, path: str = "", limit: int = 10) -> list[str]:
    """Where two JSON values differ, as 'path: direct != mcp' lines (strings shortened)."""
    if type(a) is not type(b) or not isinstance(a, (dict, list)):
        return [] if a == b else [f"{path or '.'}: {str(a)[:80]!r} != {str(b)[:80]!r}"]
    found = []
    keys = sorted(set(a) | set(b)) if isinstance(a, dict) else range(max(len(a), len(b)))
    for key in keys:
        left = a.get(key) if isinstance(a, dict) else (a[key] if key < len(a) else None)
        right = b.get(key) if isinstance(b, dict) else (b[key] if key < len(b) else None)
        found += differences(left, right, f"{path}/{key}", limit)
        if len(found) >= limit:
            break
    return found[:limit]


def compare(direct, mcp, calls: list[tuple[str, tuple]]) -> dict:
    """Make each call both ways; count byte-identical results and keep the first few differences."""
    identical, mismatches, errors = 0, [], []
    for method, args in calls:
        expected = canonical_json(getattr(direct, method)(*args).model_dump(mode="json"))
        try:
            getattr(mcp, method)(*args)
        except Exception as error:
            errors.append({"call": [method, [str(a) for a in args]], "error": str(error)[:300]})
            continue
        if canonical_json(mcp.last_raw) == expected:
            identical += 1
        else:
            direct_value = getattr(direct, method)(*args).model_dump(mode="json")
            mismatches.append({"call": [method, [str(a) for a in args]],
                               "differences": differences(direct_value, mcp.last_raw)})
    return {"calls": len(calls), "identical": identical, "mismatches": mismatches, "errors": errors}


def compare_resources(direct, mcp) -> dict:
    sections = direct.section_index()
    same = sum(mcp.read_resource(f"manual://section/{s['section_id']}").encode("utf-8")
               == direct.get_manual_section(s["section_id"]).text.encode("utf-8") for s in sections)
    chapters = sorted({s["chapter"] for s in sections})
    same_chapters = sum(mcp.read_resource(f"manual://chapter/{c}").encode("utf-8") == direct.chapter_text(c).encode("utf-8")
                        for c in chapters)
    return {"sections": f"{same}/{len(sections)}", "chapters": f"{same_chapters}/{len(chapters)}"}


class ReplayLLM:
    """Cache-only model for replaying a saved run with zero new calls.

    The guarded client caches only valid answers, so a prompt whose original
    answer failed the schema is never in the cache. Replay treats such a prompt
    as that invalid answer again; if the replay then reproduces the saved run
    exactly, every other prompt (and so every tool observation in it) matched.
    """

    def __init__(self, llm):
        self.llm, self.uncached = llm, 0

    def __getattr__(self, name):
        return getattr(self.llm, name)

    def structured(self, system, user, schema):
        from bill_auditor.llm.gemini import StructuredResult
        from bill_auditor.llm.guarded import BudgetExhausted

        try:
            return self.llm.structured(system, user, schema)
        except BudgetExhausted:
            self.uncached += 1
            return StructuredResult(None, "(not in cache: replayed as the original invalid answer)", {})
