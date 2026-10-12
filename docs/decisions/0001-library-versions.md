# 0001: Library versions

- Status: accepted
- Date: 2026-10-06
- Milestone: 0

## Context

LangGraph and MCP change quickly, and evaluation results are only comparable if the code underneath does not move. Every dependency is pinned to an exact version in `pyproject.toml`, and `uv.lock` pins every transitive dependency. Versions were taken from PyPI on 2026-10-06 and checked by installing them together and importing them on Windows (Python 3.11.16, uv-managed).

## Decision

| Package | Version | Role |
| --- | --- | --- |
| langgraph | 1.2.14 | Orchestration (`StateGraph`, `Send`, recursion limit) |
| langchain-core | 1.6.7 | Shared message/tool types used by LangGraph |
| ~~langchain[mcp]~~ | ~~1.4.3~~ | Removed in Milestone 5 (ADR 0010): the MCP client is a thin client on `mcp` |
| anyio | 4.15.1 | Runs the async MCP client from the synchronous graph (Milestone 5, ADR 0010) |
| mcp | 2.3.0 | Official MCP Python SDK; our server uses `MCPServer` |
| chromadb | 1.5.9 | Local persistent vector store |
| fastembed | 0.8.1 | Local ONNX embeddings (no API key), runs `BAAI/bge-small-en-v1.5` |
| pdfplumber | 0.11.10 | Manual PDF text extraction |
| pydantic | 2.13.5 | Schemas and validation |
| python-dotenv | 1.2.4 | Reads `.env` |
| pytest (dev) | 9.1.1 | Tests |
| hatchling (build) | 1.32.4 | Build backend |

SQLite is the one bundled with Python 3.11.16 (3.53.1), which includes FTS5.

## The MCP client choice

The plan left this open: `langchain-mcp-adapters` or `langchain.mcp`.

| Option | MCP SDK | Status |
| --- | --- | --- |
| `langchain-mcp-adapters` 0.3.2 | Requires `mcp<2.0` (latest 1.x is 1.30.0) | Stable, but tied to the previous major version |
| `langchain.mcp` in `langchain[mcp]` 1.4.3 | Uses FastMCP 4, which requires `mcp>=2,<3` | Beta: "API may change" warning on import |
| Our own thin client on `mcp.ClientSession` | 2.x | More code to write and test |

MCP 2.0 was released on 2026-07-28 and has breaking changes: `mcp.server.fastmcp.FastMCP` was renamed to `mcp.server.mcpserver.MCPServer`. Most tutorials still show the 1.x import.

**Chosen: `mcp` 2.3.0 for our server and `langchain.mcp` as the client.** The server is the part we write most code against, so it should be on the current major version, and the client must speak the same version. The beta risk is contained because the client is used in one place (the graph's tool loader). The pinned version cannot change under us, and the Milestone 5 test (byte-identical tool outputs through MCP and direct calls) catches any behaviour change on upgrade.

Fallback if `langchain.mcp` proves unworkable at Milestone 5: a thin client on `mcp.ClientSession` that wraps each tool as a LangChain `StructuredTool`. This choice is re-checked at the start of Milestone 5.

**Update (Milestone 5, ADR 0010):** re-checked; the thin client on the official SDK was chosen and `langchain[mcp]` removed, since nothing in the code is a LangChain tool-calling agent.

## Windows-specific settings

- `python-preference = "only-managed"`: uv downloads its own Python instead of using the Microsoft Store Python, which redirects AppData writes.
- `link-mode = "copy"`: hard links from uv's cache failed on this machine (Windows error 396, "cloud operation"). A failed hard-link install left two packages half-installed, which only showed up as an import error, so the environment was reinstalled from scratch.
- The virtual environment lives at `C:\dev\bill-auditor-data\.venv` (via `UV_PROJECT_ENVIRONMENT`), outside OneDrive.

## Consequences

- Upgrades are deliberate: change the pin, rerun unit tests, acceptance tests and the evaluation, and record the result here.
- Importing `langchain.mcp` prints a `LangChainBetaWarning`; this is expected.
