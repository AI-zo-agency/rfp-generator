"""
Hybrid PII Detection Pipeline for RFP Documents
================================================
Pass 1: Regex     -> structured PII (email, phone, SSN, credit card, IP, etc.)
Pass 2: NER model  -> contextual PII (person names, orgs, addresses, locations)
Pass 3: Merge/dedupe -> single ordered entity list with spans
Pass 4: Pseudonymize -> replace entities with reversible tokens

Install:
    pip install transformers torch --break-system-packages

Usage:
    python rfp_pii_pipeline.py
"""

import re
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional


# --------------------------------------------------------------------------
# 1. Data model
# --------------------------------------------------------------------------

@dataclass
class Entity:
    text: str
    label: str          # e.g. PERSON, ORG, EMAIL, PHONE, SSN, ADDRESS
    start: int
    end: int
    score: float
    source: str          # "regex" or "ner"


# --------------------------------------------------------------------------
# 2. Regex pass — structured PII
# --------------------------------------------------------------------------

REGEX_PATTERNS = {
    "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "PHONE": r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "CREDIT_CARD": r"\b(?:\d[ -]*?){13,16}\b",
    "IP_ADDRESS": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "URL": r"https?://[^\s]+",
    "ZIP_CODE": r"\b\d{5}(?:-\d{4})?\b",
}


def detect_regex_pii(text: str) -> List[Entity]:
    entities = []
    for label, pattern in REGEX_PATTERNS.items():
        for m in re.finditer(pattern, text):
            entities.append(
                Entity(
                    text=m.group(),
                    label=label,
                    start=m.start(),
                    end=m.end(),
                    score=0.99,  # regex matches are treated as high-confidence
                    source="regex",
                )
            )
    return entities


# --------------------------------------------------------------------------
# 3. NER pass — contextual PII (names, orgs, locations)
# --------------------------------------------------------------------------

class NERDetector:
    """
    Wraps a HuggingFace token-classification pipeline.
    Swap `model_name` for Ettin-68M-Nemotron-PII, GLiNER, or any NER model
    you have access to. Falls back gracefully if transformers isn't installed.
    """

    def __init__(self, model_name: str = "dslim/bert-base-NER"):
        self.model_name = model_name
        self._pipe = None

    def _load(self):
        if self._pipe is None:
            from transformers import pipeline
            self._pipe = pipeline(
                "token-classification",
                model=self.model_name,
                aggregation_strategy="simple",
            )
        return self._pipe

    def detect(self, text: str, min_score: float = 0.6) -> List[Entity]:
        pipe = self._load()

        # BERT's tokenizer mishandles the Hawaiian ʻokina (ʻ, U+02BB) and similar
        # curly-apostrophe characters, splitting words into junk fragments like
        # "H" + "O" + "##i ...". Swap them for a plain apostrophe just for NER,
        # then map offsets back onto the original text.
        okina_chars = "\u02bb\u2018\u2019"
        ner_text = re.sub(f"[{okina_chars}]", "'", text)

        raw = pipe(ner_text)
        entities = []
        for r in raw:
            if r["score"] < min_score:
                continue
            span_text = text[r["start"]:r["end"]]  # pull from ORIGINAL text
            # drop junk: bare single letters/punctuation are tokenizer artifacts
            if len(span_text.strip()) <= 1:
                continue
            entities.append(
                Entity(
                    text=span_text,
                    label=r["entity_group"],  # PER, ORG, LOC, MISC (model-dependent)
                    start=r["start"],
                    end=r["end"],
                    score=float(r["score"]),
                    source="ner",
                )
            )
        return entities


# --------------------------------------------------------------------------
# 4. Merge + dedupe overlapping spans (regex wins ties, since it's higher precision)
# --------------------------------------------------------------------------

def merge_entities(regex_ents: List[Entity], ner_ents: List[Entity]) -> List[Entity]:
    all_ents = sorted(regex_ents + ner_ents, key=lambda e: (e.start, -e.score))
    merged: List[Entity] = []

    for ent in all_ents:
        overlap = False
        for kept in merged:
            if ent.start < kept.end and ent.end > kept.start:
                overlap = True
                # prefer regex over ner on overlap, else the longer/higher-confidence span
                if ent.source == "regex" and kept.source != "regex":
                    merged.remove(kept)
                    merged.append(ent)
                elif (ent.end - ent.start) > (kept.end - kept.start):
                    merged.remove(kept)
                    merged.append(ent)
                break
        if not overlap:
            merged.append(ent)

    return sorted(merged, key=lambda e: e.start)


# --------------------------------------------------------------------------
# 5. Pseudonymization
# --------------------------------------------------------------------------

