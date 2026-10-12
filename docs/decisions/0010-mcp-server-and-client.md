# 0010: MCP server and client

- Status: accepted
- Date: 2026-10-11
- Milestone: 5
- Re-checks the MCP client choice left open in ADR 0001

## Context

Milestone 5 serves the evidence lookups from our own MCP server and switches the graph to an MCP client. Done when: tool outputs are byte-identical through MCP and direct calls, and evaluation scores are within run-to-run variance.

Versions re-checked on PyPI on 2026-10-11: `mcp` 2.3.0 is still the latest (released 2026-10-02); `langchain-mcp-adapters` 0.3.2 still requires `mcp<2`; `langchain.mcp` (in `langchain[mcp]` 1.4.4) is still beta and wraps MCP tools as LangChain tools.

## Decision 1: a thin client on the official SDK, not `langchain.mcp`

The client is `McpLookups` on the SDK's `mcp.Client` (ADR 0001's named fallback). `langchain.mcp` exists to hand MCP tools to a LangChain tool-calling agent; nothing here is one. The checks call lookups as ordinary functions, and the ReAct agent uses a structured-output action schema. LangChain is used nowhere in the code, so `langchain[mcp]` is removed from the dependencies. Benefits: no beta dependency, one layer fewer between the wire and our models, and the parity test compares the raw structured content.

The SDK is async and the graph is synchronous, so the client runs on a background event loop (an anyio blocking portal); parallel checks and parallel agent tool calls share one session.

## Decision 2: what goes through MCP

Through MCP: the six evidence tools (`get_fee_schedule_amount`, `check_code_pair`, `get_unit_limit`, `get_charge_benchmark`, `search_rules(query, mode, k)`, `get_manual_section`), used by the checks, the explain subgraph's retrieval and the ReAct agent, behind one `Lookups` interface with a direct and an MCP implementation. Resources: `manual://sections`, `manual://section/{id}`, `manual://chapter/{id}`.

Not through MCP, on purpose: intake (input validation) and the verify step, including quote verification. The verifier re-reads the source tables directly and never trusts the channel that produced the evidence.

## Decision 3: guardrails

- Transport: stdio only. The server is a child process with no network port, and it gets its paths as arguments plus the SDK's minimal environment, so it never loads `.env` or any secret.
- Read-only: every tool is annotated read-only, idempotent and closed-world, and the database is opened with SQLite `mode=ro`, so a bug cannot write.
- Pinning: on connect the client checks the server's name and version, that the tool list equals the six-tool allow-list, that every tool is read-only, and that a SHA-256 fingerprint of the tool list (names, descriptions, input and output schemas, annotations) equals the pinned value. A changed description, which is how tool poisoning works, or an extra tool is refused. Changing a tool means bumping `SERVER_VERSION` and the pin together.
- Timeouts: 10 s per call, enforced by the calling thread. Testing showed that the SDK's read timeout cannot interrupt a synchronous tool running in a worker thread, so the timeout is enforced by the caller. A timeout or tool error raises `LookupUnavailable`, which the checks report as "cannot check: lookup unavailable" and the explain step as insufficient evidence.
- Outputs are data: each structured result is validated back into its Pydantic model.

## Proof (done-when)

`run_eval mcp-parity` launches the server over stdio and compares canonical JSON bytes, direct vs MCP, for every lookup the checks make on all 348 bills, the lookup oracles, every retrieval query in each mode, and every manual section and chapter resource. It then runs the rules-only system, the full system and the ReAct agent through MCP with **zero new model calls allowed** (cache only). If any tool output differed, a prompt would differ, the cache would miss and the run would stop. Results: `docs/results/m5-mcp.md`.

## Consequences

- Our server can be used by any MCP client (for example the MCP Inspector), not only our graph.
- Server start-up loads the embedding model (a few seconds) once per run.
- Two lookup oracles use a state-level charge benchmark argument that is not exposed as a tool; they are skipped in the parity test.
