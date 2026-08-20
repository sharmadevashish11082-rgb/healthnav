"""Healthcare Navigation Engine (deterministic, offline).

Turns free-text concerns into structured *navigation* intent:

    urgency level -> suggested service categories -> what you can do
    -> questions for the clinician

This module is deliberately NOT a diagnosis engine. It never outputs a
disease, a prescription or a treatment recommendation. Everything it returns
is framed as navigation guidance ("you may want to consider a general
physician consultation"), never as a medical conclusion.

Pipeline (mirrors the product spec's safety layer):

    user input -> input validation -> rule analysis
    -> medical-safety rules -> output validation -> navigation response

The rule-based analysis is fully deterministic, so it works offline and is
easy to test. An LLM provider can be slotted in later behind the same
`analyze()` interface as long as it honours the same safety rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

URGENCY_EMERGENCY = "emergency"
URGENCY_URGENT = "urgent"
URGENCY_ROUTINE = "routine"
URGENCY_INFORMATION = "information"

URGENCY_LABELS = {
    URGENCY_EMERGENCY: "Emergency",
    URGENCY_URGENT: "Urgent",
    URGENCY_ROUTINE: "Routine",
    URGENCY_INFORMATION: "Information",
}

DISCLAIMER = (
    "This information is for healthcare navigation and preparation only. "
    "It is not a medical diagnosis and does not replace a healthcare professional."
)

NO_DIAGNOSIS_NOTE = (
    "This app helps you navigate the healthcare system. It does not diagnose, "
    "prescribe or treat, and it never replaces a healthcare professional."
)

# ---------------------------------------------------------------------------
# Safety patterns
# ---------------------------------------------------------------------------

# Symptoms / situations that require IMMEDIATE emergency care.
EMERGENCY_PATTERNS = (
    # chest / breathing
    "chest pain", "chest pressure", "chest tightness", "crushing chest",
    "shortness of breath", "difficulty breathing", "hard to breathe",
    "can't breathe", "cannot breathe", "not breathing", "struggling to breathe",
    "breathless at rest", "blue lips", "blue face", "cold and clammy",
    # consciousness
    "unconscious", "passed out", "fainted", "unresponsive", "not responding",
    "won't wake up", "very confused", "disoriented",
    # bleeding / injury
    "severe bleeding", "bleeding heavily", "heavy bleeding", "won't stop bleeding",
    "head injury", "hit my head", "banged my head", "road accident", "car accident",
    "major accident", "stabbed", "gunshot", "poisoning", "poisoned", "overdose",
    "drowning", "electrocuted", "serious burn", "severe burn", "third degree burn",
    "choking", "choked",
    # stroke
    "stroke", "slurred speech", "face droop", "drooping face",
    "numbness on one side", "weakness on one side", "can't lift my arm",
    # seizure
    "seizure", "convulsion", "convulsing", "fitting", "fit for",
    # mental-health emergency
    "suicidal", "want to end my life", "self-harm", "self harm", "hurt myself",
    "want to hurt myself",
    # severe allergic
    "severe allergic reaction", "anaphylaxis", "swollen face", "swollen lips",
    "swelling of the face", "swelling of the tongue", "swelling of the lips",
    # GI / other red flags
    "vomiting blood", "coughing up blood", "coughing blood", "blood in vomit",
    "blood in stool", "black stool", "passing blood", "rigid neck", "stiff neck",
    "severe abdominal pain", "worst pain", "unbearable pain", "screaming in pain",
    # pregnancy / infant
    "bleeding during pregnancy", "heavy period", "labour", "labor",
    "contractions", "water broke", "baby not moving", "infant not feeding",
    "newborn fever",
)

# High fever threshold — "fever 104", "fever of 105", "104 degree fever" etc.
HIGH_FEVER_RE = re.compile(
    r"(?:fever|temperature)\s*(?:of\s*)?(?:10[4-9]|11[0-9])\b|"
    r"(?:10[4-9]|11[0-9])\s*(?:degree|degrees|\u00b0)\s*(?:fever|temperature)",
    re.IGNORECASE,
)

# Symptoms that need prompt (not immediate) attention.
URGENT_PATTERNS = (
    "high fever", "persistent fever", "fever since", "fever for",
    "vomiting", "throwing up", "diarrhea", "loose motions", "loose stools",
    "dehydrated", "dehydration", "not drinking", "can't keep water down",
    "burning while urinating", "pain while urinating", "blood in urine",
    "severe headache", "worst headache", "blurred vision", "double vision",
    "dizziness", "lightheaded", "palpitations", "racing heart",
    "irregular heartbeat", "chest discomfort", "wheezing", "asthma attack",
    "can't swallow", "difficulty swallowing", "swollen", "swelling",
    "pus", "infected wound", "rash spreading", "hives", "fever with rash",
    "severe pain", "unbearable pain", "pain in the chest",
    "baby", "infant", "newborn", "toddler", "pregnant", "pregnancy",
    "elderly", "above 60", "high blood pressure", "low blood pressure",
    "blood sugar very high", "jaundice", "yellow eyes", "yellow skin",
    "dark urine", "fits", "severe dizziness",
)

# "I just want information" intent (no appointment needed yet).
INFORMATION_PATTERNS = (
    "what is", "what are", "how does", "how do", "tell me about",
    "information", "need to know", "is it safe", "should i get", "difference between",
    "prevent", "prevention", "vaccine schedule", "what should i know",
    "do i need", "can i",
)

# ---------------------------------------------------------------------------
# Service catalogue
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceCategory:
    key: str
    label: str
    description: str
    keywords: tuple[str, ...]
    questions: tuple[str, ...] = ()


SERVICE_CATEGORIES: tuple[ServiceCategory, ...] = (
    ServiceCategory(
        "general-physician",
        "General physician / primary-care consultation",
        "A good starting point for most new or non-specific health concerns. "
        "A general physician can assess the concern and refer you to a "
        "specialist if needed.",
        ("fever", "cough", "cold", "sore throat", "throat pain", "throat infection",
         "runny nose", "blocked nose", "congestion", "body ache", "body pain",
         "headache", "weakness", "fatigue", "tiredness", "tired", "flu", "viral",
         "infection", "checkup", "check-up", "check up", "routine check",
         "full body", "vaccination", "vaccine", "blood pressure", "diabetes",
         "thyroid", "not feeling well", "unwell", "sick", "loss of appetite",
         "weight loss", "weight gain", "general physician", "primary care",
         "family doctor", "gp", "don't know", "dont know", "not sure"),
        ("What common causes should I be aware of?",
         "Are there lifestyle changes that could help before the visit?"),
    ),
    ServiceCategory(
        "pediatric",
        "Pediatric care",
        "For the health concerns of infants, children and teenagers.",
        ("child", "baby", "infant", "newborn", "toddler", "kid", "kids",
         "son", "daughter", "school-age", "adolescent", "paediatric",
         "pediatric", "childhood"),
        ("What symptoms should I monitor in my child?",
         "When should I bring my child back or seek urgent care?",
         "Are the recommended vaccinations up to date?"),
    ),
    ServiceCategory(
        "dentist",
        "Dental care",
        "For teeth, gum and mouth concerns.",
        ("tooth", "teeth", "toothache", "tooth pain", "dental", "gum",
         "gums", "cavity", "cavities", "braces", "wisdom tooth", "root canal",
         "filling", "mouth ulcer", "bad breath", "jaw pain", "bleeding gums"),
        ("What can I do to manage the pain before the appointment?",
         "Is a filling, root canal or extraction likely?",
         "How should I care for my teeth in the meantime?"),
    ),
    ServiceCategory(
        "dermatology",
        "Dermatology",
        "For skin, hair and nail concerns.",
        ("skin", "rash", "acne", "pimple", "pimples", "itching", "itchy",
         "hair loss", "hair fall", "dandruff", "mole", "moles", "eczema",
         "psoriasis", "fungal", "ringworm", "hives", "pigmentation",
         "dry skin", "skin tag", "wart", "scalp"),
        ("What could be causing this skin concern?",
         "What products or habits should I avoid in the meantime?",
         "Should the spot be examined or biopsied?"),
    ),
    ServiceCategory(
        "eye-care",
        "Eye care",
        "For vision and eye concerns.",
        ("eye", "eyes", "vision", "blurry", "blurred", "glasses", "spectacles",
         "contact lens", "contact lenses", "red eye", "redness in eye",
         "watery eyes", "cataract", "glaucoma", "dry eyes", "eye pain",
         "floaters", "night blindness", "eye strain", "squint"),
        ("Do I need a new eye test or prescription?",
         "What should I do for eye strain and screen time?",
         "Are there signs I should watch for between visits?"),
    ),
    ServiceCategory(
        "ent",
        "ENT (ear, nose, throat)",
        "For ear, nose, throat, sinus and hearing concerns.",
        ("ear", "ears", "earache", "ear pain", "hearing", "hearing loss",
         "ringing in ears", "ear wax", "nose", "nasal", "sinus", "sinusitis",
         "tonsil", "tonsillitis", "voice", "hoarse", "vertigo",
         "voice loss", "snoring", "allergic rhinitis"),
        ("What could be causing these symptoms?",
         "Are there home-care steps that are safe before the visit?",
         "Do I need a hearing or sinus test?"),
    ),
    ServiceCategory(
        "orthopedics",
        "Orthopedics",
        "For bone, joint, muscle and spine concerns.",
        ("bone", "joint", "knee", "back pain", "neck pain", "shoulder",
         "hip", "fracture", "sprain", "strain", "ligament", "arthritis",
         "slipped disc", "disc", "posture", "foot", "ankle", "wrist",
         "elbow", "cartilage", "osteoporosis"),
        ("Do I need an X-ray or scan before the visit?",
         "What activities should I avoid in the meantime?",
         "Will physiotherapy be part of the treatment plan?"),
    ),
    ServiceCategory(
        "physiotherapy",
        "Physiotherapy",
        "For rehabilitation, mobility and musculoskeletal therapy.",
        ("physio", "physiotherapy", "rehabilitation", "rehab", "mobility",
         "exercise therapy", "frozen shoulder", "post-surgery", "post surgery",
         "back strengthening", "stretching", "pain management", "balance training"),
        ("How many sessions might I need?",
         "What exercises are safe for me to do at home?",
         "Do I need a referral from a doctor first?"),
    ),
    ServiceCategory(
        "mental-health",
        "Mental-health professional",
        "For counselling, therapy and mental-health support.",
        ("anxiety", "depression", "stress", "panic", "panic attack",
         "sleep", "insomnia", "therapy", "therapist", "counselor", "counsellor",
         "counselling", "counseling", "mood", "overthinking", "sad", "grief",
         "burnout", "adhd", "ocd", "anger", "trauma", "mental health"),
        ("What kind of therapy might suit me?",
         "How do I find a therapist I can trust?",
         "What should I do between sessions if things get hard?"),
    ),
    ServiceCategory(
        "gynecology",
        "Gynecology / women's health",
        "For reproductive and women's health concerns.",
        ("pregnancy", "pregnant", "period", "periods", "menstrual",
         "menstruation", "pcos", "pcod", "gynec", "gynae", "uterus",
         "ovarian", "contraception", "birth control", "menopause", "pap smear",
         "breast lump", "lactation", "breastfeeding", "vaginal", "cervix"),
        ("Which tests or screenings should I consider?",
         "What symptoms should I track between visits?",
         "Are there lifestyle factors that matter for this concern?"),
    ),
    ServiceCategory(
        "cardiology",
        "Cardiology",
        "For heart, circulation and blood-pressure concerns.",
        ("heart", "palpitations", "blood pressure", "hypertension",
         "cholesterol", "cardiac", "breathlessness on exertion", "chest",
         "swelling in feet", "swollen ankles", "heartbeat", "heart rate",
         "ecg", "treadmill"),
        ("What heart-related tests are appropriate?",
         "What numbers (BP, pulse) should I track at home?",
         "What should I do immediately if symptoms worsen?"),
    ),
    ServiceCategory(
        "neurology",
        "Neurology",
        "For brain, nerve and nervous-system concerns.",
        ("migraine", "seizure", "epilepsy", "tremor", "numbness",
         "tingling", "memory", "parkinson", "balance", "nerve pain",
         "neuropathy", "multiple sclerosis", "brain"),
        ("Do I need a scan or nerve test?",
         "How should I track episodes between visits?",
         "When should I go to an emergency department instead?"),
    ),
    ServiceCategory(
        "gastroenterology",
        "Gastroenterology",
        "For stomach, gut and digestive concerns.",
        ("stomach", "abdominal", "abdomen", "acidity", "acid reflux",
         "heartburn", "gas", "bloating", "constipation", "diarrhea",
         "loose motion", "loose motions", "ibs", "ulcer", "nausea",
         "indigestion", "liver", "hepatitis", "gallbladder", "appetite"),
        ("What tests might help find the cause?",
         "What foods should I avoid until the visit?",
         "When should the symptoms be treated as urgent?"),
    ),
    ServiceCategory(
        "diagnostic-labs",
        "Diagnostic / laboratory services",
        "For blood tests, scans and other diagnostic investigations.",
        ("blood test", "lab test", "laboratory", "x-ray", "xray", "mri",
         "ct scan", "ultrasound", "ecg", "eeg", "pathology", "sample",
         "fasting", "diagnostic", "blood work", "test report", "biopsy",
         "urine test", "sugar test", "lipid profile"),
        ("Do I need to fast or prepare before the test?",
         "When will the results be available?",
         "Who should I discuss the results with?"),
    ),
    ServiceCategory(
        "emergency-department",
        "Emergency department",
        "For medical emergencies requiring immediate care.",
        ("emergency", "accident", "trauma", "severe", "critical",
         "immediate care", "urgent care"),
        (),
    ),
)

# Emergency is always the *pathway*, never just a "category".
_CATEGORY_INDEX = {c.key: c for c in SERVICE_CATEGORIES}
_FALLBACK_KEY = "general-physician"


def get_category(key: str) -> ServiceCategory | None:
    return _CATEGORY_INDEX.get(key)


GENERIC_QUESTIONS = (
    "What could be causing this?",
    "What information should I monitor or track before the visit?",
    "Are there tests you recommend?",
    "What should I do if it gets worse?",
    "When should I follow up?",
)

# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

_ONSET_PATTERNS = (
    r"for\s+(?:the\s+)?(?:last|past)\s+([^.,;]{2,40})",
    r"since\s+([^.,;]{2,40})",
    r"started\s+(?:about\s+)?([^.,;]{2,40})",
    r"began\s+(?:about\s+)?([^.,;]{2,40})",
    r"(\b\d+\s+(?:day|week|month|year)s?\s+ago\b)",
    r"(\b(?:yesterday|last\s+week|last\s+month|this\s+morning|this\s+week)\b)",
)

_FREQUENCY_PATTERNS = (
    r"every\s+(?:day|morning|night|evening|week)",
    r"\bdaily\b",
    r"once\s+a\s+(?:day|week|month)",
    r"twice\s+a\s+(?:day|week)",
    r"\bat\s+night\b",
    r"in\s+the\s+morning",
    r"\bafter\s+eating\b",
    r"when\s+i\s+[a-z]{2,20}",
    r"\bon\s+and\s+off\b",
    r"\bsometimes\b",
    r"\boften\b",
    r"\bconstantly\b",
    r"\bintermittent\b",
)

_MEDICATION_HINTS = ("taking", "take", "prescribed", "medication", "medicine",
                     "tablet", "tablets", "capsule", "capsules", "syrup",
                     "dose", "dosage", "mg")
_ALLERGY_HINTS = ("allergic", "allergy", "allergies")

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")


def _lower(text: str) -> str:
    return (text or "").lower()


def _contains(low: str, phrase: str) -> bool:
    """Word-boundary-aware substring test (both args lowercased).
    Prevents 'labor' matching inside 'laboratory', 'kid' inside 'kidney', etc."""
    return re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low) is not None


def structure_input(text: str) -> dict:
    """Best-effort structured extraction: onset, frequency, symptoms,
    medications and allergies. Every field is optional and human-readable."""
    text = (text or "").strip()
    structured = {
        "main_concern": text,
        "onset": "",
        "frequency": "",
        "symptoms": [],
        "medications": [],
        "allergies": [],
    }
    if not text:
        return structured

    low = _lower(text)

    for pat in _ONSET_PATTERNS:
        m = re.search(pat, low)
        if m:
            structured["onset"] = m.group(1).strip(" ,;-")
            break

    freq_hits = []
    for pat in _FREQUENCY_PATTERNS:
        m = re.search(pat, low)
        if m:
            frag = m.group(0).strip()
            if frag not in freq_hits:
                freq_hits.append(frag)
    structured["frequency"] = "; ".join(freq_hits[:3])

    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    symptoms, meds, allergies = [], [], []
    for sent in sentences:
        s_low = _lower(sent)
        if any(h in s_low for h in _ALLERGY_HINTS):
            allergies.append(sent)
        elif any(h in s_low for h in _MEDICATION_HINTS):
            meds.append(sent)
        elif sent not in symptoms:
            symptoms.append(sent)
    structured["symptoms"] = symptoms[:6]
    structured["medications"] = meds[:3]
    structured["allergies"] = allergies[:3]
    return structured


def _clean_text(text: str) -> str:
    """Input validation: strip control chars, cap length, reject empty."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_urgency(text: str) -> tuple[str, list[str]]:
    """Return (urgency, reasons). Emergency outranks urgent; 'information'
    intent is only chosen when no health concern is present."""
    low = _lower(text).replace("\u2019", "'")
    reasons: list[str] = []

    for pat in EMERGENCY_PATTERNS:
        if _contains(low, pat):
            reasons.append(f"'{pat}' needs immediate attention")
    if HIGH_FEVER_RE.search(low):
        reasons.append("very high fever was mentioned")

    if reasons:
        return URGENCY_EMERGENCY, reasons

    for pat in URGENT_PATTERNS:
        if _contains(low, pat):
            reasons.append(f"'{pat}' should be checked promptly")
    if reasons:
        return URGENCY_URGENT, reasons

    has_concern = any(
        _contains(low, kw) for cat in SERVICE_CATEGORIES for kw in cat.keywords
    ) or any(_contains(low, w) for w in ("pain", "ache", "hurt", "sore",
                                          "bleeding", "swelling", "burning", "itch"))
    if not has_concern and any(_contains(low, p) for p in INFORMATION_PATTERNS):
        return URGENCY_INFORMATION, ["the request appears to be a question for information"]

    return URGENCY_ROUTINE, []


