"""Memories must survive retrieval as a source in their own right."""

from app.services import supermemory


def _chunk(doc_id: str, text: str) -> dict:
    return {"id": f"c-{doc_id}", "documentId": doc_id, "chunk": text, "title": doc_id}


def _memory(doc_id: str, text: str) -> dict:
    return {"id": f"m-{doc_id}", "documentId": doc_id, "memory": text, "title": doc_id}


def test_memory_survives_when_same_document_has_a_chunk() -> None:
    chunks = [_chunk("pricing-guide", "Tier 2 engagements include quarterly reviews.")]
    memories = [_memory("pricing-guide", "The billable rate for an Account Manager is $275.00")]

    merged = supermemory.merge_chunk_first_hits(memories, chunks)

    texts = " ".join(supermemory.hit_text(hit) for hit in merged)
    assert "quarterly reviews" in texts
    assert "$275.00" in texts, "memory was discarded because its document had a chunk hit"


def test_chunks_still_come_first() -> None:
    chunks = [_chunk("doc-a", "chunk text")]
    memories = [_memory("doc-b", "memory text")]

    merged = supermemory.merge_chunk_first_hits(memories, chunks)

    assert supermemory.hit_text(merged[0]) == "chunk text"


def test_duplicate_memory_text_is_dropped() -> None:
    memories = [
        _memory("doc-a", "Ron Comer has retired."),
        _memory("doc-b", "Ron Comer has retired."),
    ]

    merged = supermemory.merge_chunk_first_hits(memories, [])

    assert len(merged) == 1


def test_memory_already_contained_in_a_chunk_is_dropped() -> None:
    chunks = [_chunk("doc-a", "Rates: Account Manager $275.00 per hour, billed monthly.")]
    memories = [_memory("doc-a", "Account Manager $275.00 per hour")]

    merged = supermemory.merge_chunk_first_hits(memories, chunks)

    assert len(merged) == 1


def test_memory_for_a_document_with_no_chunk_is_kept() -> None:
    chunks = [_chunk("doc-a", "unrelated chunk")]
    memories = [_memory("doc-b", "Ron Comer has retired.")]

    merged = supermemory.merge_chunk_first_hits(memories, chunks)

    assert len(merged) == 2


import asyncio

from app.services import kb_rag_retrieve


def test_retrieve_for_question_keeps_a_memory_behind_many_chunks(monkeypatch) -> None:
    """A memory fact must reach the context even when chunks fill the rank cut."""
    chunks = [
        _chunk(f"proposal-{i}", f"Proposal {i} rate table: Account Manager $150.00 per hour. " * 40)
        for i in range(12)
    ]
    memory = _memory(
        "pricing-guide", "The billable rate for an Account Manager at zo agency is $275.00"
    )

    async def fake_chunk_first(query, *, limit, filters, threshold):
        return supermemory_merge(chunks, memory)

    def supermemory_merge(chunk_hits, memory_hit):
        from app.services import supermemory

        return supermemory.merge_chunk_first_hits([memory_hit], chunk_hits)

    async def fake_resolve(hit):
        return ""

    monkeypatch.setattr(kb_rag_retrieve, "_search_hits_chunk_first", fake_chunk_first)
    # kb_rag_retrieve imports `supermemory` locally inside each function rather than
    # at module scope, so there is no `kb_rag_retrieve.supermemory` attribute to patch.
    # Patch the real module instead — the function's local `from app.services import
    # supermemory` resolves to this same module object.
    monkeypatch.setattr(
        supermemory, "resolve_hit_document_content", fake_resolve
    )

    context, sources, _queries = asyncio.run(
        kb_rag_retrieve.retrieve_for_question("What is the billable rate for an Account Manager?")
    )

    assert "$275.00" in context, "memory fact was cut before reaching the context"


def test_memory_floor_tops_up_when_some_memories_survived(monkeypatch) -> None:
    """One surviving memory must not block the rest from being topped up.

    Regression: for a rate question, a memory from an adjacent pricing guide
    survived the rank cut and satisfied a naive "did any memory survive?" check,
    while the memory actually holding the rate card stayed cut. Ranking is
    stubbed to identity here so the cut boundary is deterministic: 7 chunks plus
    the adjacent memory fill the 8-hit cut, leaving the rate card just outside.
    """
    from app.services import supermemory as sm

    chunks = [_chunk(f"proposal-{i}", f"Proposal {i} narrative text. " * 20) for i in range(7)]
    adjacent = _memory("pricing-guide", "zo agency pricing tiers for bundled engagements")
    rate_card = _memory(
        "rate-card", "The billable rate for an Account Manager at zo agency is $275.00"
    )
    ordered = chunks + [adjacent, rate_card]

    async def fake_chunk_first(query, *, limit, filters, threshold):
        return list(ordered)

    async def fake_resolve(hit):
        return ""

    monkeypatch.setattr(kb_rag_retrieve, "_search_hits_chunk_first", fake_chunk_first)
    monkeypatch.setattr(kb_rag_retrieve, "rank_hits_for_question", lambda hits, q: list(hits))
    monkeypatch.setattr(sm, "resolve_hit_document_content", fake_resolve)

    context, _sources, _queries = asyncio.run(
        kb_rag_retrieve.retrieve_for_question(
            "What is the billable rate for an Account Manager?", limit=8
        )
    )

    assert "pricing tiers" in context, "sanity: the adjacent memory should survive the cut"
    assert "$275.00" in context, "rate-card memory was cut because one other memory survived"
