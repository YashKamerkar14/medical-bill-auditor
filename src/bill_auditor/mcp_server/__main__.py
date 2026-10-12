"""Start our MCP server over stdio: `python -m bill_auditor.mcp_server --db PATH [--chroma DIR --models DIR]`.

Paths come from arguments, not from .env, so the server process never loads
secrets (the MCP stdio client also passes it only a minimal environment).
stdout carries the protocol, so nothing else may print there; logs go to stderr.
"""

import argparse
import logging
import sys
from pathlib import Path

from bill_auditor.lookups import DirectLookups
from bill_auditor.mcp_server.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="the SQLite reference database (opened read-only)")
    parser.add_argument("--chroma", type=Path, help="the Chroma index directory; without it only keyword search works")
    parser.add_argument("--models", type=Path, help="the embedding model cache directory")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    chroma = embedder = None
    if args.chroma is not None:
        from bill_auditor.ingest.index_manual import open_chroma
        from bill_auditor.retrieval.embeddings import FastEmbedEmbedder

        chroma, embedder = open_chroma(args.chroma), FastEmbedEmbedder(args.models)
    build_server(DirectLookups(args.db, chroma, embedder)).run("stdio")


if __name__ == "__main__":
    main()
