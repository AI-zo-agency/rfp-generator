"""Selection excerpt edit — remove/delete must allow empty replacement."""

from __future__ import annotations

import unittest

from app.services.proposal_section_quality import word_count
from app.services.proposal_section_editor import (
    _heal_selection_join_deterministic,
    _remove_heal_is_safe,
    _selection_asks_to_delete_entire_span,
    _selection_asks_to_fill_verify,
    _selection_asks_to_remove,
    _selection_join_looks_broken,
    _selection_replacement_regressed,
    _span_for_named_target,
    _splice_selection,
    _strip_named_mentions,
    _trim_replacement_boundary_overlap,
    _understand_local_edit,
)


class TrimReplacementBoundaryOverlapTests(unittest.TestCase):
    """A model that over-runs the selected span must not duplicate surrounding prose."""

    def test_over_generated_replacement_is_trimmed_to_span(self) -> None:
        sentence = (
            "We will select and configure a purpose-built DMO platform, "
            "[VERIFY: confirm preferred platform], that supports listings."
        )
        content = f"| **Tourism management software** | {sentence} |"
        sel = "We will select and configure a"
        start = content.index(sel)
        end = start + len(sel)
        # Model returned the WHOLE sentence for a few-word selection.
        trimmed = _trim_replacement_boundary_overlap(
            sentence, prefix=content[:start], suffix=content[end:]
        )
        spliced = _splice_selection(content, start=start, end=end, replacement=trimmed)
        self.assertEqual(spliced, content)
        self.assertEqual(spliced.count("purpose-built DMO platform"), 1)

    def test_prefix_overlap_is_trimmed(self) -> None:
        content = "The countywide destination plan covers the region."
        sel = "covers the region"
        start = content.index(sel)
        end = start + len(sel)
        # Model repeated the whole clause before the selection (≥ 20-char overlap).
        replacement = "The countywide destination plan spans every community"
        trimmed = _trim_replacement_boundary_overlap(
            replacement, prefix=content[:start], suffix=content[end:]
        )
        self.assertFalse(trimmed.startswith("The countywide destination plan"))
        self.assertEqual(trimmed, "spans every community")

    def test_clean_replacement_is_left_alone(self) -> None:
        content = "Our team of 20 people serves the county."
        sel = "20 people"
        start = content.index(sel)
        end = start + len(sel)
        replacement = "35 professionals"
        trimmed = _trim_replacement_boundary_overlap(
            replacement, prefix=content[:start], suffix=content[end:]
        )
        self.assertEqual(trimmed, replacement)


class SelectionRemoveTests(unittest.TestCase):
    def test_remove_phrasing_detected(self) -> None:
        self.assertTrue(_selection_asks_to_remove("remove this much part only."))
        self.assertTrue(_selection_asks_to_remove("Delete this excerpt"))
        self.assertTrue(_selection_asks_to_remove("cut this out"))
        self.assertFalse(_selection_asks_to_remove("make this warmer and clearer"))


