"""Seed / refresh Supabase demo prompts from local prompt files.

Requires table from migration 20260914_demo_rfp_agent_prompts.sql.

  cd demo/rfp-two-agents
  ../../.venv/bin/python seed_prompts_supabase.py
"""

from __future__ import annotations

import sys
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = DEMO_ROOT.parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from prompt_store import PROMPT_FILES, _disk_prompt, _upsert_row, supabase_configured  # noqa: E402


def main() -> int:
    if not supabase_configured():
        print("Supabase not configured (SUPABASE_URL / SERVICE_ROLE_KEY)")
        return 1
    a1 = _disk_prompt("agent1")
    a2 = _disk_prompt("agent2")
    _upsert_row(agent1=a1, agent2=a2)
    print(
        f"Upserted demo_rfp_agent_prompts id=rfp-two-agents "
        f"(agent1={len(a1)} chars, agent2={len(a2)} chars) "
        f"from {PROMPT_FILES['agent1'].name} / {PROMPT_FILES['agent2'].name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
