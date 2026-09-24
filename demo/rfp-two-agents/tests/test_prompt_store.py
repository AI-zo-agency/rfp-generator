"""prompt_store disk path (no Supabase)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prompt_store as ps


class PromptStoreDiskTests(unittest.TestCase):
    def test_save_and_load_disk_when_supabase_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f1 = root / "a1.txt"
            f2 = root / "a2.txt"
            f1.write_text("hello agent1\n", encoding="utf-8")
            f2.write_text("hello agent2\n", encoding="utf-8")
            with (
                patch.object(ps, "PROMPT_FILES", {"agent1": f1, "agent2": f2}),
                patch.object(ps, "supabase_configured", return_value=False),
            ):
                saved = ps.save_prompts(agent1="updated one", agent2=None)
                self.assertEqual(saved["source"], "disk")
                self.assertEqual(saved["agent1"], "updated one")
                self.assertEqual(saved["agent2"], "hello agent2")
                self.assertEqual(f1.read_text(encoding="utf-8").strip(), "updated one")
                body, source = ps.load_prompt("agent1")
                self.assertEqual(body, "updated one")
                self.assertEqual(source, "disk")


if __name__ == "__main__":
    unittest.main()