def suggest_services(text: str, urgency: str) -> list[dict]:
    """Score every service category against the text.

    Returns a list of dicts: {key, label, description, confidence, matched}.
    Specialists rank above the general-physician fallback when they match;
    the general physician is still included as a recommended starting point.
    """
    low = _lower(text).replace("\u2019", "'")
    scored = []
    for cat in SERVICE_CATEGORIES:
        if cat.key == "emergency-department":
            continue  # surfaced through the urgency pathway instead
        matched = [kw for kw in cat.keywords if _contains(low, kw)]
        if matched:
            specificity = len(max(matched, key=len))
            scored.append({
                "key": cat.key,
                "label": cat.label,
                "description": cat.description,
                "confidence": min(0.95, 0.55 + 0.08 * len(matched)),
                "matched": matched[:5],
                "_specificity": specificity,
            })
    scored.sort(key=lambda d: (d["confidence"], d["_specificity"]), reverse=True)

    fallback = get_category(_FALLBACK_KEY)
    if not scored:
        scored = [{
            "key": fallback.key, "label": fallback.label,
            "description": fallback.description,
            "confidence": 0.5, "matched": [],
            "_specificity": 0,
        }]
    else:
        general = get_category(_FALLBACK_KEY)
        has_general = any(s["key"] == _FALLBACK_KEY for s in scored)
        if not has_general and urgency == URGENCY_ROUTINE:
            scored.append({
                "key": general.key, "label": general.label,
                "description": general.description,
                "confidence": 0.5, "matched": [],
                "_specificity": 0,
            })
    for s in scored:
        s.pop("_specificity", None)
    return scored[:3]


