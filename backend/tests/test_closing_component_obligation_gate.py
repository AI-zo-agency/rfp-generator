"""Closing sections must require a submission obligation, not a topic mention.

Real symptom this guards: a 12-page-capped RFP produced sections for
"Acknowledgement of Addenda", "Contract / Agreement Acknowledgment" and an
unconditional "Offeror Commitment and Closing Statement" — none of which the
RFP asked vendors to write. They matched procedural prose.
"""

from __future__ import annotations

import unittest

from app.services.proposal_closing_package import detect_closing_components


def _ids(text: str, **kw) -> set[str]:
    return {c.id for c in detect_closing_components(text, **kw)}


class ObligationGateTests(unittest.TestCase):
    def test_procedural_addenda_clause_does_not_add_a_section(self) -> None:
        # Paraphrase of the RFP 1.6 language that triggered the false positive.
        rfp = (
            "1.6 ADDENDA. The County may issue any addenda to this solicitation "
            "prior to the proposal due date. Vendors are responsible for "
            "monitoring ColoradoVSS and BidNet for addenda affecting this "
            "proposal. Addenda will be posted electronically."
        )
        self.assertNotIn("addenda_acknowledgement", _ids(rfp))

    def test_real_addenda_requirement_still_adds_the_section(self) -> None:
        rfp = (
            "All addenda must be acknowledged and returned with your proposal. "
            "Failure to return signed addenda may render the quote non-responsive."
        )
        self.assertIn("addenda_acknowledgement", _ids(rfp))

    def test_procedural_contract_terms_clause_does_not_add_a_section(self) -> None:
        rfp = (
            "1.7 CONTRACT TERMS. The initial term is one year with two optional "
            "extensions. Submission of a quote constitutes acceptance of the "
            "terms and conditions stated herein. The County may cancel with "
            "thirty days notice."
        )
        self.assertNotIn("exemplar_agreement", _ids(rfp))

    def test_commitment_section_is_not_added_unconditionally(self) -> None:
        rfp = "Section IV Documentation. Provide a company overview and pricing."
        self.assertNotIn("offeror_commitment", _ids(rfp))

    def test_empty_rfp_text_adds_nothing(self) -> None:
        self.assertEqual(_ids(""), set())

    def test_commitment_still_available_when_caller_opts_in(self) -> None:
        ids = _ids("Provide a company overview.", always_include_commitment=True)
        self.assertIn("offeror_commitment", ids)

    def test_genuine_reference_requirement_survives(self) -> None:
        rfp = (
            "Offerors must submit three client references from like institutions, "
            "including contact name, telephone and email, with the proposal."
        )
        self.assertIn("references", _ids(rfp))

    def test_genuine_pricing_form_requirement_survives(self) -> None:
        rfp = (
            "The Pricing Proposal Form must be completed and returned with your "
            "quote. Provide hourly, monthly and annual blended rates."
        )
        self.assertIn("pricing_form", _ids(rfp))



class InjectionSiteTests(unittest.TestCase):
    """The unit gate is useless if injection sites override it.

    detect_closing_components defaulting to False did not fix anything on its
    own: ensure_closing_sections and the outline dedup pass both passed
    always_include_commitment=True explicitly, so the unrequested closing
    section was still added to real drafts.
    """

    def test_no_injection_site_forces_the_commitment_section(self) -> None:
        import ast
        from pathlib import Path

        app_dir = Path(__file__).resolve().parents[1] / "app"
        offenders: list[str] = []

        for path in app_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name != "detect_closing_components":
                    continue
                for kw in node.keywords:
                    if kw.arg != "always_include_commitment":
                        continue
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        offenders.append(
                            f"{path.relative_to(app_dir.parent)}:{node.lineno}"
                        )

        self.assertEqual(
            offenders,
            [],
            msg=(
                "always_include_commitment=True at "
                + ", ".join(offenders)
                + " — this re-adds a section no RFP asked for"
            ),
        )



class AttachmentChecklistScopeTests(unittest.TestCase):
    """The attachments tab must not read as a second Insurance Information.

    Section 1.5 already states zö's coverage. A closing tab titled "Insurance
    Certificates & Required Attachments" reads as a duplicate of it, and its
    instructions never said not to restate limits and carriers. Its real job is
    a checklist of documents to RETURN.
    """

    RFP = (
        "Submit a certificate of insurance naming the County as additional "
        "insured, a completed W-9, and Exhibit A with your proposal."
    )

    def _component(self):
        comps = detect_closing_components(self.RFP)
        return next((c for c in comps if c.id == "insurance_attachments"), None)

    def test_component_is_still_detected(self) -> None:
        self.assertIsNotNone(self._component())

    def test_title_no_longer_leads_with_insurance(self) -> None:
        title = self._component().title
        self.assertNotIn("Insurance Certificates", title)
        self.assertIn("Attachments", title)

    def test_instructions_forbid_restating_section_1_5(self) -> None:
        instructions = self._component().draft_instructions
        self.assertIn("1.5", instructions)
        self.assertIn("do NOT restate", instructions)
        for banned in ("limits", "carriers"):
            self.assertIn(banned, instructions)

    def test_instructions_ask_for_a_checklist_not_prose(self) -> None:
        instructions = self._component().draft_instructions
        self.assertIn("CHECKLIST", instructions)
        self.assertIn("W-9", instructions)


if __name__ == "__main__":
    unittest.main()
