"""Medical document organizer: model, OCR wrapper, document-type
classification and safe summarization.

The summary is deliberately conservative: it reports the document type, the
date, measurements and terms that are *explicitly present* in the text, and
questions to ask a clinician. It never says "you have X".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Document-type classification
# ---------------------------------------------------------------------------

DOC_TYPES = (
    "Prescription",
    "Blood test report",
    "X-ray report",
    "MRI/CT report",
    "Doctor's notes",
    "Discharge summary",
    "Vaccination record",
    "Bill / Invoice",
    "Insurance document",
    "Referral letter",
    "Other",
)

DOC_TYPE_KEYWORDS = {
    "Prescription": ("rx", "prescription", "take one", "take 1", "tablet",
                     "tablets", "capsule", "mg", "dosage", "medicine",
                     "medication", "syrup", "before food", "after food",
                     "once daily", "twice daily", "bd", "od", "sos"),
    "Blood test report": ("hemoglobin", "haemoglobin", "wbc", "rbc", "platelet",
                          "cholesterol", "glucose", "blood sugar", "lipid",
                          "creatinine", "sodium", "potassium", "triglyceride",
                          "hba1c", "hb", "esr", "blood group", "urea", "bilirubin"),
    "X-ray report": ("x-ray", "xray", "radiograph", "radiology", "chest x",
                     "fracture", "bone density"),
    "MRI/CT report": ("mri", "ct scan", "magnetic resonance", "computed tomography",
                      "t1", "t2 weighted", "contrast study"),
    "Doctor's notes": ("diagnosis", "assessment", "plan", "follow-up",
                       "follow up", "impression", "consultation", "advice",
                       "review", "opd", "chief complaints", "history"),
    "Discharge summary": ("discharge", "admitted", "admission", "inpatient",
                          "summary", "discharged", "date of admission",
                          "date of discharge"),
    "Vaccination record": ("vaccine", "vaccination", "immunization",
                           "immunisation", "booster", "dose", "dpt", "mmr",
                           "bcg", "polio", "hepatitis b", "covid"),
    "Bill / Invoice": ("invoice", "amount", "total", "paid", "bill", "gst",
                       "receipt", "payment", "cash", "upi", "balance due"),
    "Insurance document": ("insurance", "policy", "claim", "sum insured",
                           "premium", "insured", "nominee", "cashless", "tpa"),
    "Referral letter": ("referral", "referred to", "referred by", "ref:"),
}

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class Document:
    id: int
    user_id: int
    title: str
    doc_type: str
    date: str                 # ISO date of the document
    facility: str = ""
    tags: list = field(default_factory=list)
    file_path: str = ""
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    notes: str = ""
    created_at: str = ""

    def to_row(self) -> tuple:
        return (self.user_id, self.title, self.doc_type, self.date,
                self.facility, ",".join(self.tags), self.file_path,
                self.ocr_text, self.ocr_confidence, self.notes, self.created_at)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_document(text: str) -> tuple[str, float, list[str]]:
    """Return (doc_type, confidence 0-1, matched_terms) from OCR'd text."""
    low = (text or "").lower()
    if not low.strip():
        return "Other", 0.0, []
    best_type, best_score, best_terms = "Other", 0.0, []
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in low]
        if not hits:
            continue
        # reward both number of hits and distinct terms
        score = min(0.97, 0.4 + 0.12 * len(hits))
        if score > best_score:
            best_type, best_score, best_terms = doc_type, score, hits[:6]
    return best_type, best_score, best_terms


# ---------------------------------------------------------------------------
# Summary (carefully framed — never a diagnosis)
# ---------------------------------------------------------------------------

_MEASUREMENT_RE = re.compile(
    r"([a-zA-Z][a-zA-Z /]{1,30}?)\s*[:=]\s*(\d+(?:\.\d+)?)\s*"
    r"(mg/dl|mg/dL|g/dl|g/dL|mmol/L|ng/ml|u/l|U/L|IU/L|%|mm|cm|kg|mmHg)?", re.IGNORECASE)


def summarize_document(doc_type: str, date: str, text: str) -> dict:
    """Conservative, evidence-based summary. Reports only what is explicit."""
    text = (text or "").strip()
    measurements = []
    seen = set()
    for m in _MEASUREMENT_RE.finditer(text):
        key = m.group(0).lower()
        if key in seen:
            continue
        seen.add(key)
        measurements.append(m.group(0).strip())

    terms = sorted({t for t in re.split(r"[\s,;]+", text.lower())
                    if 3 <= len(t) <= 24 and t not in
                    ("the", "and", "for", "with", "was", "were", "that",
                     "this", "your", "patient", "report", "date", "name",
                     "age", "sex", "result", "value", "normal", "range",
                     "test", "tests", "from", "are", "not", "has", "have")})[:14]

    return {
        "document_type": doc_type,
        "date": date,
        "measurements": measurements[:10],
        "terms": terms,
        "questions": [
            "What does this report show?",
            "Are any of these values outside the expected range?",
            "Do I need any follow-up tests?",
            "Should I share this report with my regular doctor?",
        ],
        "framing": ("This report contains the following findings. Discuss "
                    "their significance with your healthcare professional."),
    }


# ---------------------------------------------------------------------------
# OCR (optional — EasyOCR, same approach as the receipt scanner)
# ---------------------------------------------------------------------------

_reader = None


def ocr_available() -> bool:
    try:
        import easyocr  # noqa: F401
        return True
    except ImportError:
        return False


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def ocr_image(path: str) -> dict:
    """OCR an image file. Returns {text, confidence} or raises on failure."""
    reader = _get_reader()
    results = reader.readtext(path, detail=1)
    lines, confs = [], []
    for _, text, conf in results:
        t = str(text).strip()
        if t:
            lines.append(t)
            confs.append(float(conf))
    avg = (sum(confs) / len(confs)) if confs else 0.0
    return {"text": "\n".join(lines), "confidence": round(avg * 100, 1)}


# ---------------------------------------------------------------------------
# Local date helper (kept tiny to avoid importing the parent utils.py)
# ---------------------------------------------------------------------------


def parse_iso_date(text: str):
    from datetime import datetime
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def today_iso() -> str:
    from datetime import date
    return date.today().isoformat()