def _action_list(urgency: str) -> list[str]:
    if urgency == URGENCY_EMERGENCY:
        return [
            "Contact local emergency services now (in India: 112 or 108).",
            "Seek immediate care at the nearest emergency department.",
            "If possible, have someone stay with you and do not drive yourself.",
            "Keep important information ready: current medications, allergies, ID.",
        ]
    if urgency == URGENCY_URGENT:
        return [
            "Contact a healthcare professional promptly (today).",
            "Find a nearby clinic or hospital and check appointment options.",
            "Prepare your symptoms, medications and allergies before calling.",
        ]
    if urgency == URGENCY_INFORMATION:
        return [
            "Organise questions and resources for a future appointment.",
            "Find reliable facilities or services to learn more.",
            "Save this information so you are prepared when you do visit.",
        ]
    return [
        "Find nearby clinics and compare facility information.",
        "Check opening hours and appointment options.",
        "Prepare questions for the clinician.",
        "Bring relevant past reports and a current medication list.",
    ]


def generate_questions(service_keys: list[str], structured: dict | None = None) -> list[str]:
    """A checklist of questions for the clinician, personalised by service
    category. The AI helps the patient prepare — it does not pretend to be
    the doctor."""
    questions: list[str] = []
    for key in service_keys:
        cat = get_category(key)
        if cat:
            for q in cat.questions:
                if q not in questions:
                    questions.append(q)
    for q in GENERIC_QUESTIONS:
        if q not in questions:
            questions.append(q)
    if structured and structured.get("medications"):
        questions.append("Are my current medications relevant to this concern?")
    if structured and structured.get("allergies"):
        questions.append("How should my allergies be taken into account?")
    return questions[:8]


