import logging
import re
from typing import Dict, Any, Optional

# Dedicated Logger for AI Timesheet Classification Generation Traces
logger = logging.getLogger("app.services.ai_classifier")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[AI-CLASSIFIER LOG] %(asctime)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

TEXT_NUMBERS = {
    "first": 1, "1st": 1, "one": 1, "i": 1,
    "second": 2, "2nd": 2, "two": 2, "ii": 2,
    "third": 3, "3rd": 3, "three": 3, "iii": 3,
    "fourth": 4, "4th": 4, "four": 4, "iv": 4,
    "fifth": 5, "5th": 5, "five": 5, "v": 5,
}

def extract_topic_nlu(task_text: str) -> str:
    """Extracts normalized deliverable topic from freeform contractor text."""
    if not task_text:
        return "General Work"
    
    cleaned = re.sub(
        r"^(working on|finishing|editing|making|drafting|getting|edits on|edit for|revisions for|final edits on|second round of edits on the|third round of edits on)\s+",
        "",
        task_text,
        flags=re.IGNORECASE
    )
    cleaned = re.sub(
        r"\s+-\s+(rd|round|r)\s*\.|\s+(round|r|rd|edit|edits)\s*\d+.*$",
        "",
        cleaned,
        flags=re.IGNORECASE
    )
    cleaned = cleaned.strip()
    return cleaned if cleaned else task_text

def detect_round_nlu(task_text: str) -> Optional[int]:
    """Detects revision round number using NLU regex (handles spelled out words like 'second', 'third', 'fourth')."""
    if not task_text:
        return None

    lower = task_text.lower()

    # Check digit match (e.g. 'round 4', 'rd 2', 'edit #3', 'r4')
    digit_match = re.search(r"(?:round|r|rd|edit|edits|revision)\s*[\.\#]?\s*(\d+)", lower)
    if digit_match:
        return int(digit_match.group(1))

    # Check spelled out text match (e.g. 'second round', 'third round', 'final edits')
    for word, number in TEXT_NUMBERS.items():
        if re.search(rf"\b{word}\b\s*(?:round|round of edits|edit|revisions|pass)?", lower):
            return number

    if "final edits" in lower:
        return 3

    return None

def classify_timesheet_task(task_description: str) -> Dict[str, Any]:
    """
    AI Classification Engine: Analyzes raw contractor task string and generates
    structured NLU classification metadata with full execution logging.
    """
    logger.info(f"INPUT TASK TEXT: '{task_description}'")

    topic = extract_topic_nlu(task_description)
    detected_round = detect_round_nlu(task_description)
    task_lower = (task_description or "").lower()
    
    is_edit = (
        detected_round is not None or
        "edit" in task_lower or
        "edits" in task_lower or
        "round" in task_lower or
        "rd" in task_lower or
        "revision" in task_lower
    )

    is_over_scope = False
    work_category = "In-Scope Baseline Production"
    status_tag = "✓ Billable Time"
    reasoning = f"Initial asset production for '{topic}' covered under monthly retainer scope."

    if is_edit:
        round_num = detected_round if detected_round is not None else 1
        if round_num >= 4 or "round 4" in task_lower or "r4" in task_lower:
          is_over_scope = True
          work_category = f"Unbilled Revision Creep (Round {round_num})"
          status_tag = "⚠️ Over Scope"
          reasoning = f"Exceeds 3-round retainer cap (Round {round_num} requested). Requires add-on change order."
        else:
          is_over_scope = False
          work_category = f"In-Scope Revision (Round {round_num} of 3)"
          status_tag = "✓ Billable Time"
          reasoning = f"Revision Round {round_num} covered under standard client retainer scope."

    result = {
        "raw_task": task_description,
        "topic": topic,
        "detected_round": detected_round,
        "is_edit_task": is_edit,
        "is_over_scope": is_over_scope,
        "work_category": work_category,
        "status_tag": status_tag,
        "ai_reasoning": reasoning
    }

    logger.info(
        f"AI GENERATED CLASSIFICATION => Topic: '{topic}' | Round: {detected_round} | "
        f"OverScope: {is_over_scope} | Status: '{status_tag}' | Category: '{work_category}'"
    )

    return result
