"""Unit tests for HealthNav. Pure stdlib (unittest), no GUI needed.

Run:  python -m unittest test_health -v
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime

import database as db
import documents as docs
import facilities as fac
import navigation as nav


class UrgencyTests(unittest.TestCase):
    def test_emergency_chest_pain(self):
        urgency, reasons = nav.classify_urgency(
            "I have chest pain and difficulty breathing.")
        self.assertEqual(urgency, nav.URGENCY_EMERGENCY)
        self.assertTrue(reasons)

    def test_emergency_mental_health(self):
        urgency, _ = nav.classify_urgency("I have been feeling suicidal lately.")
        self.assertEqual(urgency, nav.URGENCY_EMERGENCY)

    def test_emergency_high_fever(self):
        urgency, _ = nav.classify_urgency("My son has fever of 105 since this morning.")
        self.assertEqual(urgency, nav.URGENCY_EMERGENCY)

    def test_urgent_vomiting(self):
        urgency, _ = nav.classify_urgency("I have been vomiting all night.")
        self.assertEqual(urgency, nav.URGENCY_URGENT)

    def test_routine_sore_throat(self):
        urgency, _ = nav.classify_urgency("I have a sore throat and want to see someone.")
        self.assertEqual(urgency, nav.URGENCY_ROUTINE)

    def test_information_question(self):
        urgency, _ = nav.classify_urgency("How does health insurance work?")
        self.assertEqual(urgency, nav.URGENCY_INFORMATION)

    def test_question_with_health_keyword_is_routine(self):
        urgency, _ = nav.classify_urgency("What is a blood test for?")
        self.assertEqual(urgency, nav.URGENCY_ROUTINE)


class ServiceSuggestionTests(unittest.TestCase):
    def test_sore_throat_leads_to_general_physician(self):
        urgency, _ = nav.classify_urgency(
            "I have been having a sore throat and want to see someone.")
        services = nav.suggest_services(
            "I have been having a sore throat and want to see someone.", urgency)
        self.assertEqual(services[0]["key"], "general-physician")

    def test_toothache_leads_to_dentist(self):
        services = nav.suggest_services("My tooth is hurting a lot.", nav.URGENCY_ROUTINE)
        keys = [s["key"] for s in services]
        self.assertIn("dentist", keys)

    def test_unknown_doctor_fallback(self):
        services = nav.suggest_services("I don't know what type of doctor I need.",
                                        nav.URGENCY_ROUTINE)
        self.assertEqual(services[0]["key"], "general-physician")

    def test_skin_rash_dermatology(self):
        services = nav.suggest_services("I have a red itchy rash on my arm.",
                                        nav.URGENCY_ROUTINE)
        self.assertIn("dermatology", [s["key"] for s in services])


class QuestionGenerationTests(unittest.TestCase):
    def test_questions_include_generic_baseline(self):
        qs = nav.generate_questions(["general-physician"])
        self.assertIn("What could be causing this?", qs)
        self.assertIn("When should I follow up?", qs)

    def test_questions_are_never_diagnostic(self):
        qs = nav.generate_questions(["cardiology"], nav.structure_input(
            "I get palpitations after coffee."))
        for q in qs:
            self.assertNotIn("you have", q.lower())


class SafetyValidatorTests(unittest.TestCase):
    def test_analyze_output_is_safe(self):
        for text in ("I have a sore throat.", "I feel very sad lately.",
                     "Chest pain since morning.", "What is a blood test?",
                     "I have been vomiting since last night."):
            payload = nav.analyze(text)
            self.assertTrue(payload["safety"]["safe"], text)
            self.assertEqual(payload["safety"]["violations"], [])

    def test_forbidden_phrasing_is_detected(self):
        payload = nav.analyze("I have a sore throat.")
        payload["questions"] = ["You have a bacterial infection."]
        nav.validate_response(payload)
        self.assertFalse(payload["safety"]["safe"])
        self.assertTrue(payload["safety"]["violations"])

    def test_emergency_message_never_diagnoses(self):
        payload = nav.analyze("Severe chest pain and shortness of breath.")
        self.assertEqual(payload["urgency"], nav.URGENCY_EMERGENCY)
        self.assertNotIn("you have", payload["urgency_message"].lower())
        self.assertEqual(payload["service_categories"][0]["key"],
                         "emergency-department")

    def test_urgent_message_has_no_claim_phrasing(self):
        payload = nav.analyze("I have been vomiting since last night.")
        self.assertEqual(payload["urgency"], nav.URGENCY_URGENT)
        # the template must not read as a claim ("you have X")
        self.assertNotIn("you have", payload["urgency_message"].lower())
        self.assertTrue(payload["safety"]["safe"])


class StructureInputTests(unittest.TestCase):
    def test_extracts_onset_and_frequency(self):
        s = nav.structure_input(
            "I have had a cough for the last 3 days. It is worse every morning. "
            "I am taking a cough syrup prescribed by my doctor.")
        self.assertIn("3 days", s["onset"])
        self.assertIn("every morning", s["frequency"])
        self.assertTrue(s["medications"])

    def test_extracts_allergy(self):
        s = nav.structure_input("I get a rash when I eat peanuts. I am allergic to them.")
        self.assertTrue(any("allerg" in m.lower() for m in s["allergies"]))


class FacilityMathTests(unittest.TestCase):
    def test_zero_distance_at_same_point(self):
        self.assertAlmostEqual(
            fac.haversine_km(12.97, 77.59, 12.97, 77.59), 0.0, places=6)

    def test_one_degree_latitude_about_111km(self):
        km = fac.haversine_km(12.97, 77.59, 13.97, 77.59)
        self.assertGreater(km, 105.0)
        self.assertLess(km, 116.0)

    def test_open_now_uses_injected_time(self):
        hours = {d: "09:00-18:00" for d in
                 ("mon", "tue", "wed", "thu", "fri", "sat")}
        # Monday 10:00 -> open; Monday 20:00 -> closed; Sunday -> closed
        mon = datetime(2026, 8, 10, 10, 0)
        self.assertTrue(fac.is_open_now(hours, mon))
        mon_late = datetime(2026, 8, 10, 20, 0)
        self.assertFalse(fac.is_open_now(hours, mon_late))
        sunday = datetime(2026, 8, 9, 10, 0)
        self.assertFalse(fac.is_open_now(hours, sunday))

    def test_unknown_hours_returns_none(self):
        self.assertIsNone(fac.is_open_now({}, datetime.now()))


class DocumentTests(unittest.TestCase):
    def test_classify_prescription(self):
        dtype, conf, terms = docs.classify_document(
            "Rx Tab. Amoxicillin 500 mg 1-0-1 for 5 days")
        self.assertEqual(dtype, "Prescription")
        self.assertGreater(conf, 0.5)
        self.assertTrue(terms)

    def test_classify_blood_report(self):
        dtype, _, _ = docs.classify_document(
            "Hemoglobin 13.2 g/dL, WBC 6800, Platelets 2.4 lakh, cholesterol 178")
        self.assertEqual(dtype, "Blood test report")

    def test_classify_empty_is_other(self):
        self.assertEqual(docs.classify_document("")[0], "Other")

    def test_summary_is_conservative(self):
        summary = docs.summarize_document(
            "Blood test report", "2026-08-08",
            "Hemoglobin: 13.2 g/dL  Glucose: 92 mg/dL")
        self.assertTrue(summary["measurements"])
        self.assertIn("framing", summary)
        self.assertNotIn("you have", summary["framing"].lower())
        self.assertTrue(summary["questions"])


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls._old_path = db.DB_PATH
        db.DB_PATH = os.path.join(cls._tmp, "test.db")
        db.init()

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls._old_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self.uid = db.create_user("Test User", "t@test.local", "+91 11111 11111",
                                  "secret1")

    def tearDown(self):
        db.delete_account(self.uid)

    def test_duplicate_email_rejected(self):
        with self.assertRaises(ValueError):
            db.create_user("Other", "t@test.local", "", "secret1")

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            db.create_user("X", "x@test.local", "", "abc")

    def test_authentication_roundtrip(self):
        self.assertIsNone(db.authenticate("t@test.local", "wrong"))
        user = db.authenticate("t@test.local", "secret1")
        self.assertEqual(user["id"], self.uid)

    def test_consent_records_and_completion(self):
        self.assertFalse(db.required_consents_complete(self.uid))
        for ctype in db.CONSENT_TYPES:
            if db.CONSENT_TYPES[ctype]["required"]:
                db.record_consent(self.uid, ctype, True)
        self.assertTrue(db.required_consents_complete(self.uid))
        records = db.get_consent_records(self.uid)
        self.assertTrue(all(r["version"] for r in records))
        current = db.current_consents(self.uid)
        self.assertEqual(current["terms"]["version"], "1.4")

    def test_cookie_and_privacy_prefs(self):
        db.set_cookie_prefs(self.uid, True, True, False)
        prefs = db.get_cookie_prefs(self.uid)
        self.assertTrue(prefs["analytics"])
        self.assertFalse(prefs["marketing"])
        db.set_privacy_pref(self.uid, "camera", True)
        self.assertTrue(db.get_privacy_prefs(self.uid)["camera"])

    def test_facility_seed_and_save(self):
        db.init_facilities(fac.SAMPLE_FACILITIES)
        all_f = db.get_all_facilities()
        self.assertGreaterEqual(len(all_f), 10)
        db.save_facility(self.uid, all_f[0].id)
        self.assertTrue(db.is_saved(self.uid, all_f[0].id))
        saved = db.get_saved_facilities(self.uid)
        self.assertEqual(saved[0].id, all_f[0].id)
        db.unsave_facility(self.uid, all_f[0].id)
        self.assertFalse(db.is_saved(self.uid, all_f[0].id))

    def test_appointment_flow(self):
        aid = db.add_appointment(self.uid, "Check-up", "2026-09-01 10:00",
                                 reminder_minutes=60)
        row = db.get_appointment(aid)
        self.assertEqual(row["title"], "Check-up")
        self.assertEqual(len(db.get_reminders(self.uid)), 1)
        db.update_appointment(aid, status="cancelled")
        self.assertEqual(len(db.get_upcoming_appointments(self.uid)), 0)
        db.delete_appointment(aid)

    def test_document_search(self):
        did = db.add_document(self.uid, "My blood report", "Blood test report",
                              "2026-07-01", tags=["blood"])
        hits = db.search_documents(self.uid, query="blood")
        self.assertEqual(len(hits), 1)
        hits = db.search_documents(self.uid, doc_type="Prescription")
        self.assertEqual(hits, [])
        db.delete_document(did)

    def test_timeline_and_export(self):
        db.add_appointment(self.uid, "Visit", "2026-08-20 09:00")
        db.add_document(self.uid, "Report", "Other", "2026-08-10")
        timeline = db.get_timeline(self.uid)
        self.assertEqual(len(timeline), 2)
        export = db.export_user_data(self.uid)
        self.assertIn("consents", export)
        self.assertIn("documents", export)

    def test_delete_user_data(self):
        db.add_document(self.uid, "Report", "Other", "2026-08-10")
        db.set_cookie_prefs(self.uid, True, False, False)
        paths = db.delete_user_data(self.uid)
        self.assertEqual(paths, [])
        self.assertEqual(db.get_documents(self.uid), [])
        # account still exists
        self.assertIsNotNone(db.get_user(self.uid))


if __name__ == "__main__":
    unittest.main()