# ---------------------------------------------------------------------------
# Output safety validator
# ---------------------------------------------------------------------------

# Phrases that must NEVER appear in any generated response.
FORBIDDEN_OUTPUT_PATTERNS = (
    r"you\s+(have|are\s+suffering\s+from|are\s+diagnosed\s+with|definitely\s+have)",
    r"\bdiagnos(?:e|ed|is)\b",
    r"\bprescription\b",
    r"\bdosage\b",
    r"take\s+\d+\s*(?:mg|ml|g|gm|tablet|tablets)",
    r"\b(?:amoxicillin|paracetamol|ibuprofen|azithromycin|ciprofloxacin|"
    r"metformin|omeprazole|prednisolone)\b",
    r"you\s+should\s+(?:take|stop\s+taking|avoid\s+taking)",
    r"\byou\s+will\s+(?:recover|be\s+fine)\b",
    r"\byou\s+have\s+(?:cancer|diabetes|infection|typhoid|malaria|dengue|"
    r"heart\s+disease|ulcer)\b",
)


_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|isn'?t|aren'?t|won'?t|can'?t|cannot|doesn'?t|don'?t)\b",
    re.IGNORECASE,
)


def _negated_before(text: str, start: int) -> bool:
    """True if a negation word appears shortly before `start`.

    Lets the validator accept safe phrasing like "not a medical diagnosis"
    or "does not diagnose" while still flagging real claims.
    """
    window = text[max(0, start - 30):start]
    return _NEGATION_RE.search(window) is not None


