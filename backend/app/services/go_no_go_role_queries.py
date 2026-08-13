"""Derive KB searches from the roles/skills an RFP actually requires.

Why this exists: Go/No-Go evidence queries were built from sector, client,
location and title only. For a municipal website RFP that means the KB was
searched for "municipal marketing case studies" but never for "web developer"
or "WordPress" — so the one genuinely relevant bio (a Web Developer with 10+
years of WordPress work) was never retrieved, while narrative-heavy bios
surfaced through generic sector queries and got cited as technical evidence.

The tool then reported capability matches from whoever happened to come back,
not from whoever could do the work.

Matching is deterministic keyword -> query. No model judgment: the RFP names a
discipline, we go look for someone in the KB who does it.
"""

from __future__ import annotations

import re

# discipline -> (RFP trigger patterns, KB search terms)
# Terms are written the way bios and resumes phrase them, not the way RFPs do.
_ROLE_LEXICON: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "web development",
        (r"\bweb\s*site\b", r"\bwebsite\b", r"\bweb\s+development\b", r"\bfront[- ]?end\b",
         r"\bhtml\b", r"\bcss\b", r"\bjavascript\b", r"\bresponsive\s+design\b",
         r"\bwebsite\s+redesign\b", r"\bweb\s+moderni"),
        "web developer front-end developer website build redesign modernization "
        "WordPress CMS 04_Bio 03_CS",
    ),
    (
        "CMS",
        (r"\bCMS\b", r"\bcontent\s+management\s+system\b", r"\bdrupal\b",
         r"\bwordpress\b", r"\bsitecore\b", r"\bcontentful\b"),
        "CMS content management system Drupal WordPress Sitecore implementation "
        "developer 04_Bio 03_CS 06_WON",
    ),
    (
        "hosting and infrastructure",
        (r"\bhosting\b", r"\bserver\b", r"\buptime\b", r"\bSLA\b",
         r"\bdisaster\s+recovery\b", r"\bbackup\b"),
        "hosting infrastructure server uptime maintenance support 04_Bio 01_companyfacts",
    ),
    (
        "content migration",
        (r"\bcontent\s+migration\b", r"\bmigrat(?:e|ion)\b.{0,30}\b(?:content|pages|site)\b"),
        "content migration site migration page migration project 03_CS",
    ),
    (
        "UX design",
        (r"\bUX\b", r"\buser\s+experience\b", r"\binformation\s+architecture\b",
         r"\bwireframe\b", r"\busability\b"),
        "UX designer user experience information architecture wireframes 04_Bio",
    ),
    (
        "accessibility",
        (r"\bWCAG\b", r"\bsection\s+508\b", r"\bADA\b", r"\bVPAT\b", r"\baccessib"),
        "WCAG accessibility Section 508 VPAT remediation audit 04_Bio 03_CS",
    ),
    (
        "cybersecurity",
        (r"\bcyber\s*security\b", r"\bpenetration\s+test", r"\bSOC\s?2\b",
         r"\bdata\s+breach\b", r"\bsecurity\s+audit\b"),
        "cybersecurity security audit penetration testing compliance 01_companyfacts",
    ),
    (
        "GIS and mapping",
        (r"\bGIS\b", r"\bgeospatial\b", r"\bmapping\s+(?:tool|service|integration)\b",
         r"\bArcGIS\b"),
        "GIS geospatial mapping integration ArcGIS 03_CS 04_Bio",
    ),
    (
        "analytics",
        (r"\bGA4\b", r"\bgoogle\s+analytics\b", r"\bgoogle\s+tag\s+manager\b",
         r"\bGTM\b", r"\bdashboard\b", r"\bUTM\b"),
        "Google Analytics GA4 tag manager reporting dashboard analytics specialist 04_Bio",
    ),
    (
        "CRM integration",
        (r"\bCRM\b", r"\bslate\b", r"\bsalesforce\b", r"\bHubSpot\b",
         r"\bAPI\s+integration\b", r"\bsingle\s+sign[- ]?on\b", r"\bSSO\b"),
        "CRM integration API single sign-on Salesforce HubSpot 04_Bio 03_CS",
    ),
    (
        "SEO",
        (r"\bSEO\b", r"\bsearch\s+engine\s+optimi", r"\borganic\s+search\b"),
        "SEO search engine optimization specialist 04_Bio 03_CS",
    ),
    (
        "paid media",
        (r"\bPPC\b", r"\bpaid\s+(?:media|search|social)\b", r"\bmedia\s+buy",
         r"\bgoogle\s+ads\b", r"\bmeta\s+ads\b"),
        "PPC paid media buyer Google Ads Meta Ads specialist 04_Bio",
    ),
    (
        "video and photography",
        (r"\bvideograph", r"\bphotograph", r"\bvideo\s+production\b", r"\bdrone\b"),
        "videographer photographer video production 04_Bio 03_CS",
    ),
    (
        "translation",
        (r"\btranslat", r"\bbilingual\b", r"\bspanish[- ]language\b", r"\bmultilingual\b"),
        "translation bilingual Spanish multilingual services partner 03_CS",
    ),
    (
        "project management",
        (r"\bproject\s+manager\b", r"\bproject\s+management\b", r"\bPMP\b",
         r"\baccount\s+manager\b"),
        "project manager account manager PMP delivery lead 04_Bio",
    ),
    (
        "copywriting",
        (r"\bcopywrit", r"\bcontent\s+writ", r"\beditorial\b", r"\bmessaging\b"),
        "copywriter content writer editorial messaging 04_Bio",
    ),
    (
        "branding",
        (r"\bbrand(?:ing)?\b", r"\blogo\b", r"\bvisual\s+identity\b", r"\bstyle\s+guide\b"),
        "branding visual identity logo style guide creative director 04_Bio 03_CS",
    ),
    (
        "social media",
        (r"\bsocial\s+media\b", r"\bcommunity\s+management\b", r"\binfluencer\b"),
        "social media manager community management content calendar 04_Bio",
    ),
    (
        "public relations",
        (r"\bpublic\s+relations\b", r"\bmedia\s+relations\b", r"\bpress\s+release\b",
         r"\bcrisis\s+communication"),
        "public relations media relations press crisis communications 04_Bio 03_CS",
    ),
    (
        "AI search",
        (r"\bAI[- ]?(?:assisted|powered)\b", r"\bartificial\s+intelligence\b",
         r"\bchatbot\b", r"\bnatural\s+language\s+search\b"),
        "AI assisted search chatbot machine learning implementation 03_CS",
    ),
    (
        "intranet",
        (r"\bintranet\b", r"\bemployee\s+portal\b", r"\bstaff\s+portal\b"),
        "intranet employee portal internal communications platform 03_CS",
    ),
)


