#!/usr/bin/env python3
"""
Interactive KB Q&A loop (CLI RAG).

Ask questions against the zö agency Supermemory knowledge base in a terminal loop.
Type `exit`, `quit`, or `q` to stop.

Retrieval is fully search-driven: hybrid + document-chunk search for the question,
then each matching document is loaded in full (all chunks via v3 GET).

Usage:
  cd backend && source .venv/bin/activate
  python scripts/kb_qa_loop.py

  python scripts/kb_qa_loop.py --question "What are Rachel Rice key accounts?" --sources
  python scripts/kb_qa_loop.py --question "..." --no-llm
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
from app.services import proposal_knowledge_base_tools as kb_tools

logger = logging.getLogger("kb_qa_loop")

SYSTEM_PROMPT = """You are a QA assistant for zö agency's approved knowledge base.

Rules:
- Answer ONLY using the provided context below.
- If the answer is not in the context, say exactly: "Not found in knowledge base."
- Do NOT invent clients, degrees, certifications, insurance limits, or team details.
- Cite source file names in parentheses when you use them.
- Keep answers concise and factual."""

EXIT_COMMANDS = {"exit", "quit", "q"}


async def retrieve_context(
    question: str,
    *,
    limit: int,
    category: str | None,
    max_chars: int,
) -> tuple[str, list[str]]:
    filters: dict[str, object] | None = None
    if category:
        filters = {
            "AND": [
                *supermemory.KNOWLEDGE_BASE_SEARCH_FILTERS["AND"],
                {"key": "category", "value": category},
            ]
        }
    return await kb_tools.search_and_fetch_full(
        question,
        limit=limit,
        max_chars=max_chars,
        filters=filters,
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
    show_sources: bool,
    show_context: bool,
    context_preview: int,
    no_llm: bool,
) -> int:
    context, sources = await retrieve_context(
        question,
        limit=limit,
        category=category,
        max_chars=max_chars,
    )

    if show_sources:
        print("\nSources:")
        for src in sources or ["(none)"]:
            print(f"  - {src}")

    if show_context:
        preview = context if len(context) <= context_preview else context[:context_preview] + "\n..."
        print(f"\n--- Context ({len(context)} chars) ---\n{preview}\n---")

    if no_llm:
        print(context)
        return 0

    if not llm.is_configured():
        print("ERROR: No LLM API key configured. Use --no-llm for retrieval-only mode.")
        return 1

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
            show_sources=args.sources,
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
    parser.add_argument("--limit", type=int, default=12, help="Max search hits per mode (default: 12)")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=80_000,
        help="Max context characters sent to LLM (default: 80000)",
    )
    parser.add_argument("--sources", action="store_true", help="Print source file names")
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
            show_sources=args.sources,
            show_context=args.context,
            context_preview=args.context_preview,
            no_llm=args.no_llm,
        )

    return await interactive_loop(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
