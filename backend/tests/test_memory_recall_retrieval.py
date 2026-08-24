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