def required_disciplines(rfp_text: str) -> list[str]:
    """Disciplines this RFP asks for, in lexicon order."""
    text = rfp_text or ""
    found: list[str] = []
    for name, patterns, _terms in _ROLE_LEXICON:
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            found.append(name)
    return found


def role_evidence_queries(rfp_text: str, *, max_queries: int = 12) -> list[str]:
    """KB searches for the people/skills this RFP requires.

    Without these the KB is only ever asked about sectors and clients, so a
    requirement for a named discipline is answered by whichever bios happen to
    rank on topical similarity — which is how a Creative Director whose own bio
    reads "Web Design/Development (Not Programming)" ends up cited as evidence
    of development capability.
    """
    text = rfp_text or ""
    queries: list[str] = []
    for name, patterns, terms in _ROLE_LEXICON:
        if len(queries) >= max_queries:
            break
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            queries.append(f"zö agency {terms}")
    return queries


def role_queries_for_requirement(requirement: str, *, max_queries: int = 2) -> list[str]:
    """Attach discipline searches to a single RFP requirement.

    Role queries used to run only as a global pool. When the query fan-out
    filled with planner strings first, platform/discipline searches never ran —
    and even when they did, hits were not attributed to the matching row.
    """
    text = requirement or ""
    if not text.strip():
        return []
    queries: list[str] = []
    for _name, patterns, terms in _ROLE_LEXICON:
        if len(queries) >= max_queries:
            break
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            queries.append(f"zö agency {terms}")
    return queries


def primary_query_for_requirement(requirement: str) -> str:
    """One compact KB search derived from the requirement's own wording."""
    text = re.sub(r"\s+", " ", (requirement or "")).strip()
    if not text:
        return ""
    return f"zö agency {text[:140]} 04_Bio 03_CS 06_WON"