class LocalNamedEditTests(unittest.TestCase):
    def test_remove_named_person_is_not_delete_span(self) -> None:
        edit = _understand_local_edit("remove Drew Stone")
        self.assertIsNotNone(edit)
        self.assertEqual(edit.kind, "remove_named")
        self.assertEqual(edit.target, "Drew Stone")
        self.assertFalse(
            _selection_asks_to_delete_entire_span("remove Drew Stone", excerpt="x")
        )

    def test_remove_this_excerpt_is_delete_span(self) -> None:
        edit = _understand_local_edit("remove this")
        self.assertIsNotNone(edit)
        self.assertEqual(edit.kind, "delete_span")
        self.assertTrue(
            _selection_asks_to_delete_entire_span(
                "remove this",
                excerpt="Drew Stone, Account Director.",
            )
        )

    def test_add_named_person(self) -> None:
        edit = _understand_local_edit("add Letitia Hopper to the team")
        self.assertIsNotNone(edit)
        self.assertEqual(edit.kind, "add_named")
        self.assertEqual(edit.target, "Letitia Hopper")

    def test_last_remove_instruction_wins_after_pasted_excerpt(self) -> None:
        pasted = (
            "We commit to measurable performance indicators that align "
            "directly with your campaign goals.\n\n"
            "Ron Comer delivers monthly performance reports by the 15th.\n\n"
            "Remove ron comer from this section"
        )
        edit = _understand_local_edit(pasted)
        self.assertIsNotNone(edit)
        self.assertEqual(edit.kind, "remove_named")
        self.assertEqual(edit.target.casefold(), "ron comer")

    def test_named_span_is_the_sentence_not_just_the_name(self) -> None:
        content = (
            "Ron Comer delivers monthly performance reports by the 15th. "
            "Sonja Anderson reviews every report before submission."
        )
        span = _span_for_named_target(content, "Ron Comer")
        self.assertIsNotNone(span)
        start, end = span
        chunk = content[start:end]
        self.assertIn("delivers monthly", chunk)
        self.assertNotIn("Sonja Anderson", chunk)

    def test_strip_named_mentions_deletes_every_sentence_not_just_the_name(
        self,
    ) -> None:
        content = (
            "## Performance and Outcome Indicators\n\n"
            "We commit to measurable performance indicators.\n\n"
            "| Channel | Target Impressions |\n"
            "| Digital Display Advertising | 75,000 |\n"
            "| Total Digital Impressions | 307,500 |\n\n"
            "Ron Comer delivers monthly performance reports by the 15th of each "
            "month, covering the prior month's activity. Each report includes:\n\n"
            "- Impression totals by channel with comparison to targets\n"
            "- Website performance metrics and technical updates\n\n"
            "Sonja Anderson, Agency Director, reviews every report before "
            "submission to ensure grant compliance. Ron Comer presents "
            "the closeout summary at the June 30 meeting.\n\n"
            "We attend biweekly LBHA meetings to discuss performance.\n"
        )
        before = word_count(content)
        updated, n = _strip_named_mentions(content, "Ron Comer")
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("Ron Comer", updated)
        self.assertNotIn("ron comer", updated.casefold())
        self.assertIn("Digital Display Advertising", updated)
        self.assertIn("Sonja Anderson", updated)
        self.assertIn("Each report includes:", updated)
        self.assertGreater(before - word_count(updated), 1)
        self.assertLess(word_count(updated), before)

    def test_strip_named_mentions_also_drops_first_name_and_he_owns_leftover(
        self,
    ) -> None:
        content = (
            "## Contract Management Infrastructure\n\n"
            "Ron Comer, Senior Account Manager, is assigned to this contract. "
            "He owns day-to-day execution, biweekly meeting attendance, "
            "deliverable coordination, and invoice preparation. Sonja Anderson, "
            "Agency Director, provides executive oversight for grant compliance "
            "and strategic escalation.\n\n"
            "We use Asana as our project management platform. Ron updates the "
            "project board weekly and shares access with your team.\n\n"
            "Ron reviews the timeline weekly in Asana. If a milestone slips, "
            "we escalate immediately with a mitigation plan.\n\n"
            "Ron tracks deliverable status in a master checklist. We maintain "
            "a shared Google Drive folder organized by deliverable type.\n\n"
            "Sonja reviews every invoice before submission. Ron maintains a "
            "compliance checklist in Asana that tracks spending against the "
            "$43,519 ceiling.\n\n"
            "Ron drafts reports using data from platform dashboards. Sonja "
            "reviews each report before delivery to ensure accuracy.\n"
        )
        updated, n = _strip_named_mentions(content, "Ron Comer")
        self.assertGreaterEqual(n, 2)
        folded = updated.casefold()
        self.assertNotIn("ron comer", folded)
        self.assertNotRegex(updated, r"\bRon\b")
        self.assertNotRegex(updated, r"\bHe owns\b")
        self.assertIn("Sonja Anderson", updated)
        self.assertIn("Asana", updated)
        self.assertIn("Google Drive", updated)

    def test_strip_named_mentions_cleans_he_owns_after_name_already_gone(
        self,
    ) -> None:
        content = (
            "## Contract Management Infrastructure\n\n"
            "He owns day-to-day execution, biweekly meeting attendance, "
            "deliverable coordination, and invoice preparation. Sonja Anderson, "
            "Agency Director, provides executive oversight.\n\n"
            "Ron updates the project board weekly and shares access with your team.\n"
        )
        updated, n = _strip_named_mentions(content, "Ron Comer")
        self.assertGreaterEqual(n, 1)
        self.assertNotRegex(updated, r"\bHe owns\b")
        self.assertNotRegex(updated, r"\bRon\b")
        self.assertIn("Sonja Anderson", updated)

    def test_strip_removes_any_named_thing_not_only_people(self) -> None:
        content = (
            "## Contract Management Infrastructure\n\n"
            "We use Asana as our project management platform. Every deliverable "
            "is logged as a task.\n\n"
            "Ron reviews the timeline weekly in Asana and flags at-risk work.\n\n"
            "Sonja reviews every invoice before submission to confirm grant "
            "compliance.\n"
        )
        updated, n = _strip_named_mentions(content, "Asana")
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("Asana", updated)
        self.assertIn("Sonja reviews every invoice", updated)
        self.assertIn("Every deliverable is logged as a task.", updated)

    def test_strip_asana_does_not_eat_following_he_sentence(self) -> None:
        content = (
            "We use Asana as our project management platform. "
            "He owns day-to-day execution and invoice preparation. "
            "Sonja Anderson provides executive oversight.\n"
        )
        updated, _n = _strip_named_mentions(content, "Asana")
        self.assertNotIn("Asana", updated)
        self.assertIn("He owns day-to-day execution", updated)
        self.assertIn("Sonja Anderson", updated)

    def test_strip_heading_block_for_named_subsection(self) -> None:
        content = (
            "## Timeline Monitoring\n\n"
            "Ron reviews the timeline weekly in Asana and flags any at-risk "
            "deliverables during biweekly LBHA meetings.\n\n"
            "## Deliverable Tracking\n\n"
            "Every deliverable has an Asana task with an assigned owner.\n"
        )
        updated, n = _strip_named_mentions(content, "Timeline Monitoring")
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("Timeline Monitoring", updated)
        self.assertNotIn("at-risk deliverables", updated)
        self.assertIn("Deliverable Tracking", updated)
        self.assertIn("assigned owner", updated)

    def test_title_with_and_does_not_delete_every_performance_word(self) -> None:
        content = (
            "We commit to measurable performance indicators.\n\n"
            "## Performance and Outcome Indicators\n\n"
            "Campaign performance metrics are reported monthly.\n"
        )
        updated, _n = _strip_named_mentions(
            content, "Performance and Outcome Indicators"
        )
        self.assertNotIn("## Performance and Outcome Indicators", updated)
        self.assertIn("measurable performance indicators", updated)

    def test_understand_remove_tool_or_heading(self) -> None:
        asana = _understand_local_edit("remove Asana from this section")
        self.assertIsNotNone(asana)
        self.assertEqual(asana.kind, "remove_named")
        self.assertEqual(asana.target.casefold(), "asana")
        heading = _understand_local_edit(
            "Delete Timeline Monitoring from this section"
        )
        self.assertIsNotNone(heading)
        self.assertEqual(heading.kind, "remove_named")
        self.assertEqual(heading.target.casefold(), "timeline monitoring")

    def test_named_target_span_is_that_row_not_the_whole_section(self) -> None:
        content = (
            "## Staffing\n\n"
            "- Sonja Anderson, Owner, strategy and oversight.\n"
            "- Drew Stone, Account Director, day-to-day lead.\n"
            "- Letitia Hopper, Digital Media Strategist.\n\n"
            "We staff this engagement from the Bend studio.\n"
        )
        span = _span_for_named_target(content, "Drew Stone")
        self.assertIsNotNone(span)
        start, end = span
        chunk = content[start:end]
        self.assertIn("Drew Stone", chunk)
        self.assertNotIn("Letitia Hopper", chunk)
        self.assertNotIn("Bend studio", chunk)

    def test_near_full_highlight_is_not_wiped(self) -> None:
        body = "A" * 200
        self.assertFalse(
            _selection_asks_to_delete_entire_span(
                "remove this person",
                excerpt=body,
                full_content=body,
                selection_start=0,
                selection_end=len(body),
            )
        )

    def test_empty_replacement_ok_when_removing(self) -> None:
        excerpt = "Add a section confirming compliance with KVCC submission requirements."
        self.assertTrue(
            _selection_replacement_regressed(excerpt, "", allow_remove=False)
        )
        self.assertFalse(
            _selection_replacement_regressed(excerpt, "", allow_remove=True)
        )

    def test_join_looks_broken_after_mid_opener_delete(self) -> None:
        before = ""
        after = (
            "compliance with KVCC submission requirements: proposal signed by "
            "person with authority to bind Z'Onion Creative Group LLC."
        )
        self.assertTrue(_selection_join_looks_broken(before, after))
        self.assertFalse(
            _selection_join_looks_broken(before, after[0].upper() + after[1:])
        )

    def test_deterministic_heal_capitalizes_section_start(self) -> None:
        content = (
            "Add a section confirming compliance with KVCC submission requirements: "
            "proposal signed by person with authority."
        )
        start, end = 0, len("Add a section confirming ")
        spliced = _splice_selection(content, start=start, end=end, replacement="")
        self.assertTrue(spliced.startswith("compliance "))
        healed = _heal_selection_join_deterministic(spliced, splice_at=start)
        self.assertTrue(healed.startswith("Compliance "))
        self.assertIn("submission requirements", healed)

    def test_deterministic_heal_capitalizes_after_paragraph_break(self) -> None:
        content = "Intro paragraph.\n\nAdd a section confirming compliance with rules."
        start = content.index("Add a section confirming ")
        end = start + len("Add a section confirming ")
        spliced = _splice_selection(content, start=start, end=end, replacement="")
        healed = _heal_selection_join_deterministic(spliced, splice_at=start)
        self.assertIn("\n\nCompliance with rules.", healed)

    def test_remove_heal_safety_rejects_balloon(self) -> None:
        spliced = "Compliance with KVCC submission requirements."
        balloon = spliced + (" extra claim." * 40)
        self.assertFalse(_remove_heal_is_safe(spliced=spliced, healed=balloon))
        self.assertTrue(
            _remove_heal_is_safe(
                spliced=spliced,
                healed="Compliance with KVCC submission requirements is confirmed.",
            )
        )


