# Manuscript regression fixtures

These are **sanitised synthetic drafts** that reproduce known defect classes (duplication, year inconsistency, budget fabrication, truncation, internal-note leaks). They are **not** live client PDFs or proposal exports.

Each fixture named `{name}` may include:

| File | Required | Purpose |
|------|----------|---------|
| `{name}.draft.json` | yes | `ProposalDraft`-shaped manuscript |
| `{name}.rfp.json` | yes | `RfpRecord`-shaped RFP metadata |
| `{name}.expected_findings.json` | yes | Expected finding codes (`critical` / `warning`) |
| `{name}.research.json` | no | `ProposalResearchCache` (often with `budget`) |

Load via `loader.load_fixture(name)`.
