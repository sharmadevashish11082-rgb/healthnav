"""Healthcare facilities: model, distance math, open/closed logic and a
curated sample dataset.

In production this data would come from a places API (e.g. Google Places)
with proper attribution and caching rules. For this local MVP we ship a
clearly-labelled demo dataset so the app works fully offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

# ---------------------------------------------------------------------------
# Verification levels (spec section 27)
# ---------------------------------------------------------------------------

VERIFICATION_LEVELS = {
    "officially_verified": "Officially verified",
    "provider_verified": "Data provider verified",
    "user_reported": "User reported",
    "unavailable": "Information unavailable",
}

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class Facility:
    id: int
    name: str
    facility_type: str          # Clinic / Hospital / Diagnostic lab / Pharmacy...
    services: list              # e.g. ["General Physician", "Emergency"]
    lat: float
    lon: float
    address: str
    phone: str
    website: str = ""
    rating: float = 0.0
    review_count: int = 0
    opening_hours: dict = field(default_factory=dict)  # {"mon": "09:00-18:00", ...}
    accessibility: list = field(default_factory=list)  # ["Wheelchair entrance", ...]
    emergency: bool = False
    verification_level: str = "unavailable"
    verified_date: str = ""
    appointment_methods: list = field(default_factory=list)
    description: str = ""

    def to_row(self) -> tuple:
        return (self.name, self.facility_type, ",".join(self.services),
                self.lat, self.lon, self.address, self.phone, self.website,
                self.rating, self.review_count, self._hours_json(),
                ",".join(self.accessibility), int(self.emergency),
                self.verification_level, self.verified_date,
                ",".join(self.appointment_methods), self.description)

    def _hours_json(self) -> str:
        import json
        return json.dumps(self.opening_hours)

    def verification_label(self) -> str:
        return VERIFICATION_LEVELS.get(self.verification_level,
                                       self.verification_level)


# ---------------------------------------------------------------------------
# Distance + open/closed
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def is_open_now(opening_hours: dict, now: datetime | None = None) -> bool | None:
    """True if open now, False if closed, None if hours are unknown."""
    if not opening_hours:
        return None
    now = now or datetime.now()
    day_key = _DAYS[now.weekday()]
    slot = (opening_hours.get(day_key) or "").strip()
    if not slot:
        return False
    try:
        start_s, end_s = slot.split("-", 1)
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        return None
    t = now.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end  # overnight shift


def opening_hours_text(opening_hours: dict) -> str:
    """A compact, human-readable summary of opening hours."""
    if not opening_hours:
        return "Hours not available"
    parts = []
    for d in _DAYS:
        slot = (opening_hours.get(d) or "").strip()
        parts.append(f"{d.title()}: {slot or 'Closed'}")
    return "  |  ".join(parts)


# ---------------------------------------------------------------------------
# Sample dataset (demo — around a fixed demo location, e.g. central Bengaluru)
# ---------------------------------------------------------------------------

DEMO_LOCATION = {"lat": 12.9716, "lon": 77.5946, "label": "Demo location (Indiranagar, Bengaluru)"}

_HOURS_9_6 = {d: "09:00-18:00" for d in _DAYS}
_HOURS_9_6_SUN_OFF = {d: "09:00-18:00" for d in ("mon", "tue", "wed", "thu", "fri", "sat")}
_HOURS_24 = {d: "00:00-23:59" for d in _DAYS}
_HOURS_10_8 = {d: "10:00-20:00" for d in _DAYS}


def _f(fid, name, ftype, services, lat, lon, address, phone, rating, reviews,
       hours, accessibility, emergency, verification, verified, website="",
       appointment_methods=None, description=""):
    return Facility(
        id=fid, name=name, facility_type=ftype, services=list(services),
        lat=lat, lon=lon, address=address, phone=phone, rating=rating,
        review_count=reviews, opening_hours=hours,
        accessibility=list(accessibility), emergency=emergency,
        verification_level=verification, verified_date=verified,
        website=website, appointment_methods=list(appointment_methods or []),
        description=description,
    )


SAMPLE_FACILITIES = [
    _f(1, "ABC Clinic", "Clinic", ["General Physician", "Pediatrics"],
       12.9750, 77.5990, "12, 100 Feet Road, Indiranagar, Bengaluru 560038",
       "+91 80 4111 2001", 4.4, 132, _HOURS_9_6_SUN_OFF,
       ["Wheelchair entrance", "Wheelchair parking"], False,
       "officially_verified", "2026-08-08",
       "https://example.com/abc-clinic", ["Phone booking", "Walk-in"],
       "A multi-speciality neighbourhood clinic with a pharmacy attached."),
    _f(2, "XYZ Hospital", "Hospital", ["Emergency", "General Medicine", "Cardiology",
                                       "Orthopedics"],
       12.9680, 77.5900, "45, MG Road, Bengaluru 560001",
       "+91 80 4222 3001", 4.2, 487, _HOURS_24,
       ["Wheelchair entrance", "Accessible restrooms", "Accessible parking"], True,
       "officially_verified", "2026-08-08",
       "https://example.com/xyz-hospital", ["Website booking", "Phone booking"],
       "24x7 multi-speciality hospital with an emergency department."),
    _f(3, "Sunrise Dental Studio", "Clinic", ["Dental care", "Orthodontics"],
       12.9788, 77.6012, "7, 80 Feet Road, HAL 2nd Stage, Bengaluru 560008",
       "+91 80 4333 4001", 4.7, 210, _HOURS_10_8,
       ["Wheelchair entrance"], False, "officially_verified", "2026-08-08",
       "https://example.com/sunrise-dental", ["Website booking", "Phone booking"],
       "Modern dental clinic with digital X-ray."),
    _f(4, "GreenView Eye Care", "Clinic", ["Eye care", "Optometry"],
       12.9734, 77.6050, "22, 100 Feet Road, Indiranagar, Bengaluru 560038",
       "+91 80 4444 5001", 4.5, 96, _HOURS_9_6_SUN_OFF,
       ["Wheelchair entrance", "Accessible restrooms"], False,
       "provider_verified", "2026-07-30",
       "https://example.com/greenview-eye", ["Phone booking"],
       "Eye clinic with refraction, cataract and contact-lens services."),
    _f(5, "CarePlus Diagnostics", "Diagnostic lab",
       ["Blood tests", "Imaging", "ECG", "Ultrasound"],
       12.9701, 77.5961, "3, CMH Road, Indiranagar, Bengaluru 560038",
       "+91 80 4555 6001", 4.3, 301, {"mon": "06:30-20:00", "tue": "06:30-20:00",
                                      "wed": "06:30-20:00", "thu": "06:30-20:00",
                                      "fri": "06:30-20:00", "sat": "06:30-14:00",
                                      "sun": "06:30-11:00"},
       ["Wheelchair entrance"], False, "officially_verified", "2026-08-08",
       "https://example.com/careplus-diagnostics", ["Phone booking", "Home collection"],
       "NABL-accredited lab with home sample collection."),
    _f(6, "Wellness First Physiotherapy", "Clinic", ["Physiotherapy", "Rehabilitation"],
       12.9805, 77.5930, "18, 12th Main, HAL 2nd Stage, Bengaluru 560008",
       "+91 80 4666 7001", 4.6, 74, _HOURS_9_6_SUN_OFF,
       ["Wheelchair entrance", "Accessible restrooms"], False,
       "user_reported", "2026-07-15",
       "https://example.com/wellness-first", ["Phone booking"],
       "Physiotherapy and sports-injury rehabilitation centre."),
    _f(7, "MindCare Counselling Centre", "Clinic",
       ["Mental health", "Counselling", "Psychology"],
       12.9698, 77.6042, "9, 6th Main, HAL 2nd Stage, Bengaluru 560008",
       "+91 80 4777 8001", 4.8, 58, _HOURS_9_6,
       ["Wheelchair entrance"], False, "unavailable", "",
       "https://example.com/mindcare", ["Website booking", "Phone booking"],
       "Counselling and therapy centre (by appointment only)."),
    _f(8, "CityCare Skin & Hair", "Clinic", ["Dermatology", "Trichology"],
       12.9722, 77.5890, "31, Brigade Road, Bengaluru 560001",
       "+91 80 4888 9001", 4.1, 189, _HOURS_10_8,
       ["Wheelchair entrance"], False, "provider_verified", "2026-07-22",
       "https://example.com/citycare-skin", ["Phone booking", "Walk-in"],
       "Dermatology and hair clinic."),
    _f(9, "Swasthya Women's Care", "Clinic",
       ["Gynecology", "Obstetrics", "Pediatrics"],
       12.9760, 77.5975, "14, 100 Feet Road, Indiranagar, Bengaluru 560038",
       "+91 80 4999 0001", 4.5, 143, _HOURS_9_6_SUN_OFF,
       ["Wheelchair entrance", "Accessible restrooms", "Accessible parking"],
       False, "officially_verified", "2026-08-08",
       "https://example.com/swasthya", ["Website booking", "Phone booking"],
       "Women's health and maternity clinic."),
    _f(10, "Apollo Pharmacy (Indiranagar)", "Pharmacy", ["Pharmacy", "Medicines"],
        12.9742, 77.6020, "66, 100 Feet Road, Indiranagar, Bengaluru 560038",
        "+91 80 5000 1001", 4.3, 356, _HOURS_24,
        ["Wheelchair entrance"], False, "officially_verified", "2026-08-08",
        "https://example.com/apollo-pharmacy", ["Walk-in"],
        "24x7 pharmacy and wellness store."),
    _f(11, "HearingWell ENT Centre", "Clinic", ["ENT", "Audiology"],
        12.9705, 77.5985, "5, 80 Feet Road, HAL 2nd Stage, Bengaluru 560008",
        "+91 80 5111 2001", 4.4, 67, _HOURS_9_6_SUN_OFF,
        ["Wheelchair entrance"], False, "user_reported", "2026-06-18",
        "https://example.com/hearingwell", ["Phone booking"],
        "ENT consultation and hearing-aid services."),
    _f(12, "Meridian Gastro & Liver Clinic", "Clinic",
        ["Gastroenterology", "General Medicine"],
        12.9675, 77.5950, "8, Residency Road, Bengaluru 560025",
        "+91 80 5222 3001", 4.0, 88, _HOURS_9_6_SUN_OFF,
        ["Wheelchair entrance"], False, "provider_verified", "2026-07-05",
        "https://example.com/meridian-gastro", ["Phone booking", "Website booking"],
        "Gastroenterology and liver clinic."),
    _f(13, "VitalCare Heart Centre", "Clinic", ["Cardiology", "General Medicine"],
        12.9710, 77.5920, "26, Lavelle Road, Bengaluru 560001",
        "+91 80 5333 4001", 4.6, 172, _HOURS_9_6,
        ["Wheelchair entrance", "Accessible restrooms"], False,
        "provider_verified", "2026-07-28",
        "https://example.com/vitalcare", ["Website booking", "Phone booking"],
        "Cardiology OPD with ECG and echo."),
]
