"""Checkpoint save/load roundtrip for frozen outlines."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import checkpoint_store
import server


class CheckpointStoreTests(unittest.TestCase):
    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(checkpoint_store, "CHECKPOINTS_DIR", root):
                save = checkpoint_store.save_outline_checkpoint(
                    demo_id="rfpda-ckpt1",
                    plan={
                        "rfpId": "rfpda-ckpt1",
                        "writing": {
                            "proposalOutline": {
                                "sections": [
                                    {"id": "a", "title": "One", "order": 1, "children": []},
                                    {"id": "b", "title": "Two", "order": 2, "children": []},
                                ]
                            }
                        },
                    },
                    rfp_text="x" * 250,
                    rfp_meta={"title": "OSFM", "client": "State"},
                    sections=[
                        {"id": "a", "title": "One", "children": []},
                        {"id": "b", "title": "Two", "children": []},
                    ],
                    section_count=2,
                    run_id="run-1",
                    pdf_bytes=b"%PDF-1.4 minimal",
                    pdf_filename="osfm.pdf",
                )
                self.assertEqual(save["section_count"], 2)
                self.assertTrue((root / "rfpda-ckpt1.json").is_file())
                self.assertTrue((root / "rfpda-ckpt1.pdf").is_file())

                rows = checkpoint_store.list_checkpoints()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["demo_id"], "rfpda-ckpt1")
                self.assertEqual(rows[0]["section_count"], 2)

                loaded = checkpoint_store.load_outline_checkpoint("rfpda-ckpt1")
                self.assertEqual(loaded["section_count"], 2)
                self.assertEqual(loaded["rfp_meta"]["title"], "OSFM")
                self.assertEqual(loaded["pdf_bytes"][:4], b"%PDF")
                self.assertEqual(len(loaded["sections"]), 2)


class CheckpointLoadApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_restores_session_for_generate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(checkpoint_store, "CHECKPOINTS_DIR", root):
                checkpoint_store.save_outline_checkpoint(
                    demo_id="rfpda-ckpt2",
                    plan={
                        "rfpId": "rfpda-ckpt2",
                        "writing": {
                            "proposalOutline": {
                                "sections": [
                                    {"id": f"s{i}", "title": f"Tab {i}", "order": i, "children": []}
                                    for i in range(1, 13)
                                ]
                            },
                            "costRequirementStatus": "ambiguous",
                        },
                    },
                    rfp_text="y" * 250,
                    rfp_meta={"title": "Twelve", "client": "Buyer"},
                    sections=[
                        {"id": f"s{i}", "title": f"Tab {i}", "children": []}
                        for i in range(1, 13)
                    ],
                    section_count=12,
                    run_id="run-2",
                )
                body = server.LoadCheckpointBody(demo_id="rfpda-ckpt2")
                out = await server.checkpoints_load(body)
                self.assertEqual(out["demo_id"], "rfpda-ckpt2")
                self.assertEqual(out["output"]["sectionCount"], 12)
                self.assertEqual(len(out["output"]["sections"]), 12)
                sess = server._SESSIONS["rfpda-ckpt2"]
                self.assertTrue(sess.get("from_checkpoint"))
                self.assertFalse(sess.get("draft_ready"))
                del server._SESSIONS["rfpda-ckpt2"]


if __name__ == "__main__":
    unittest.main()