class SelectionVerifyFillTests(unittest.TestCase):
    def test_fill_verify_phrasing_detected(self) -> None:
        self.assertTrue(
            _selection_asks_to_fill_verify("fill missing verify tags in insurance")
        )
        self.assertTrue(
            _selection_asks_to_fill_verify(
                "Fill in the missing [VERIFY] tags with KB facts"
            )
        )
        self.assertFalse(
            _selection_asks_to_fill_verify("make this paragraph warmer")
        )

    def test_verify_fill_does_not_regress_when_prose_preserved(self) -> None:
        excerpt = (
            "We carry commercial general liability with limits of "
            "[VERIFY: CGL limit amount] per occurrence."
        )
        replacement = (
            "We carry commercial general liability with limits of "
            "$1,000,000 per occurrence."
        )
        self.assertFalse(
            _selection_replacement_regressed(
                excerpt, replacement, allow_verify_fill=True
            )
        )

    def test_verify_fill_still_rejects_truncated_span(self) -> None:
        excerpt = (
            "Insurance Information\n\n"
            "We maintain coverage through Next Insurance. "
            "General liability: [VERIFY: CGL limit]. "
            "Workers compensation: [VERIFY: WC confirmation]. "
            "Certificates available on request."
        )
        # Model returned only the filled value — must still reject.
        self.assertTrue(
            _selection_replacement_regressed(
                excerpt, "$1,000,000", allow_verify_fill=True
            )
        )


if __name__ == "__main__":
    unittest.main()
