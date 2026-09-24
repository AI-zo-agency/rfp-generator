"""Self-check: opportunity_extract package imports + RfpDoc.from_context_text."""

from __future__ import annotations

from app.services.proposal_intelligence.opportunity_extract import load_agent_prompt
from app.services.proposal_intelligence.opportunity_extract.agent1_tools import RfpDoc


def main() -> None:
    a1 = load_agent_prompt("agent1")
    a2 = load_agent_prompt("agent2")
    assert "Opportunity Intelligence" in a1 or "opportunity" in a1.casefold()
    assert "Strategy" in a2
    marked = (
        "--- Page 1 ---\nAlpha compliance shall submit Form A.\n\n"
        "--- Page 2 ---\nBeta evaluation criteria 100 points.\n"
    )
    doc = RfpDoc.from_context_text(marked)
    assert doc.page_count >= 2
    assert "Form A" in doc.pages[0]
    plain = RfpDoc.from_context_text("x" * 8000)
    assert plain.page_count >= 2
    print("opportunity_extract self-check ok", {"pages_marked": doc.page_count, "pages_plain": plain.page_count})


if __name__ == "__main__":
    main()
