# ZO-AGENCY LLM Agent Prompting Rules

Whenever you write, update, or create a system prompt for any internal LLM agent or service in this codebase, you MUST adhere to the following strict principles to ensure tight coordination and consistency across all agents:

1. **The Research Brief is the Single Source of Truth**: All agents must be instructed to base their output strictly on the shared Research Brief/RFP context. They must not invent or contradict facts, ensuring total alignment across all generated proposal sections.
2. **Thorough RFP Compliance**: Agents must be explicitly instructed to read and fulfill *every* requirement demanded by the RFP for their specific section.
3. **No Fabrication (Anti-Hallucination)**: Agents must be explicitly told NEVER to invent missing data, clients, or metrics.
4. **No Blank Refusals**: Agents must be explicitly told NEVER to leave a section completely empty with a lazy meta-comment (e.g., "Please provide more information"). 
5. **Use [VERIFY] Tags for Gaps**: Instead of fabricating data or refusing to write, agents must intelligently draft the best possible content and use inline `[VERIFY: description of missing fact]` tags to flag gaps for human review.

These rules ensure all micro-services and LLM calls in the ZO-AGENCY backend coordinate smoothly, producing consistent, fully fleshed-out, and accurate proposal drafts.
