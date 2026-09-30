from app.models.proposal import ProposalDraft, ProposalSection, VoiceFinding
from app.services.proposal_presubmit_review import _scan_voice


def _draft(content, findings=()):
    return ProposalDraft(
        rfpId="r1",
        updatedAt="2026-09-30T00:00:00Z",
        sections=[ProposalSection(id="approach", title="Approach", mode="write", content=content)],
        voiceFindings=list(findings),
    )


def test_em_dash_is_critical_and_says_rev_6():
    issues = _scan_voice(_draft("We work fast — every day of the week."))
    assert any(i.severity == "critical" and "Rev 6" in i.message and "em dash" in i.message for i in issues)


def test_needs_human_finding_is_a_warning():
    f = VoiceFinding(sectionId="approach", find="Bid that record.", rule="instruction-leak", kind="needs_human", detail="verifier: unsure")
    issues = _scan_voice(_draft("Plain text. Bid that record.", [f]))
    assert any(i.severity == "warning" and "instruction-leak" in i.message for i in issues)


def test_suggestion_is_info():
    f = VoiceFinding(sectionId="approach", find="we provide", rule="tense", kind="suggestion", detail="we'll provide")
    issues = _scan_voice(_draft("Plain text where we provide it.", [f]))
    assert any(i.severity == "info" and "tense" in i.message for i in issues)


def test_finding_whose_text_is_gone_is_ignored():
    f = VoiceFinding(sectionId="approach", find="old sentence", rule="hedge", kind="needs_human")
    assert _scan_voice(_draft("Clean text only.", [f])) == []


def test_clean_section_has_no_issues():
    assert _scan_voice(_draft("Clean text only.")) == []


def _em_issues(issues):
    return [i for i in issues if "em dash" in i.message]


def test_em_dash_only_on_tag_and_heading_lines_is_not_flagged():
    body = (
        "## Heading — with dash\n"
        "[MANUAL FILL: fill — this]\n"
        "  [VERIFY: a — b]\n"
        "[DESIGNER NOTE: x — y]\n"
        "Plain sentence."
    )
    assert _em_issues(_scan_voice(_draft(body))) == []


def test_tag_line_plus_body_em_dash_gives_one_critical_issue():
    body = "[MANUAL FILL: fill — this]\nWe work fast — every day.\nAnother — one."
    issues = _em_issues(_scan_voice(_draft(body)))
    assert len(issues) == 1 and issues[0].severity == "critical"


def test_em_dash_in_markdown_table_row_is_flagged():
    body = "| Phase | Note |\n| --- | --- |\n| One | fast — cheap |"
    issues = _em_issues(_scan_voice(_draft(body)))
    assert len(issues) == 1 and issues[0].severity == "critical"
