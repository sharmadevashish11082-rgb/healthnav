# HealthNav — Healthcare Navigation Assistant

A pure-Python desktop app that helps people **navigate** the healthcare
system — it does **not** diagnose, prescribe or treat.

Built with the same approach as the other apps in this workspace: Tkinter
GUI, SQLite storage, optional EasyOCR scanning, no web frameworks.

> ⚠️ **This is healthcare *navigation*, not medicine.** The app suggests the
> *kind of care* that may fit ("you may want to consider a general
> physician consultation") and helps you prepare — it never says "you have
> disease X". For potentially serious symptoms it routes you to emergency
> care instead of attempting an explanation.

## Run

```bash
cd healthnav
python demo_data.py        # optional: seed a demo account + sample data
python main.py
```

Demo login after seeding: **demo@healthnav.local** / **demo1234**

Without seeding, create your own account on the signup tab (you'll then walk
through the consent screen).

### Tests

```bash
python -m unittest test_health -v
```

### Optional packages

The core app is pure stdlib (tkinter + sqlite3 ship with Python).

| Feature              | Install                                          |
|----------------------|--------------------------------------------------|
| Document OCR         | `pip install easyocr`                            |
| Camera document scan | `pip install opencv-python pillow`               |
| Voice input          | `pip install SpeechRecognition`                  |

## What's implemented (MVP per the product spec §43)

| # | Feature | Where |
|---|---------|-------|
| 1 | User authentication (email/phone + hashed password) | `database.py`, `main.py` (AuthView) |
| 2 | Legal consent screen — required + optional, with consent records (type, version, timestamp) | `main.py` (ConsentView), `database.py` |
| 3 | Cookie / preference system — Accept All / Reject Non-Essential / Manage, reopenable | `main.py` (CookieDialog) |
| 4 | Describe healthcare need — free text, voice, guided questionnaire | `main.py` (NavigationView) |
| 5 | AI converts request into structured navigation intent | `navigation.py` (`structure_input`, `analyze`) |
| 6 | Healthcare-service suggestion (never a diagnosis) | `navigation.py` (`suggest_services`) |
| 7 | Nearby facility search with filters (distance, open now, type, emergency) | `main.py` (FacilitiesView), `facilities.py` |
| 8 | Map view — offline schematic map, zoom, selectable pins | `main.py` (MapCanvas) |
| 9 | Facility details — hours, phone, services, accessibility, verification badge, directions, save, share, feedback | `main.py` (FacilityDialog) |
| 10 | Appointments — upcoming, add/edit, booking method, reminders | `main.py` (AppointmentsView) |
| 11 | Questions-for-clinician generator | `navigation.py` (`generate_questions`) |
| 12 | Consultation preparation checklist (persisted) | `main.py` (HomeView) |
| 13 | Medical document organizer — upload, tags, search, filters | `main.py` (DocumentsView) |
| 14 | Emergency / urgent-care safety pathway | `navigation.py` + `main.py` (SafetyView) |
| 15 | Privacy center — data collected, permissions, export JSON, delete | `main.py` (SettingsView) |

Bonus pieces that came almost for free with the same data model: health
timeline, saved facilities, navigation history, document summary (with
conservative framing), facility verification levels, feedback, emergency
contacts, account deletion, data export.

## Safety design (`navigation.py`)

The engine follows the pipeline from spec §41:

```
user input -> input validation -> rule analysis
           -> medical-safety rules -> output validation -> navigation response
```

- **Urgency layer**: `emergency` (immediate care), `urgent` (contact
  promptly), `routine` (schedule a consultation), `information` (organize
  resources). Emergency patterns always win.
- **Service suggestion** is phrased as "you may want to consider…", never a
  diagnosis. If no specialty matches, it falls back to general physician.
- **Output validator** scans every generated response for forbidden phrasing
  (diagnosis claims, prescription/dosage language, drug names, false
  certainty). Tests assert the validator is clean for all template output.
- Emergency responses do **not** generate questions or suggestions — they
  route to emergency services.

## Roadmap (deliberately not in v1)

Real maps/Places API with attribution · LLM-backed navigation behind the same
safety layer · OCR PDF support · telemedicine (highly regulated — see India's
Telemedicine Practice Guidelines) · push notifications · multilingual UI
(Hindi, Punjabi, Bengali, …) · accessibility (screen reader, high contrast,
keyboard nav) · admin dashboard · multi-profile households with permissions.

Before any public/India-facing launch, have a lawyer or privacy professional
review the consent design, privacy policy and data handling against
applicable Indian data-protection requirements.

## Project layout

| File | Purpose |
|------|---------|
| `main.py` | Tkinter GUI: auth, consent, dashboard, navigation, facilities + map, appointments, documents, timeline, safety, privacy |
| `navigation.py` | The safe navigation engine (urgency, service matching, questions, output validator) |
| `facilities.py` | Facility model, haversine distance, open/closed logic, demo dataset |
| `documents.py` | Document model, classification, conservative summary, optional OCR |
| `database.py` | SQLite: users, consents, cookie/privacy prefs, facilities, appointments, documents, timeline, export/delete |
| `theme.py` | Healthcare teal palette + ttk styles |
| `demo_data.py` | Seed demo account + sample data |
| `test_health.py` | Unit tests (stdlib only) |