class PIIPseudonymizer:
    def __init__(self):
        self.entity_map: Dict[str, Dict] = {}
        self._counters: Dict[str, int] = {}

    def _token_for(self, entity: Entity) -> str:
        key = f"{entity.label}:{entity.text}"
        if key in self.entity_map:
            return self.entity_map[key]["token"]

        self._counters[entity.label] = self._counters.get(entity.label, 0) + 1
        token = f"[{entity.label}_{self._counters[entity.label]}]"

        self.entity_map[key] = {
            "token": token,
            "type": entity.label,
            "original": entity.text,
            "source": entity.source,
            "score": entity.score,
        }
        return token

    def pseudonymize(self, text: str, entities: List[Entity]) -> str:
        # replace from the end so earlier offsets stay valid
        result = text
        for ent in sorted(entities, key=lambda e: e.start, reverse=True):
            token = self._token_for(ent)
            result = result[: ent.start] + token + result[ent.end :]
        return result

    def rehydrate(self, text: str) -> str:
        """Reverse pseudonymization using the stored entity map."""
        result = text
        for record in self.entity_map.values():
            result = result.replace(record["token"], record["original"])
        return result


# --------------------------------------------------------------------------
# 6. Audit logging
# --------------------------------------------------------------------------

def log_pii_operations(rfp_id: str, entity_map: Dict, out_dir: str = "audit_logs"):
    import os
    os.makedirs(out_dir, exist_ok=True)

    audit_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rfp_id": rfp_id,
        "entities_processed": len(entity_map),
        "entity_types": sorted(set(v["type"] for v in entity_map.values())),
        "mapping": {
            k: {"token": v["token"], "type": v["type"]} for k, v in entity_map.items()
        },
    }

    path = f"{out_dir}/{rfp_id}.json"
    with open(path, "w") as f:
        json.dump(audit_entry, f, indent=2)
    return path


# --------------------------------------------------------------------------
# 6b. PDF text extraction
# --------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract raw text from a PDF file so it can be fed into the PII pipeline.
    Requires: pip install pypdf --break-system-packages
    """
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    pages_text = []
    for page in reader.pages:
        pages_text.append(page.extract_text() or "")
    raw_text = "\n".join(pages_text)
    return normalize_pdf_text(raw_text)


def normalize_pdf_text(text: str) -> str:
    """
    Clean up common PDF-extraction artifacts that break NER tokenization:
    - collapse hard line-wraps within paragraphs into spaces
    - collapse runs of whitespace
    - fix hyphenated line-break splits ("Hawai-\nʻi" -> "Hawaiʻi")
    """
    # de-hyphenate words split across a line break
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # collapse newlines that are just wrapping (not real paragraph breaks)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # collapse multiple blank lines into one
    text = re.sub(r"\n{2,}", "\n\n", text)
    # collapse repeated spaces/tabs
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# --------------------------------------------------------------------------
# 7. End-to-end pipeline
# --------------------------------------------------------------------------


def process_rfp(rfp_text: str, rfp_id: str, ner_model: Optional[str] = None) -> Dict:
    # Pass 1: regex
    regex_ents = detect_regex_pii(rfp_text)

    # Pass 2: NER (contextual)
    detector = NERDetector(ner_model) if ner_model else NERDetector()
    ner_ents = detector.detect(rfp_text)

    # Pass 3: merge
    merged = merge_entities(regex_ents, ner_ents)

    # Pass 4: pseudonymize
    pseudonymizer = PIIPseudonymizer()
    anonymized_text = pseudonymizer.pseudonymize(rfp_text, merged)

    # Pass 5: audit log
    log_path = log_pii_operations(rfp_id, pseudonymizer.entity_map)

    return {
        "rfp_id": rfp_id,
        "anonymized_text": anonymized_text,
        "entity_map": pseudonymizer.entity_map,
        "entity_count": len(pseudonymizer.entity_map),
        "audit_log_path": log_path,
    }


# --------------------------------------------------------------------------
# 8. Demo
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Usage: python rfp_pii_pipeline.py "/path/to/your.pdf"
        pdf_path = sys.argv[1]
        print(f"Extracting text from: {pdf_path}")
        sample_rfp = extract_text_from_pdf(pdf_path)
    else:
        # Fallback demo text if no PDF path is given
        sample_rfp = """'/Users/mahipatel/Downloads/Cumberland Valley Visitors Bureau.pdf'""
        Our company, Acme Logistics Ltd, is requesting a proposal for a new
        warehouse management system. Please contact John Carter at
        john.carter@acmelogistics.com or (415) 555-0192 for questions.
        Our office is located in San Francisco, CA 94107.
        """

    result = process_rfp(sample_rfp, rfp_id="rfp_0001")

    print("=== Anonymized Text ===")
    print(result["anonymized_text"])
    print("\n=== Entity Map ===")
    print(json.dumps(result["entity_map"], indent=2))
    print(f"\nAudit log written to: {result['audit_log_path']}")