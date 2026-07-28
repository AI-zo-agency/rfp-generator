"""Unit tests for memory-first + chunk gap-fill merge."""

from __future__ import annotations

import unittest

from app.services.supermemory import merge_memory_and_chunk_hits


class MergeMemoryAndChunkHitsTests(unittest.TestCase):
    def test_chunk_fills_doc_missing_from_memories(self) -> None:
        memories = [
            {
                "id": "m1",
                "memory": "Torrent Labs is a client of zö agency.",
                "metadata": {"fileName": "02_MasterTemplate_OrgStructure", "type": "knowledge_base"},
                "similarity": 0.8,
            }
        ]
        chunks = [
            {
                "id": "c1",
                "chunk": "# KPIs\n- Refined brand positioning",
                "metadata": {"fileName": "03_CS_TorrentLaboratories.pdf", "type": "knowledge_base"},
                "similarity": 0.7,
            }
        ]
        merged = merge_memory_and_chunk_hits(memories, chunks)
        names = [
            (h.get("metadata") or {}).get("fileName")
            for h in merged
        ]
        self.assertIn("02_MasterTemplate_OrgStructure", names)
        self.assertIn("03_CS_TorrentLaboratories.pdf", names)
        torrent = next(
            h for h in merged if (h.get("metadata") or {}).get("fileName") == "03_CS_TorrentLaboratories.pdf"
        )
        self.assertEqual(torrent.get("_retrieval_mode"), "documents")

    def test_same_doc_enriches_memory_with_chunk_text(self) -> None:
        memories = [
            {
                "id": "m1",
                "memory": "Torrent Laboratory brand work.",
                "metadata": {"fileName": "03_CS_TorrentLaboratories.pdf", "type": "knowledge_base"},
                "similarity": 0.9,
            }
        ]
        chunks = [
            {
                "id": "c1",
                "chunk": "# KPIs\n- Increased newsletter open rates",
                "metadata": {"fileName": "03_CS_TorrentLaboratories.pdf", "type": "knowledge_base"},
                "similarity": 0.7,
            }
        ]
        merged = merge_memory_and_chunk_hits(memories, chunks)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].get("_enriched_from_chunks"))
        self.assertIn("KPIs", merged[0].get("chunk") or "")


if __name__ == "__main__":
    unittest.main()
