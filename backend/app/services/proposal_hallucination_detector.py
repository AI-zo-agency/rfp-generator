"""Hallucination detection for proposal content - prevents fabricated facts."""

from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Known verified facts (from 01_companyfacts_verified)
VERIFIED_CERTIFICATIONS = {"WBENC", "WOSB"}
VERIFIED_AWARDS = {
    "Creative Excellence 2024",
    "Netty 2024", 
    "NYX 2024",
    "Vega Digital 2024",
    "Enterprising Women of the Year 2026",
    "Enterprising Women 2026"
}
AGENCY_FOUNDED_YEAR = 2012
AGENCY_NAME_VARIANTS = {"zö agency", "zo agency", "zö", "Zö Agency", "ZO Agency"}

# Known team member names from approved bios (04_Bio_*.pdf)
# This should be populated from actual KB files, but these are common ones
APPROVED_BIO_NAMES = {
    "Sonja Anderson",
    "Rachael Rice", 
    "Ella Lindau",  # Note: Lindau not Lindeau
    "Sarah Eichhorn",
    "Nicole Anderson",
    "Drew Zimmerman",
    # Add more as bio files are confirmed
}

# Common hallucination patterns
HALLUCINATION_PATTERNS = [
    # Retention rate - explicitly not tracked
    (r"(\d+\.?\d*)\s*(year|yr)[\s-]*(average)?\s*(client\s*)?retention", 
     "Client retention rate (not formally tracked - do not cite specific numbers)"),
    
    # Fabricated experience claims
    (r"(\d+)\+?\s*years?\s+of\s+(government|municipal|state|federal)\s+experience",
     "Specific years of sector experience (verify against company facts)"),
    
    # Audience size claims (often from specific projects, not agency-wide)
    (r"served\s+(audiences?\s+of\s+)?(\d+\.?\d*)\s*(million|M)\s+residents",
     "Audience size claim (may be project-specific, not agency-wide)"),
    
    # Too many client name drops (laundry list pattern)
    (r"(City|County|State)\s+of\s+\w+[,\s]+(City|County|State)\s+of\s+\w+[,\s]+(City|County|State)\s+of\s+\w+",
     "Excessive client name-dropping (max 1-2 clients in Who We Are section)"),
    
    # Invented certifications
    (r"Google\s+Ads\s+Certif",
     "Google Ads certification (individual certs, not agency certifications)"),
    (r"Meta\s+(Ads|Blueprint)\s+Certif",
     "Meta certification (individual certs, not agency certifications)"),
    (r"Spotify\s+API\s+Certif",
     "Spotify API certification (not a verified agency certification)"),
    (r"ISO\s+\d+\s+(design|review)",
     "ISO certification (not in verified agency certifications)"),
    (r"State\s+Teaching\s+License",
     "Teaching license (individual credential, not agency certification)"),
    
    # Certifications in wrong section
    (r"(WBENC|WOSB|Women[- ]Owned|certified\s+(woman|women|minority))",
     "Certifications mentioned (should ONLY appear in section 1.4 Certifications, not in Who We Are)"),
    
    # Deferred information (explicitly forbidden)
    (r"(upon|on)\s+request",
     "Deferred information ('upon request' is forbidden - provide full details or [VERIFY])"),
    (r"(will\s+be\s+)?provided\s+separately",
     "Deferred information (provide inline or use [VERIFY])"),
    (r"Attachment\s+\d+\s+(will\s+include|contains)",
     "Unnamed attachment reference (include data inline or specific [VERIFY])"),
    (r"available\s+(through|via)\s+the\s+Bureau",
     "Deferred to Bureau (provide contact details inline)"),
    
    # Name misspellings
    (r"\bLindeau\b",
     "Name misspelling: should be 'Lindau' not 'Lindeau'"),
]


