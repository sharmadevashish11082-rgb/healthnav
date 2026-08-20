"""Seed HealthNav with a demo account and realistic sample data so the app
has content on first launch.

Usage:
    python demo_data.py           # seed only if the database is empty
    python demo_data.py --reset   # wipe all data, then reseed

Demo login after seeding:  email demo@healthnav.local  password demo1234
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import database as db
from facilities import SAMPLE_FACILITIES

DEMO_EMAIL = "demo@healthnav.local"
DEMO_PASSWORD = "demo1234"


def _demo_documents_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_documents")


def seed(reset: bool = False) -> bool:
    if reset and os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init()
    db.init_facilities(SAMPLE_FACILITIES)

    existing = db.get_user_by_email(DEMO_EMAIL)
    if existing:
        print("Demo data already present - nothing added.")
        print("Use:  python demo_data.py --reset   to wipe and reseed.")
        return False

    uid = db.create_user("Demo User", DEMO_EMAIL, "+91 90000 00000", DEMO_PASSWORD)

    # consent + preferences (as if the user completed onboarding)
    for ctype in db.CONSENT_TYPES:
        info = db.CONSENT_TYPES[ctype]
        db.record_consent(uid, ctype, granted=True)
    db.set_cookie_prefs(uid, analytics=True, preferences=True, marketing=False)
    for key, val in db.PRIVACY_PREF_DEFAULTS.items():
        db.set_privacy_pref(uid, key, val)
    db.set_setting(uid, "location_lat", "12.9716")
    db.set_setting(uid, "location_lon", "77.5946")
    db.set_setting(uid, "location_label", "Indiranagar, Bengaluru")

    # appointments
    today = datetime.now()
    soon = today + timedelta(days=1)
    db.add_appointment(
        uid, "General Consultation", soon.strftime("%Y-%m-%d 16:30"),
        facility_id=1, doctor="Dr. A. Rao", reason="Persistent sore throat",
        notes="Bring previous blood test reports.",
        booking_method="Phone booking", reminder_minutes=1440)
    later = today + timedelta(days=9)
    db.add_appointment(
        uid, "Dental check-up", later.strftime("%Y-%m-%d 10:00"),
        facility_id=3, doctor="Dr. S. Menon", reason="Routine cleaning",
        notes="", booking_method="Website booking", reminder_minutes=2880)
    past = today - timedelta(days=4)
    db.add_appointment(
        uid, "Follow-up consultation", past.strftime("%Y-%m-%d 18:00"),
        facility_id=1, doctor="Dr. A. Rao", reason="Review reports",
        notes="", booking_method="Walk-in", reminder_minutes=0, status="done")

    # saved facilities + views
    db.save_facility(uid, 1)
    db.save_facility(uid, 5)
    db.record_facility_view(uid, 1)
    db.record_facility_view(uid, 2)

    # documents with sample OCR text so summaries have content
    blood_text = (
        "Patient: Demo User   Date: 08 Aug 2026   ABC Lab\n"
        "Hemoglobin: 13.2 g/dL   WBC: 6800 /uL   Platelets: 2.4 lakh\n"
        "Fasting glucose: 92 mg/dL   Total cholesterol: 178 mg/dL\n"
        "Reference ranges shown. Report reviewed by lab technologist."
    )
    rx_text = (
        "Rx  -  Tab. Amoxicillin 500 mg 1-0-1 for 5 days\n"
        "Tab. Paracetamol 500 mg SOS   Syrup cough 2 tsp at night\n"
        "Dr. A. Rao   ABC Clinic   Date: 05 Aug 2026"
    )
    xray_text = (
        "X-Ray Chest PA view   Date: 20 Jul 2026   CarePlus Diagnostics\n"
        "No obvious abnormality detected. Radiologist: Dr. N. Iyer"
    )
    d1 = db.add_document(uid, "Blood test report - Aug 2026", "Blood test report",
                         "2026-08-08", "ABC Lab", ["blood", "recent"],
                         ocr_text=blood_text, ocr_confidence=91.0,
                         notes="Fasting sample, morning.")
    d2 = db.add_document(uid, "Prescription - Dr. Rao", "Prescription",
                         "2026-08-05", "ABC Clinic", ["medication"],
                         ocr_text=rx_text, ocr_confidence=88.0)
    d3 = db.add_document(uid, "Chest X-ray - Jul 2026", "X-ray report",
                         "2026-07-20", "CarePlus Diagnostics", ["imaging"],
                         ocr_text=xray_text, ocr_confidence=84.0)

    # navigation history
    db.add_navigation(
        uid, "I have been having a sore throat and want to see someone.",
        "routine", ["general-physician"],
        ["What could be causing this?", "Are there tests you recommend?"])

    print(f"Seeded demo account: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"  {len(SAMPLE_FACILITIES)} facilities, 3 appointments, 3 documents.")
    print("Open the app with:  python main.py")
    return True


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
