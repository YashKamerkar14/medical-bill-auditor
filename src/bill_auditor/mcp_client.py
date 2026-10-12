"""The graph's MCP client: `McpLookups` makes every evidence lookup through our MCP server.

A thin client on the official SDK's `mcp.Client` (docs/decisions/0010). The SDK
is async; the graph is synchronous, so the client runs on a background event
loop (an anyio "blocking portal") and each lookup is a blocking call into it.
Many threads may call at once (parallel checks, parallel agent tool calls):
requests are multiplexed over the one session.

Guardrails (CLAUDE.md, Agent loop and Operations and MCP):
- Pinned server: on connect, the server's name and version must match, and the
  fingerprint of its tool list (names, descriptions, input and output schemas,
  annotations) must equal PINNED_TOOLS_SHA256. A server whose tools changed is
  refused: this blocks "tool poisoning" or a later silent change ("rug pull").
- Read-only allow-list: only the six known tools are ever called, and every one
  must be annotated read-only.
- Timeout on every call (TOOL_TIMEOUT_S); a timeout or tool error raises
  LookupUnavailable, which callers turn into "cannot check" (never a guess).
- Tool outputs are data: each is validated back into its Pydantic model.
"""

import hashlib
import json
import sys
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date

from anyio.from_thread import start_blocking_portal
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment

from bill_auditor.config import PROJECT_ROOT
from bill_auditor.lookups import LookupUnavailable, SearchMode
from bill_auditor.mcp_server.server import SERVER_NAME, SERVER_VERSION
from bill_auditor.retrieval.models import ManualSection, SearchResult
from bill_auditor.tables.charges import ChargeBenchmarkResult
from bill_auditor.tables.code_pairs import CodePairResult
from bill_auditor.tables.fee_schedule import FeeScheduleResult
from bill_auditor.tables.unit_limits import UnitLimitResult

TOOL_TIMEOUT_S = 10
CONNECT_TIMEOUT_S = 120  # the server loads the embedding model at start
PINNED_TOOLS_SHA256 = "109244ab30ba35333e52b85dac0d33e73d2b7d341e1386de5963e1ffadc361a3"  # update consciously, with SERVER_VERSION
ALLOWED_TOOLS = frozenset({"get_fee_schedule_amount", "check_code_pair", "get_unit_limit", "get_charge_benchmark",
                           "search_rules", "get_manual_section"})


class ServerNotTrusted(RuntimeError):
    """The server is not the pinned one: different identity, tools or annotations."""