def detect_hallucinations(content: str, section_title: str = "") -> list[dict[str, Any]]:
    """
    Scan proposal content for common hallucination patterns.
    Returns list of issues found.
    """
    issues = []
    
    # Special check for "Business Information" — registration facts only
    if "business information" in section_title.lower():
        if re.search(r"##\s*who we are\b", content, re.IGNORECASE):
            issues.append({
                "type": "duplicate_subsection_content",
                "pattern": "'Who We Are' narrative in Business Information (belongs in section 1.1 only)",
                "matched_text": "Who We Are",
                "position": 0,
                "section": section_title,
                "severity": "high",
            })
        if re.search(r"why this matters for\b", content, re.IGNORECASE):
            issues.append({
                "type": "duplicate_subsection_content",
                "pattern": "Client pitch paragraph in Business Information (not a registration fact)",
                "matched_text": "Why This Matters",
                "position": 0,
                "section": section_title,
                "severity": "medium",
            })
        cert_check = re.search(
            r"(WBENC|WOSB|Women[- ]Owned|certified\s+(woman|women|minority))",
            content,
            re.IGNORECASE,
        )
        if cert_check:
            issues.append({
                "type": "certification_in_wrong_section",
                "pattern": "Certifications in Business Information (should ONLY be in section 1.4)",
                "matched_text": cert_check.group(0),
                "position": cert_check.start(),
                "section": section_title,
                "severity": "high",
            })
        if re.search(r"\bawards?\s+and\s+recognition\b", content, re.IGNORECASE):
            issues.append({
                "type": "duplicate_subsection_content",
                "pattern": "Awards section in Business Information (not a registration fact)",
                "matched_text": "Awards and Recognition",
                "position": 0,
                "section": section_title,
                "severity": "medium",
            })

    # Special check for "Who We Are" section - should NOT have certifications
    if "who we are" in section_title.lower():
        cert_check = re.search(r"(WBENC|WOSB|Women[- ]Owned|certified\s+(woman|women|minority)|Google\s+Ads|Meta\s+Ads|Spotify|ISO)", 
                              content, re.IGNORECASE)
        if cert_check:
            issues.append({
                "type": "certification_in_wrong_section",
                "pattern": "Certifications in 'Who We Are' section (should ONLY be in section 1.4 Certifications)",
                "matched_text": cert_check.group(0),
                "position": cert_check.start(),
                "section": section_title,
                "severity": "high"
            })
        
        # Check for excessive client name-dropping
        client_mentions = len(re.findall(r"\b(City|County|State)\s+of\s+\w+", content, re.IGNORECASE))
        if client_mentions > 2:
            issues.append({
                "type": "excessive_client_namedropping",
                "pattern": f"Too many client names ({client_mentions} found, max 1-2 allowed in Who We Are)",
                "matched_text": f"{client_mentions} client names",
                "position": 0,
                "section": section_title,
                "severity": "high"
            })
    
    # Check hallucination patterns
    for pattern, description in HALLUCINATION_PATTERNS:
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            issues.append({
                "type": "hallucination",
                "pattern": description,
                "matched_text": match.group(0),
                "position": match.start(),
                "section": section_title,
                "severity": "high"
            })
    
    # Check for unverified certifications (anything not in VERIFIED_CERTIFICATIONS)
    cert_pattern = r"certif(?:ied|ication)s?:?\s+([A-Z][A-Za-z\s&,]+?)(?:\.|,|;|\n|$)"
    cert_matches = re.finditer(cert_pattern, content, re.IGNORECASE)
    for match in cert_matches:
        cert_text = match.group(1).strip()
        # Check if this cert is in our verified list
        if not any(verified in cert_text for verified in VERIFIED_CERTIFICATIONS):
            # Also check if it's one of the known platform certs we flag
            is_flagged_platform = any(
                pattern_text in cert_text.lower() 
                for pattern_text in ["google ads", "meta", "spotify", "iso", "teaching"]
            )
            if is_flagged_platform or cert_text:
                issues.append({
                    "type": "unverified_certification",
                    "pattern": f"Certification not in verified list: {cert_text}",
                    "matched_text": match.group(0),
                    "position": match.start(),
                    "section": section_title,
                    "severity": "high"
                })
    
    # Check for team member names not in approved list
    # Look for capitalized names (rough heuristic)
    name_pattern = r"\b([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
    name_matches = re.finditer(name_pattern, content)
    for match in name_matches:
        name = match.group(1)
        # Skip if it's in our approved list
        if name in APPROVED_BIO_NAMES:
            continue
        # Skip common false positives (place names, etc.)
        if name in {"New Jersey", "New York", "North Carolina", "South Carolina", 
                    "Lake Oswego", "United States", "Maricopa County"}:
            continue
        # Skip if it's just the client name from context
        # This is a potential unapproved team member
        issues.append({
            "type": "potential_unapproved_team_member",
            "pattern": f"Team member name not in approved bio list: {name}",
            "matched_text": name,
            "position": match.start(),
            "section": section_title,
            "severity": "medium"  # Medium because it could be a client contact
        })
    
    # Check for $0 agency revenue (forbidden)
    zero_revenue_pattern = r"\$0\s*(agency\s+)?(revenue|fee|compensation)"
    zero_matches = re.finditer(zero_revenue_pattern, content, re.IGNORECASE)
    for match in zero_matches:
        issues.append({
            "type": "zero_revenue_claim",
            "pattern": "$0 agency revenue (forbidden - calculate from commission or agency_fee items)",
            "matched_text": match.group(0),
            "position": match.start(),
            "section": section_title,
            "severity": "high"
        })
    
    return issues


def format_hallucination_report(issues: list[dict[str, Any]]) -> str:
    """Format hallucination detection issues into a readable report."""
    if not issues:
        return "✅ No hallucinations detected"
    
    high_severity = [i for i in issues if i["severity"] == "high"]
    medium_severity = [i for i in issues if i["severity"] == "medium"]
    
    lines = [
        f"⚠️ HALLUCINATION DETECTION: {len(issues)} issues found",
        f"   High severity: {len(high_severity)} | Medium severity: {len(medium_severity)}",
        ""
    ]
    
    for issue in issues:
        severity_emoji = "🔴" if issue["severity"] == "high" else "🟡"
        lines.append(f"{severity_emoji} {issue['type']}: {issue['pattern']}")
        lines.append(f"   Matched: '{issue['matched_text'][:100]}'")
        if issue.get("section"):
            lines.append(f"   Section: {issue['section']}")
        lines.append("")
    
    return "\n".join(lines)


def filter_high_severity_hallucinations(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only high-severity hallucination issues."""
    return [i for i in issues if i["severity"] == "high"]
