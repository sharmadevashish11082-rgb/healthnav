"""SQLite storage for HealthNav.

Schema mirrors the spec's data model: Users, ConsentRecords, PrivacyPreferences,
CookiePreferences, Facilities, Appointments, Documents, SavedFacilities,
ConsultationQuestions (navigation history), HealthTimeline (derived),
Notifications (derived from appointments), Feedback.

All data lives in a local `healthnav.db` file next to this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime

from facilities import Facility

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "healthnav.db")

# ---------------------------------------------------------------------------
# Consent catalogue (spec sections 21 & 23)
# ---------------------------------------------------------------------------

CONSENT_TYPES = {
    "terms": {"label": "Terms of Service", "version": "1.4", "required": True},
    "privacy": {"label": "Privacy Policy", "version": "2.1", "required": True},
    "healthcare_disclaimer": {
        "label": "Healthcare Navigation Disclaimer", "version": "1.0", "required": True},
    "document_processing": {
        "label": "Medical Document / Data Processing terms", "version": "1.0",
        "required": True},
    "no_diagnosis": {
        "label": ("Understanding that this app does not provide medical diagnosis "
                  "or replace a healthcare professional"),
        "version": "1.0", "required": True},
    "personalization": {
        "label": "Allow personalized recommendations", "version": "1.0",
        "required": False},
    "analytics": {
        "label": "Allow analytics", "version": "1.0", "required": False},
    "marketing": {
        "label": "Allow marketing communications", "version": "1.0",
        "required": False},
}

COOKIE_CATEGORIES = {
    "essential": "Required for the website to function. Cannot normally be disabled.",
    "analytics": "Helps understand how the application is used.",
    "preferences": "Remembers settings and preferences.",
    "marketing": "Used for advertising/marketing where applicable.",
}

PRIVACY_PREF_DEFAULTS = {
    "location": True, "camera": False, "microphone": False,
    "notifications": True, "ai_processing": True, "analytics": False,
    "data_sharing": False,
}

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    """Create tables if they do not exist. Safe to call every launch."""
    conn = _connect()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            phone TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS consent_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            consent_type TEXT NOT NULL,
            version TEXT NOT NULL,
            granted INTEGER NOT NULL,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cookie_prefs (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            analytics INTEGER NOT NULL DEFAULT 0,
            preferences INTEGER NOT NULL DEFAULT 0,
            marketing INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS privacy_prefs (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            location INTEGER NOT NULL DEFAULT 1,
            camera INTEGER NOT NULL DEFAULT 0,
            microphone INTEGER NOT NULL DEFAULT 0,
            notifications INTEGER NOT NULL DEFAULT 1,
            ai_processing INTEGER NOT NULL DEFAULT 1,
            analytics INTEGER NOT NULL DEFAULT 0,
            data_sharing INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            facility_type TEXT NOT NULL,
            services TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            address TEXT NOT NULL,
            phone TEXT NOT NULL,
            website TEXT NOT NULL DEFAULT '',
            rating REAL NOT NULL DEFAULT 0,
            review_count INTEGER NOT NULL DEFAULT 0,
            hours_json TEXT NOT NULL DEFAULT '{}',
            accessibility TEXT NOT NULL DEFAULT '',
            emergency INTEGER NOT NULL DEFAULT 0,
            verification_level TEXT NOT NULL DEFAULT 'unavailable',
            verified_date TEXT NOT NULL DEFAULT '',
            appointment_methods TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS saved_facilities (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
            saved_at TEXT NOT NULL,
            PRIMARY KEY (user_id, facility_id)
        );

        CREATE TABLE IF NOT EXISTS facility_views (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
            viewed_at TEXT NOT NULL,
            PRIMARY KEY (user_id, facility_id)
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            facility_id INTEGER REFERENCES facilities(id) ON DELETE SET NULL,
            doctor TEXT NOT NULL DEFAULT '',
            when_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            booking_method TEXT NOT NULL DEFAULT '',
            reminder_minutes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'upcoming',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            doc_type TEXT NOT NULL DEFAULT 'Other',
            date TEXT NOT NULL DEFAULT '',
            facility TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            ocr_text TEXT NOT NULL DEFAULT '',
            ocr_confidence REAL NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS navigation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            input_text TEXT NOT NULL,
            urgency TEXT NOT NULL,
            service_keys TEXT NOT NULL DEFAULT '[]',
            questions TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            facility_id INTEGER REFERENCES facilities(id) ON DELETE SET NULL,
            category TEXT NOT NULL DEFAULT 'general',
            rating INTEGER,
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_appointments_user ON appointments(user_id);
        CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
        CREATE INDEX IF NOT EXISTS idx_consent_user ON consent_records(user_id);
        """
    )
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _settings_defaults() -> dict:
    return {"location_lat": "", "location_lon": "", "location_label": "",
            "prep_checklist": "{}", "language": "en", "text_scale": "1.0"}


