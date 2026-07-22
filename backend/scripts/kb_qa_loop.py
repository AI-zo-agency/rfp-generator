#!/usr/bin/env python3
"""
Interactive KB Q&A loop (CLI RAG).

Ask questions against the zö agency Supermemory knowledge base in a terminal loop.
Type `exit`, `quit`, or `q` to stop.

Retrieval expands each question into multiple bucket-aware queries (03_CS → 06_WON/07_FIN
Proposal → company facts), prefers agency Proposal/case-study docs over source RFP PDFs,
and packs matching snippets + relevant windows (not TOC-from-page-1).

Usage:
  cd backend && source .venv/bin/activate
  python scripts/kb_qa_loop.py

  python scripts/kb_qa_loop.py --question "By any chance do we have client name Travel Oregon" --sources
  python scripts/kb_qa_loop.py --question "..." --no-llm
  python scripts/kb_qa_loop.py -Q "Financial Stability" --sources --queries
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services import llm, supermemory
from app.services.kb_rag_retrieve import retrieve_for_question

logger = logging.getLogger("kb_qa_loop")

SYSTEM_PROMPT = """You are a QA assistant for zö agency's approved knowledge base.

Rules:
- Answer ONLY using the provided context below.
- If the answer is not in the context, say exactly: "Not found in knowledge base."
- Do NOT invent clients, degrees, certifications, insurance limits, or team details.
- Prefer facts from 03_CS case studies and *Proposal* files over source *RFP* solicitations.
- Cite source file names in parentheses when you use them.
- Keep answers concise and factual."""

EXIT_COMMANDS = {"exit", "quit", "q"}


async def retrieve_context(
    question: str,
    *,
    limit: int,
    category: str | None,
    max_chars: int,
    threshold: float,
) -> tuple[str, list[str], list[str]]:
    return await retrieve_for_question(
        question,
        limit=limit,
        max_chars=max_chars,
        category=category,
        threshold=threshold,
    )


async def answer_question(question: str, context: str) -> tuple[str, str]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Knowledge base context:\n{context}\n\n"
                f"Question: {question}\n\n"
                "Answer using only the context above."
            ),
        },
    ]
    return await llm.chat_text(messages, max_tokens=2048, temperature=0.0)


async def run_once(
    question: str,
    *,
    limit: int,
    category: str | None,
    max_chars: int,
    threshold: float,
    show_sources: bool,
    show_queries: bool,
    show_context: bool,
    context_preview: int,
    no_llm: bool,
) -> int:
    context, sources, queries = await retrieve_context(
        question,
        limit=limit,
        category=category,
        max_chars=max_chars,
        threshold=threshold,
    )

    if show_queries:
        print("\nQueries:")
        for q in queries:
            print(f"  - {q}")

    if show_sources:
        print("\nSources:")
        for src in sources or ["(none)"]:
            print(f"  - {src}")

    if show_context:
        preview = context if len(context) <= context_preview else context[:context_preview] + "\n..."
        print(f"\n--- Context ({len(context)} chars) ---\n{preview}\n---")

    if no_llm:
        if not show_context:
            print(context)
        return 0

    if not llm.is_configured():
        print("ERROR: No LLM API key configured. Use --no-llm for retrieval-only mode.")
        return 1

    if not sources:
        print("\nAssistant:\nNot found in knowledge base.")
        return 0

    answer, provider = await answer_question(question, context)
    print(f"\nAssistant ({provider}):\n{answer}")
    return 0


async def interactive_loop(args: argparse.Namespace) -> int:
    print("zö agency KB QA")
    print("Ask questions against Supermemory. Type 'exit' to quit.\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if not question:
            continue
        if question.lower() in EXIT_COMMANDS:
            print("Bye.")
            return 0

        code = await run_once(
            question,
            limit=args.limit,
            category=args.category,
            max_chars=args.max_chars,
            threshold=args.threshold,
            show_sources=args.sources,
            show_queries=args.queries,
            show_context=args.context,
            context_preview=args.context_preview,
            no_llm=args.no_llm,
        )
        if code != 0:
            return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive KB Q&A loop (CLI RAG)")
    parser.add_argument("--question", "-Q", help="Single question (skip interactive loop)")
    parser.add_argument(
        "--category",
        help="Filter Supermemory category (e.g. team_bio, company_facts)",
    )
    parser.add_argument("--limit", type=int, default=8, help="Max hits kept after ranking (default: 8)")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=80_000,
        help="Max context characters sent to LLM (default: 80000)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.35,
        help="Supermemory similarity threshold (default: 0.35; auto-retries lower if empty)",
    )
    parser.add_argument("--sources", action="store_true", help="Print source file names")
    parser.add_argument("--queries", action="store_true", help="Print expanded search queries")
    parser.add_argument("--context", action="store_true", help="Print retrieved context preview")
    parser.add_argument(
        "--context-preview",
        type=int,
        default=2000,
        help="Max chars to show with --context (default: 2000)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Retrieval only — print context, do not call LLM",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if not supermemory.is_configured():
        print("ERROR: SUPERMEMORY_API_KEY is not set in backend/.env")
        return 1

    if args.question:
        return await run_once(
            args.question,
            limit=args.limit,
            category=args.category,
            max_chars=args.max_chars,
            threshold=args.threshold,
            show_sources=args.sources,
            show_queries=args.queries,
            show_context=args.context,
            context_preview=args.context_preview,
            no_llm=args.no_llm,
        )

    return await interactive_loop(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