def validate_response(payload: dict) -> dict:
    """Safety net over every generated response.

    Scans all user-facing text in the payload and reports any forbidden
    phrasing. The engine's own templates should never trigger it — the
    validator exists so that a future LLM-backed provider cannot leak
    diagnosis-like language to the user. Matches in negated context
    ("not a diagnosis", "does not diagnose") are accepted.
    """
    violations = []
    text_fields = []

    def walk(node):
        if isinstance(node, str):
            text_fields.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    for key in ("urgency_message", "what_you_can_do", "questions",
                "service_categories", "note", "disclaimer"):
        walk(payload.get(key))

    for field in text_fields:
        low = field.lower()
        for pat in FORBIDDEN_OUTPUT_PATTERNS:
            for m in re.finditer(pat, low):
                if _negated_before(low, m.start()):
                    continue
                violations.append({"pattern": pat, "in_text": field[:80]})
                break

    safe = len(violations) == 0
    payload.setdefault("safety", {})
    payload["safety"]["safe"] = safe
    payload["safety"]["violations"] = violations
    return payload


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_EMERGENCY_MESSAGE = (
    "Some symptoms may require urgent medical attention. Please contact local "
    "emergency services or seek immediate medical care. Do not wait for an "
    "online navigation app to tell you what to do."
)