# ---------------------------------------------------------------------------
# Users & authentication
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("ascii"),
                         n=2 ** 14, r=8, p=1)
    return key.hex(), salt


def verify_password(password: str, salt: str, expected_hex: str) -> bool:
    key, _ = hash_password(password, salt)
    return secrets.compare_digest(key, expected_hex)


def create_user(name: str, email: str, phone: str, password: str) -> int:
    """Create a user. Raises ValueError on duplicate email/phone or short password."""
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    name = name.strip()
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if not email and not phone:
        raise ValueError("Provide an email or phone number.")
    if email and get_user_by_email(email):
        raise ValueError("An account with this email already exists.")
    if phone and get_user_by_phone(phone):
        raise ValueError("An account with this phone number already exists.")
    key, salt = hash_password(password)
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, salt, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (name, email, phone, key, salt, _now()))
        conn.commit()
        uid = cur.lastrowid
    finally:
        conn.close()
    return uid


def get_user(user_id: int):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_user_by_email(email: str):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?",
                           ((email or "").strip().lower(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_user_by_phone(phone: str):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE phone=?",
                           ((phone or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def authenticate(identifier: str, password: str):
    """Return user dict on success, None otherwise. Accepts email or phone."""
    user = get_user_by_email(identifier) or get_user_by_phone(identifier)
    if not user:
        return None
    if not verify_password(password, user["salt"], user["password_hash"]):
        return None
    set_last_login(user["id"])
    return get_user(user["id"])


def set_last_login(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), user_id))
        conn.commit()
    finally:
        conn.close()


def update_user(user_id: int, name: str | None = None, email: str | None = None,
                phone: str | None = None) -> None:
    conn = _connect()
    try:
        if name is not None:
            conn.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), user_id))
        if email is not None:
            conn.execute("UPDATE users SET email=? WHERE id=?",
                         (email.strip().lower(), user_id))
        if phone is not None:
            conn.execute("UPDATE users SET phone=? WHERE id=?", (phone.strip(), user_id))
        conn.commit()
    finally:
        conn.close()


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    user = get_user(user_id)
    if not user or not verify_password(old_password, user["salt"],
                                       user["password_hash"]):
        return False
    if len(new_password) < 6:
        raise ValueError("New password must be at least 6 characters.")
    key, salt = hash_password(new_password)
    conn = _connect()
    try:
        conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?",
                     (key, salt, user_id))
        conn.commit()
    finally:
        conn.close()
    return True