def canonical_json(data) -> bytes:
    """The bytes compared in the Milestone 5 parity test: sorted keys, no whitespace, UTF-8."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def tools_fingerprint(tools) -> str:
    """SHA-256 over everything a client relies on in the tool list."""
    items = sorted(({"name": t.name, "description": t.description, "input_schema": t.input_schema,
                     "output_schema": t.output_schema,
                     "annotations": t.annotations.model_dump(mode="json") if t.annotations else None} for t in tools),
                   key=lambda item: item["name"])
    return hashlib.sha256(canonical_json(items)).hexdigest()


def stdio_server(db_path, chroma_dir=None, models_dir=None) -> StdioServerParameters:
    """Launch our server as a child process of this Python, with a minimal environment (no secrets)."""
    args = ["-m", "bill_auditor.mcp_server", "--db", str(db_path)]
    if chroma_dir is not None:
        args += ["--chroma", str(chroma_dir), "--models", str(models_dir)]
    return StdioServerParameters(command=sys.executable, args=args, env=get_default_environment(),
                                 cwd=str(PROJECT_ROOT))


class McpLookups:
    """The `Lookups` interface over MCP. Use as a context manager, or call close()."""

    def __init__(self, server, expected_fingerprint: str = PINNED_TOOLS_SHA256, timeout_s: float = TOOL_TIMEOUT_S):
        self.timeout_s = timeout_s
        self.calls = 0
        self.last_raw = None
        self._client_cm = None
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        try:
            client_cm = self._portal.wrap_async_context_manager(Client(server, read_timeout_seconds=CONNECT_TIMEOUT_S))
            self._client = client_cm.__enter__()
            self._client_cm = client_cm  # only once it is open, so close() never exits an unopened client
            self._check_server(expected_fingerprint)
        except BaseException:
            self.close()
            raise

    def _check_server(self, expected_fingerprint: str) -> None:
        info = self._client.server_info
        if info is None or (info.name, info.version) != (SERVER_NAME, SERVER_VERSION):
            raise ServerNotTrusted(f"expected {SERVER_NAME} {SERVER_VERSION}, got {info}")
        tools = self._portal.call(self._list_tools)
        names = {t.name for t in tools}
        if names != ALLOWED_TOOLS:
            raise ServerNotTrusted(f"tool list differs from the allow-list: {sorted(names ^ ALLOWED_TOOLS)}")
        if not all(t.annotations and t.annotations.read_only_hint for t in tools):
            raise ServerNotTrusted("every tool must be annotated read-only")
        found = tools_fingerprint(tools)
        if found != expected_fingerprint:
            raise ServerNotTrusted(f"tool fingerprint {found} does not match the pinned {expected_fingerprint}")
        self.fingerprint = found

    async def _list_tools(self):
        return (await self._client.list_tools()).tools

    def close(self) -> None:
        if self._client_cm is not None:
            self._client_cm.__exit__(None, None, None)
            self._client_cm = None
        if self._portal_cm is not None:
            self._portal_cm.__exit__(None, None, None)
            self._portal_cm = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- raw access (used by the parity test) ------------------------------------------------

    def call_raw(self, tool: str, arguments: dict) -> dict:
        """The tool's structured content exactly as it came off the wire."""
        if tool not in ALLOWED_TOOLS:
            raise LookupUnavailable(f"tool {tool!r} is not on the allow-list")
        self.calls += 1
        # The timeout is enforced here, by the waiting thread, so it holds on any transport: the SDK's own read
        # timeout cannot interrupt a synchronous tool running in a worker thread (found by a Milestone 5 test).
        future = self._portal.start_task_soon(self._call, tool, arguments)
        try:
            result = future.result(timeout=self.timeout_s)
        except FutureTimeout:
            future.cancel()
            raise LookupUnavailable(f"{tool}: no answer within {self.timeout_s}s") from None
        except Exception as error:  # broken pipe, protocol error
            raise LookupUnavailable(f"{tool}: {type(error).__name__}: {error}") from error
        if result.is_error or result.structured_content is None:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise LookupUnavailable(f"{tool}: {text or 'tool error'}")
        self.last_raw = result.structured_content  # kept for the parity test (single-threaded use only)
        return result.structured_content

    async def _call(self, tool: str, arguments: dict):
        return await self._client.call_tool(tool, arguments, read_timeout_seconds=self.timeout_s)

    def read_resource(self, uri: str) -> str:
        try:
            result = self._portal.call(self._client.read_resource, uri)
        except Exception as error:
            raise LookupUnavailable(f"{uri}: {error}") from error
        return result.contents[0].text

    # --- the Lookups interface ----------------------------------------------------------------

    def get_fee_schedule_amount(self, code, modifier, locality_id, setting, date_of_service: date) -> FeeScheduleResult:
        return FeeScheduleResult.model_validate(self.call_raw("get_fee_schedule_amount", {
            "code": code, "modifier": modifier, "locality_id": locality_id, "setting": setting,
            "date_of_service": date_of_service.isoformat()}))

    def check_code_pair(self, code_a, code_b, date_of_service: date, modifiers_a=(), modifiers_b=()) -> CodePairResult:
        return CodePairResult.model_validate(self.call_raw("check_code_pair", {
            "code_a": code_a, "code_b": code_b, "date_of_service": date_of_service.isoformat(),
            "modifiers_a": list(modifiers_a), "modifiers_b": list(modifiers_b)}))

    def get_unit_limit(self, code, date_of_service: date) -> UnitLimitResult:
        return UnitLimitResult.model_validate(self.call_raw("get_unit_limit", {
            "code": code, "date_of_service": date_of_service.isoformat()}))

    def get_charge_benchmark(self, code, setting, date_of_service: date) -> ChargeBenchmarkResult:
        return ChargeBenchmarkResult.model_validate(self.call_raw("get_charge_benchmark", {
            "code": code, "setting": setting, "date_of_service": date_of_service.isoformat()}))

    def search_rules(self, query: str, mode: SearchMode = "hybrid", k: int = 5) -> SearchResult:
        return SearchResult.model_validate(self.call_raw("search_rules", {"query": query, "mode": mode, "k": k}))

    def get_manual_section(self, section_id: str) -> ManualSection:
        return ManualSection.model_validate(self.call_raw("get_manual_section", {"section_id": section_id}))