_URGENT_MESSAGE = (
    "Based on what you described, you should contact a healthcare "
    "professional promptly rather than waiting for a routine appointment."
)


def analyze(text: str) -> dict:
    """Full pipeline: validate input, structure it, classify urgency, suggest
    services, generate questions and validate the output.

    Returns a dict safe for display:
      input, urgency, urgency_label, urgency_message, reasons, structure,
      service_categories, what_you_can_do, questions, disclaimer, safety
    """
    text = _clean_text(text)
    payload: dict = {"input": text}

    if not text:
        payload.update({
            "urgency": URGENCY_INFORMATION,
            "urgency_label": URGENCY_LABELS[URGENCY_INFORMATION],
            "urgency_message": "",
            "reasons": ["No concern was entered."],
            "structure": structure_input(""),
            "service_categories": [],
            "what_you_can_do": ["Describe your concern to get navigation help."],
            "questions": [],
            "disclaimer": DISCLAIMER,
        })
        return validate_response(payload)

    urgency, reasons = classify_urgency(text)
    structured = structure_input(text)
    service_keys = []

    if urgency == URGENCY_EMERGENCY:
        payload["urgency_message"] = _EMERGENCY_MESSAGE
        suggested = [{
            "key": "emergency-department",
            "label": "Emergency department",
            "description": "Seek immediate emergency care.",
            "confidence": 1.0,
            "matched": reasons,
        }]
        questions: list[str] = []
        what = _action_list(urgency)
    else:
        payload["urgency_message"] = (
            _URGENT_MESSAGE if urgency == URGENCY_URGENT else "")
        suggested = suggest_services(text, urgency)
        service_keys = [s["key"] for s in suggested]
        questions = generate_questions(service_keys, structured)
        what = _action_list(urgency)

    payload.update({
        "urgency": urgency,
        "urgency_label": URGENCY_LABELS[urgency],
        "urgency_message": payload["urgency_message"],
        "reasons": reasons,
        "structure": structured,
        "service_categories": suggested,
        "what_you_can_do": what,
        "questions": questions,
        "disclaimer": DISCLAIMER,
        "note": NO_DIAGNOSIS_NOTE,
    })
    return validate_response(payload)
