"""Primary contact lock ignores secondary/backup hierarchy labels."""

from __future__ import annotations

import unittest

from app.models.proposal import (
    ManuscriptLocks,
    ProposalDraft,
    ProposalResearchCache,
    ProposalSection,
)
from app.services.proposal_manuscript_locks import scan_manuscript_lock_issues


class ContactLockSecondaryTests(unittest.TestCase):
    def test_secondary_backup_names_not_flagged(self) -> None:
        locks = ManuscriptLocks(
            primaryContactName="Ron Comer",
            primaryContactTitle="Senior Account Manager",
            executiveSponsorName="Sonja Anderson",
        )
        draft = ProposalDraft(
            rfpId="r1",
            updatedAt="t",
            sections=[
                ProposalSection(
                    id="team",
                    title="Assigned Team",
                    content=(
                        "Primary contact: Ron Comer, Senior Account Manager. "
                        "Secondary contact: Sonja Anderson. "
                        "Backup support for Ron Comer: Haley Neff."
                    ),
                    status="generated",
                )
            ],
        )
        research = ProposalResearchCache(
            rfpId="r1",
            updatedAt="t",
            manuscriptLocks=locks,
        )
        issues = scan_manuscript_lock_issues(draft=draft, research=research)
        contact = [i for i in issues if "Primary contact lock" in (i.message or "")]
        self.assertEqual(contact, [], msg=contact)


if __name__ == "__main__":
    unittest.main()