def delete_account(user_id: int) -> None:
    """Permanently delete the account and all linked rows (CASCADE)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Consent records (spec sections 21 & 23)
# ---------------------------------------------------------------------------


def record_consent(user_id: int, consent_type: str, granted: bool) -> None:
    info = CONSENT_TYPES.get(consent_type)
    if not info:
        raise ValueError(f"Unknown consent type: {consent_type}")
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO consent_records (user_id, consent_type, version, granted, timestamp)"
            " VALUES (?,?,?,?,?)",
            (user_id, consent_type, info["version"], int(granted), _now()))
        conn.commit()
    finally:
        conn.close()


def get_consent_records(user_id: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT consent_type, version, granted, timestamp FROM consent_records"
            " WHERE user_id=? ORDER BY timestamp DESC", (user_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def current_consents(user_id: int) -> dict:
    """Map consent_type -> {"granted": bool, "version": str, "timestamp": str}
    (latest record per type)."""
    out = {}
    for rec in get_consent_records(user_id):
        if rec["consent_type"] not in out:
            out[rec["consent_type"]] = rec
    return out


def required_consents_complete(user_id: int) -> bool:
    current = current_consents(user_id)
    for ctype, info in CONSENT_TYPES.items():
        if info["required"] and not current.get(ctype, {}).get("granted"):
            return False
    return True


# ---------------------------------------------------------------------------
# Cookie & privacy preferences
# ---------------------------------------------------------------------------


def set_cookie_prefs(user_id: int, analytics: bool, preferences: bool,
                     marketing: bool) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO cookie_prefs (user_id, analytics, preferences, marketing, updated_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(user_id) DO UPDATE SET analytics=excluded.analytics,"
            " preferences=excluded.preferences, marketing=excluded.marketing,"
            " updated_at=excluded.updated_at",
            (user_id, int(analytics), int(preferences), int(marketing), _now()))
        conn.commit()
    finally:
        conn.close()


def get_cookie_prefs(user_id: int) -> dict:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM cookie_prefs WHERE user_id=?",
                           (user_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {"analytics": False, "preferences": False,
                                  "marketing": False}


def set_privacy_pref(user_id: int, key: str, value: bool) -> None:
    if key not in PRIVACY_PREF_DEFAULTS:
        raise ValueError(f"Unknown privacy pref: {key}")
    conn = _connect()
    try:
        # Insert only the changed key (schema defaults cover the rest);
        # on conflict, update just that key so other prefs are preserved.
        conn.execute(
            f"INSERT INTO privacy_prefs ({key}, user_id, updated_at)"
            f" VALUES (?, ?, ?)"
            f" ON CONFLICT(user_id) DO UPDATE SET {key}=excluded.{key},"
            f" updated_at=excluded.updated_at",
            (1 if value else 0, user_id, _now()))
        conn.commit()
    finally:
        conn.close()


def get_privacy_prefs(user_id: int) -> dict:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM privacy_prefs WHERE user_id=?",
                           (user_id,)).fetchone()
    finally:
        conn.close()
    if row:
        prefs = {k: bool(row[k]) for k in PRIVACY_PREF_DEFAULTS}
    else:
        prefs = dict(PRIVACY_PREF_DEFAULTS)
    return prefs


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def set_setting(user_id: int, key: str, value: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?,?,?)"
            " ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (user_id, key, value))
        conn.commit()
    finally:
        conn.close()


def get_settings(user_id: int) -> dict:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings WHERE user_id=?",
                            (user_id,)).fetchall()
    finally:
        conn.close()
    out = dict(_settings_defaults())
    out.update({r["key"]: r["value"] for r in rows})
    return out


def get_setting(user_id: int, key: str, default: str = "") -> str:
    return get_settings(user_id).get(key, default)


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------


def init_facilities(facilities: list) -> None:
    """Idempotent seed: upsert every facility by id (never deletes rows, so
    saved_facilities / facility_views are preserved across launches)."""
    conn = _connect()
    try:
        for f in facilities:
            conn.execute(
                "INSERT INTO facilities (id, name, facility_type, services,"
                " lat, lon, address, phone, website, rating, review_count, hours_json,"
                " accessibility, emergency, verification_level, verified_date,"
                " appointment_methods, description) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " name=excluded.name, facility_type=excluded.facility_type,"
                " services=excluded.services, lat=excluded.lat, lon=excluded.lon,"
                " address=excluded.address, phone=excluded.phone, website=excluded.website,"
                " rating=excluded.rating, review_count=excluded.review_count,"
                " hours_json=excluded.hours_json, accessibility=excluded.accessibility,"
                " emergency=excluded.emergency,"
                " verification_level=excluded.verification_level,"
                " verified_date=excluded.verified_date,"
                " appointment_methods=excluded.appointment_methods,"
                " description=excluded.description",
                (f.id,) + f.to_row())
        conn.commit()
    finally:
        conn.close()


def _facility_from_row(row) -> Facility:
    return Facility(
        id=row["id"], name=row["name"], facility_type=row["facility_type"],
        services=[s for s in (row["services"] or "").split(",") if s],
        lat=row["lat"], lon=row["lon"], address=row["address"], phone=row["phone"],
        website=row["website"] or "", rating=row["rating"] or 0.0,
        review_count=row["review_count"] or 0,
        opening_hours=json.loads(row["hours_json"] or "{}"),
        accessibility=[s for s in (row["accessibility"] or "").split(",") if s],
        emergency=bool(row["emergency"]),
        verification_level=row["verification_level"] or "unavailable",
        verified_date=row["verified_date"] or "",
        appointment_methods=[s for s in (row["appointment_methods"] or "").split(",") if s],
        description=row["description"] or "")


def get_all_facilities() -> list[Facility]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM facilities ORDER BY name").fetchall()
    finally:
        conn.close()
    return [_facility_from_row(r) for r in rows]


def get_facility(facility_id: int) -> Facility | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM facilities WHERE id=?",
                           (facility_id,)).fetchone()
    finally:
        conn.close()
    return _facility_from_row(row) if row else None


# ---------------------------------------------------------------------------
# Saved facilities / views
# ---------------------------------------------------------------------------


def save_facility(user_id: int, facility_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO saved_facilities (user_id, facility_id, saved_at)"
            " VALUES (?,?,?)", (user_id, facility_id, _now()))
        conn.commit()
    finally:
        conn.close()


def unsave_facility(user_id: int, facility_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM saved_facilities WHERE user_id=? AND facility_id=?",
                     (user_id, facility_id))
        conn.commit()
    finally:
        conn.close()


def is_saved(user_id: int, facility_id: int) -> bool:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM saved_facilities WHERE user_id=? AND facility_id=?",
            (user_id, facility_id)).fetchone()
    finally:
        conn.close()
    return row is not None


def get_saved_facilities(user_id: int) -> list[Facility]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT f.* FROM facilities f JOIN saved_facilities s"
            " ON s.facility_id = f.id WHERE s.user_id=? ORDER BY s.saved_at DESC",
            (user_id,)).fetchall()
    finally:
        conn.close()
    return [_facility_from_row(r) for r in rows]


def record_facility_view(user_id: int, facility_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO facility_views (user_id, facility_id, viewed_at)"
            " VALUES (?,?,?)", (user_id, facility_id, _now()))
        conn.commit()
    finally:
        conn.close()


def get_recently_viewed(user_id: int, limit: int = 5) -> list[Facility]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT f.* FROM facilities f JOIN facility_views v"
            " ON v.facility_id = f.id WHERE v.user_id=? ORDER BY v.viewed_at DESC LIMIT ?",
            (user_id, limit)).fetchall()
    finally:
        conn.close()
    return [_facility_from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Appointments & reminders
# ---------------------------------------------------------------------------


def add_appointment(user_id: int, title: str, when_at: str, facility_id: int | None = None,
                    doctor: str = "", reason: str = "", notes: str = "",
                    booking_method: str = "", reminder_minutes: int = 0,
                    status: str = "upcoming") -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO appointments (user_id, title, facility_id, doctor, when_at,"
            " reason, notes, booking_method, reminder_minutes, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, title, facility_id, doctor, when_at, reason, notes,
             booking_method, reminder_minutes, status, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_appointments(user_id: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT a.*, f.name AS facility_name FROM appointments a"
            " LEFT JOIN facilities f ON f.id = a.facility_id"
            " WHERE a.user_id=? ORDER BY a.when_at", (user_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_upcoming_appointments(user_id: int) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    return [a for a in get_appointments(user_id)
            if a["status"] != "cancelled" and a["when_at"] >= today]


def get_appointment(appointment_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT a.*, f.name AS facility_name FROM appointments a"
            " LEFT JOIN facilities f ON f.id = a.facility_id WHERE a.id=?",
            (appointment_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def update_appointment(appointment_id: int, **fields) -> None:
    allowed = {"title", "doctor", "when_at", "reason", "notes", "booking_method",
               "reminder_minutes", "status"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return
    conn = _connect()
    try:
        conn.execute(f"UPDATE appointments SET {', '.join(sets)} WHERE id=?",
                     [fields[k] for k in fields if k in allowed] + [appointment_id])
        conn.commit()
    finally:
        conn.close()


def delete_appointment(appointment_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM appointments WHERE id=?", (appointment_id,))
        conn.commit()
    finally:
        conn.close()


def get_reminders(user_id: int) -> list[dict]:
    """Appointments with an active reminder, nearest first."""
    out = []
    for a in get_upcoming_appointments(user_id):
        if a["reminder_minutes"] > 0:
            out.append(a)
    out.sort(key=lambda a: a["when_at"])
    return out


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def add_document(user_id: int, title: str, doc_type: str, date: str,
                 facility: str = "", tags: list | None = None, file_path: str = "",
                 ocr_text: str = "", ocr_confidence: float = 0.0,
                 notes: str = "") -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO documents (user_id, title, doc_type, date, facility, tags,"
            " file_path, ocr_text, ocr_confidence, notes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, title, doc_type, date, facility, ",".join(tags or []),
             file_path, ocr_text, ocr_confidence, notes, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_documents(user_id: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM documents WHERE user_id=? ORDER BY date DESC, id DESC",
            (user_id,)).fetchall()
    finally:
        conn.close()
    return [_doc_from_row(r) for r in rows]


def _doc_from_row(row) -> dict:
    d = dict(row)
    d["tags"] = [t for t in (row["tags"] or "").split(",") if t]
    return d


def get_document(doc_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    return _doc_from_row(row) if row else None


def update_document(doc_id: int, **fields) -> None:
    allowed = {"title", "doc_type", "date", "facility", "tags", "ocr_text",
               "ocr_confidence", "notes"}
    sets, values = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            values.append(",".join(v) if k == "tags" and isinstance(v, list) else v)
    if not sets:
        return
    conn = _connect()
    try:
        conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id=?",
                     values + [doc_id])
        conn.commit()
    finally:
        conn.close()


def delete_document(doc_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()


def delete_all_documents(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM documents WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def search_documents(user_id: int, query: str = "", doc_type: str = "",
                     date_from: str = "", date_to: str = "",
                     tag: str = "") -> list[dict]:
    docs = get_documents(user_id)
    q = (query or "").strip().lower()
    out = []
    for d in docs:
        if doc_type and d["doc_type"] != doc_type:
            continue
        if date_from and d["date"] < date_from:
            continue
        if date_to and d["date"] > date_to:
            continue
        if tag and tag not in d["tags"]:
            continue
        if q:
            hay = " ".join([d["title"], d["doc_type"], d["facility"],
                            d["notes"], d["ocr_text"][:2000]]).lower()
            if q not in hay:
                continue
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Navigation history
# ---------------------------------------------------------------------------


def add_navigation(user_id: int, input_text: str, urgency: str,
                   service_keys: list, questions: list) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO navigation_history (user_id, input_text, urgency,"
            " service_keys, questions, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, input_text, urgency, json.dumps(service_keys),
             json.dumps(questions), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_navigation_history(user_id: int, limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM navigation_history WHERE user_id=? ORDER BY created_at DESC"
            " LIMIT ?", (user_id, limit)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["service_keys"] = json.loads(d["service_keys"] or "[]")
        d["questions"] = json.loads(d["questions"] or "[]")
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


def add_feedback(user_id: int, facility_id: int | None, category: str,
                 comment: str, rating: int | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO feedback (user_id, facility_id, category, rating, comment,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (user_id, facility_id, category, rating, comment, _now()))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dashboard stats & timeline
# ---------------------------------------------------------------------------


def user_stats(user_id: int) -> dict:
    conn = _connect()
    try:
        saved = conn.execute(
            "SELECT COUNT(*) FROM saved_facilities WHERE user_id=?", (user_id,)).fetchone()[0]
        appointments = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE user_id=?", (user_id,)).fetchone()[0]
        upcoming = len(get_upcoming_appointments(user_id))
        documents = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE user_id=?", (user_id,)).fetchone()[0]
        nav = conn.execute(
            "SELECT COUNT(*) FROM navigation_history WHERE user_id=?", (user_id,)).fetchone()[0]
    finally:
        conn.close()
    return {"saved_facilities": saved, "appointments": appointments,
            "upcoming": upcoming, "documents": documents,
            "consultation_notes": nav}


def get_timeline(user_id: int) -> list[dict]:
    """Chronological (newest first) mix of appointments and documents."""
    items = []
    for a in get_appointments(user_id):
        items.append({
            "date": a["when_at"][:10], "kind": "appointment",
            "title": a["title"], "detail": a.get("facility_name") or "",
            "ref_id": a["id"],
        })
    for d in get_documents(user_id):
        items.append({
            "date": d["date"], "kind": "document", "title": d["title"],
            "detail": d["doc_type"], "ref_id": d["id"],
        })
    items.sort(key=lambda x: (x["date"], x["kind"]), reverse=True)
    return items


# ---------------------------------------------------------------------------
# Privacy: export & deletion
# ---------------------------------------------------------------------------


def export_user_data(user_id: int) -> dict:
    """Everything the app stores about this user (for data export).
    Password hashes/salts are never included in the export."""
    user = dict(get_user(user_id) or {})
    user.pop("password_hash", None)
    user.pop("salt", None)
    return {
        "exported_at": _now(),
        "user": user,
        "consents": get_consent_records(user_id),
        "cookie_preferences": get_cookie_prefs(user_id),
        "privacy_preferences": get_privacy_prefs(user_id),
        "settings": get_settings(user_id),
        "appointments": get_appointments(user_id),
        "documents": get_documents(user_id),
        "saved_facilities": [f.name for f in get_saved_facilities(user_id)],
        "navigation_history": get_navigation_history(user_id),
        "timeline": get_timeline(user_id),
    }


def delete_user_data(user_id: int) -> list[str]:
    """Delete user content rows (documents metadata, appointments, prefs).
    Returns the list of document file paths on disk so the caller can
    remove the actual files. Does NOT delete the account itself."""
    docs = get_documents(user_id)
    conn = _connect()
    try:
        for table in ("documents", "appointments", "saved_facilities",
                      "facility_views", "navigation_history", "feedback",
                      "cookie_prefs", "privacy_prefs", "settings",
                      "consent_records"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return [d["file_path"] for d in docs if d["file_path"]]
