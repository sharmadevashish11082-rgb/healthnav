"""HealthNav — Healthcare Navigation Assistant (pure-Python desktop app).

Tkinter GUI + SQLite storage + optional EasyOCR document scanning.

Run:  python main.py
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import webbrowser
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import tkinter as tk

import database as db
import documents as docs
import facilities as fac
import navigation as nav
import theme

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "user_documents")
EMERGENCY_NUMBERS = "India: 112 (all emergencies)  ·  108 (ambulance)"

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import cv2  # noqa: F401
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

HAS_SPEECH = False
try:
    import speech_recognition  # noqa: F401
    HAS_SPEECH = True
except ImportError:
    HAS_SPEECH = False

PREP_ITEMS = [
    "Healthcare facility confirmed",
    "Appointment confirmed",
    "ID / required documents",
    "Previous relevant reports",
    "Current medication list",
    "Allergies information",
    "Questions prepared",
    "Important symptoms / timeline noted",
]

GENERIC_QUESTION_HINTS = (
    "What could be causing this?",
    "What information should I monitor?",
    "Are there tests you recommend?",
    "What should I do if it gets worse?",
    "When should I follow up?",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def fmt_when(when_at: str) -> str:
    """'2026-08-27 16:30' -> '27 Aug 2026 · 4:30 PM'"""
    try:
        dt = datetime.strptime(when_at, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return when_at or ""
    return dt.strftime("%d %b %Y · %-I:%M %p" if os.name != "nt" else "%d %b %Y · %I:%M %p")


def parse_when(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return None


def copy_to_clipboard(app, text: str) -> None:
    app.clipboard_clear()
    app.clipboard_append(text)
    app.update()


def open_browser(url: str) -> None:
    if url:
        webbrowser.open(url)


def dial_number(number: str) -> bool:
    """Try to open the native dialer; return False if unsupported."""
    try:
        if os.name == "nt":
            os.startfile(f"tel:{number.replace(' ', '')}")  # noqa: S606
            return True
        import subprocess
        subprocess.Popen(["xdg-open", f"tel:{number.replace(' ', '')}"])
        return True
    except Exception:
        return False


def directions_url(lat: float, lon: float, origin_lat: float | None = None,
                   origin_lon: float | None = None) -> str:
    base = "https://www.google.com/maps/dir/"
    if origin_lat is not None and origin_lon is not None:
        return (f"{base}?api=1&origin={origin_lat},{origin_lon}"
                f"&destination={lat},{lon}")
    return f"{base}?api=1&destination={lat},{lon}"


def card(parent, **kw) -> tk.Frame:
    kw.setdefault("bg", theme.PALETTE["surface"])
    kw.setdefault("highlightthickness", 1)
    kw.setdefault("highlightbackground", theme.PALETTE["border"])
    return tk.Frame(parent, **kw)


def heading(parent, text, size=15, color=None, pady=(0, 4)) -> tk.Label:
    try:
        bg = parent.cget("bg")
    except tk.TclError:
        bg = theme.PALETTE["bg"]
    lbl = tk.Label(parent, text=text, bg=bg,
                   font=(theme.FONT_FAMILY, size, "bold"),
                   fg=color or theme.PALETTE["text"])
    lbl.pack(anchor="w", pady=pady)
    return lbl


def user_location(app) -> tuple[float, float, str]:
    s = db.get_settings(app.user["id"])
    try:
        lat = float(s.get("location_lat") or fac.DEMO_LOCATION["lat"])
        lon = float(s.get("location_lon") or fac.DEMO_LOCATION["lon"])
    except (TypeError, ValueError):
        lat, lon = fac.DEMO_LOCATION["lat"], fac.DEMO_LOCATION["lon"]
    label = s.get("location_label") or fac.DEMO_LOCATION["label"]
    return lat, lon, label


# ---------------------------------------------------------------------------
# App root
# ---------------------------------------------------------------------------

class HealthApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HealthNav — Healthcare Navigation Assistant")
        self.geometry("1280x840")
        self.minsize(1100, 720)
        theme.apply_theme(self)
        self.user = None
        self.container = tk.Frame(self)
        self.container.pack(fill="both", expand=True)
        os.makedirs(DOCS_DIR, exist_ok=True)
        db.init()
        db.init_facilities(fac.SAMPLE_FACILITIES)
        self.show_auth()

    def _clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def show_auth(self):
        self._clear()
        AuthView(self.container, app=self).pack(fill="both", expand=True)

    def on_auth_success(self, user):
        self.user = user
        if db.required_consents_complete(user["id"]):
            self.show_main()
        else:
            self.show_consent()

    def show_consent(self):
        self._clear()
        ConsentView(self.container, app=self).pack(fill="both", expand=True)

    def on_consent_done(self):
        self.show_main()

    def show_main(self):
        self._clear()
        MainWindow(self.container, app=self).pack(fill="both", expand=True)

    def logout(self):
        self.user = None
        self.show_auth()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class AuthView(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.PALETTE["page"])
        self.app = app

        tk.Label(self, text="HealthNav", bg=theme.PALETTE["page"],
                 fg="white", font=(theme.FONT_FAMILY, 26, "bold")).pack(pady=(36, 0))
        tk.Label(self, text="Healthcare navigation — not a diagnosis",
                 bg=theme.PALETTE["page"], fg="#BFE3DF",
                 font=(theme.FONT_FAMILY, 11)).pack(pady=(0, 18))

        c = card(self, width=470, height=470)
        c.pack_propagate(False)
        c.place(relx=0.5, rely=0.54, anchor="center")

        nb = ttk.Notebook(c)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        self._build_login(nb)
        self._build_signup(nb)
        self.status = tk.Label(c, text="", bg=theme.PALETTE["surface"],
                               fg=theme.PALETTE["error"])
        self.status.pack(pady=(0, 6))

    # -------------------------------------------------------------- login
    def _build_login(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Login")
        inner = ttk.Frame(tab)
        inner.pack(fill="both", expand=True, padx=24, pady=18)

        ttk.Label(inner, text="Email or phone").pack(anchor="w")
        self.login_id = ttk.Entry(inner, width=40)
        self.login_id.pack(fill="x", pady=(2, 10))

        ttk.Label(inner, text="Password").pack(anchor="w")
        self.login_pass = ttk.Entry(inner, width=40, show="\u2022")
        self.login_pass.pack(fill="x", pady=(2, 14))

        ttk.Button(inner, text="Login", style="Accent.TButton",
                   command=self._do_login).pack(fill="x", pady=(0, 8))
        self.login_id.bind("<Return>", lambda e: self._do_login())
        self.login_pass.bind("<Return>", lambda e: self._do_login())

    def _do_login(self):
        identifier = self.login_id.get().strip()
        password = self.login_pass.get()
        user = db.authenticate(identifier, password)
        if not user:
            self.status.config(text="Invalid credentials. Try again.")
            return
        self.app.on_auth_success(user)

    # ------------------------------------------------------------- signup
    def _build_signup(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Create account")
        inner = ttk.Frame(tab)
        inner.pack(fill="both", expand=True, padx=24, pady=12)

        rows = [("Full name", "sg_name", False), ("Email", "sg_email", False),
                ("Phone", "sg_phone", False), ("Password", "sg_pass", True),
                ("Confirm password", "sg_pass2", True)]
        self.sg_vars = {}
        for label, key, secret in rows:
            ttk.Label(inner, text=label).pack(anchor="w")
            var = tk.StringVar()
            ent = ttk.Entry(inner, textvariable=var, show="\u2022" if secret else "")
            ent.pack(fill="x", pady=(2, 8))
            self.sg_vars[key] = var

        ttk.Button(inner, text="Create account", style="Accent.TButton",
                   command=self._do_signup).pack(fill="x", pady=(4, 0))

    def _do_signup(self):
        v = self.sg_vars
        name = v["sg_name"].get().strip()
        email = v["sg_email"].get().strip()
        phone = v["sg_phone"].get().strip()
        password = v["sg_pass"].get()
        confirm = v["sg_pass2"].get()
        if password != confirm:
            self.status.config(text="Passwords do not match.")
            return
        try:
            uid = db.create_user(name, email, phone, password)
        except ValueError as e:
            self.status.config(text=str(e))
            return
        self.app.on_auth_success(db.get_user(uid))


# ---------------------------------------------------------------------------
# Consent + cookies (spec sections 21-23)
# ---------------------------------------------------------------------------

class CookieDialog(tk.Toplevel):
    """Cookie / privacy preference centre."""

    def __init__(self, parent, app, initial=None):
        super().__init__(parent)
        self.title("Cookie Settings")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.app = app
        self.result = dict(initial or {})
        self.vars = {}
        initial = initial or {}

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        self._row(body, "Essential", db.COOKIE_CATEGORIES["essential"],
                  True, True)
        self._row(body, "Analytics", db.COOKIE_CATEGORIES["analytics"],
                  bool(initial.get("analytics")), False)
        self._row(body, "Preferences", db.COOKIE_CATEGORIES["preferences"],
                  bool(initial.get("preferences")), False)
        self._row(body, "Marketing", db.COOKIE_CATEGORIES["marketing"],
                  bool(initial.get("marketing")), False)

        btns = ttk.Frame(self)
        btns.pack(pady=(0, 12))
        ttk.Button(btns, text="Accept All",
                   command=lambda: self._apply(True, True, True)).pack(side="left", padx=4)
        ttk.Button(btns, text="Reject Non-Essential",
                   command=lambda: self._apply(False, False, False)).pack(side="left", padx=4)
        ttk.Button(btns, text="Save Preferences", style="Accent.TButton",
                   command=lambda: self._apply(
                       self.vars["analytics"].get(), self.vars["preferences"].get(),
                       self.vars["marketing"].get())).pack(side="left", padx=4)

    def _row(self, parent, name, description, value, disabled):
        var = tk.BooleanVar(value=value)
        self.vars[name.lower()] = var
        frame = tk.Frame(parent, bg=theme.PALETTE["surface2"],
                         highlightthickness=1, highlightbackground=theme.PALETTE["border"])
        frame.pack(fill="x", pady=3)
        cb = ttk.Checkbutton(frame, text=name, variable=var, state="disabled" if disabled else "normal")
        cb.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        tk.Label(frame, text=description, bg=theme.PALETTE["surface2"],
                 fg=theme.PALETTE["muted"], wraplength=380, justify="left",
                 font=(theme.FONT_FAMILY, 9)).grid(row=1, column=0, sticky="w",
                                                   padx=8, pady=(0, 6))

    def _apply(self, analytics, preferences, marketing):
        self.result = {"analytics": analytics, "preferences": preferences,
                       "marketing": marketing}
        self.destroy()


class ConsentView(tk.Frame):
    """Required + optional consent screen (spec section 21)."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=theme.PALETTE["page"])
        self.app = app

        tk.Label(self, text="Before continuing", bg=theme.PALETTE["page"],
                 fg="white", font=(theme.FONT_FAMILY, 22, "bold")).pack(pady=(30, 2))
        tk.Label(self, text="Please review and agree to the following",
                 bg=theme.PALETTE["page"], fg="#BFE3DF",
                 font=(theme.FONT_FAMILY, 11)).pack(pady=(0, 14))

        card_ = card(self, width=760, height=560)
        card_.pack_propagate(False)
        card_.place(relx=0.5, rely=0.55, anchor="center")

        tk.Label(card_, text="Required", bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9, "bold")
                 ).pack(anchor="w", padx=18, pady=(12, 2))

        self.req_vars = {}
        for ctype, info in db.CONSENT_TYPES.items():
            if not info["required"]:
                continue
            var = tk.BooleanVar(value=False)
            self.req_vars[ctype] = var
            cb = ttk.Checkbutton(card_, text=f"{info['label']}   (v{info['version']})",
                                 variable=var, command=self._sync)
            cb.pack(anchor="w", padx=24, pady=3)

        tk.Label(card_, text="Optional", bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9, "bold")
                 ).pack(anchor="w", padx=18, pady=(12, 2))
        self.opt_vars = {}
        for ctype, info in db.CONSENT_TYPES.items():
            if info["required"]:
                continue
            var = tk.BooleanVar(value=False)
            self.opt_vars[ctype] = var
            ttk.Checkbutton(card_, text=f"{info['label']}   (v{info['version']})",
                            variable=var).pack(anchor="w", padx=24, pady=3)

        self.cookie_prefs = {"analytics": False, "preferences": False,
                             "marketing": False}
        row = tk.Frame(card_, bg=theme.PALETTE["surface"])
        row.pack(fill="x", padx=24, pady=(14, 4))
        ttk.Button(row, text="\U0001f36a Cookie Settings",
                   command=self._open_cookies).pack(side="left")

        self.continue_btn = ttk.Button(card_, text="Continue",
                                       style="Accent.TButton",
                                       command=self._continue, state="disabled")
        self.continue_btn.pack(pady=14)
        self.hint = tk.Label(card_, text="Tick all required boxes to continue.",
                             bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"])
        self.hint.pack()

    def _sync(self):
        done = all(v.get() for v in self.req_vars.values())
        self.continue_btn.config(state="normal" if done else "disabled")
        self.hint.config(text="" if done else "Tick all required boxes to continue.")

    def _open_cookies(self):
        dlg = CookieDialog(self, self.app)
        self.wait_window(dlg)
        if dlg.result:
            self.cookie_prefs = dlg.result

    def _continue(self):
        uid = self.app.user["id"]
        for ctype, var in self.req_vars.items():
            db.record_consent(uid, ctype, var.get())
        for ctype, var in self.opt_vars.items():
            db.record_consent(uid, ctype, var.get())
        db.set_cookie_prefs(uid, **self.cookie_prefs)
        self.app.on_consent_done()


# ---------------------------------------------------------------------------
# Main window shell
# ---------------------------------------------------------------------------

class MainWindow(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.user = app.user
        self.views = {}

        # top bar
        top = tk.Frame(self, bg=theme.PALETTE["page"], height=52)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="HealthNav", bg=theme.PALETTE["page"], fg="white",
                 font=(theme.FONT_FAMILY, 15, "bold")).pack(side="left", padx=14)
        tk.Label(top, text="Healthcare navigation \u00b7 not a diagnosis",
                 bg=theme.PALETTE["page"], fg="#BFE3DF",
                 font=(theme.FONT_FAMILY, 9)).pack(side="left")
        tk.Label(top, text=f"Hi, {self.user['name'].split()[0]}",
                 bg=theme.PALETTE["page"], fg="white",
                 font=(theme.FONT_FAMILY, 10)).pack(side="right", padx=10)
        ttk.Button(top, text="Logout", style="Danger.TButton",
                   command=self.app.logout).pack(side="right", padx=(0, 12))
        ttk.Button(top, text="\U0001f6a8 Emergency", style="Danger.TButton",
                   command=lambda: self.show_view("safety")).pack(side="right", padx=6)

        # body
        body = tk.Frame(self)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, width=215, bg=theme.PALETTE["surface"],
                           highlightthickness=1, highlightbackground=theme.PALETTE["border"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self.nav_buttons = {}
        items = [
            ("home", "\U0001f3e0 Home"),
            ("navigate", "\U0001f9ed Navigate"),
            ("facilities", "\U0001f4cd Facilities"),
            ("appointments", "\U0001f4c5 Appointments"),
            ("documents", "\U0001f4c1 Documents"),
            ("timeline", "\U0001f552 Timeline"),
            ("safety", "\U0001f6a8 Safety"),
            ("settings", "\u2699\ufe0f Profile & Privacy"),
        ]
        for i, (key, label) in enumerate(items):
            btn = tk.Button(sidebar, text=label, anchor="w", relief="flat",
                            bg=theme.PALETTE["surface"], fg=theme.PALETTE["text"],
                            activebackground=theme.PALETTE["selection"],
                            activeforeground=theme.PALETTE["accent"],
                            font=(theme.FONT_FAMILY, 11), padx=16, pady=9,
                            command=lambda k=key: self.show_view(k))
            btn.pack(fill="x", pady=1)
            self.nav_buttons[key] = btn

        self.content = tk.Frame(body, bg=theme.PALETTE["bg"])
        self.content.pack(side="left", fill="both", expand=True)
        self.show_view("home")

    def show_view(self, name):
        for w in self.content.winfo_children():
            w.destroy()
        view = self.views.get(name)
        if view is None:
            cls = {
                "home": HomeView, "navigate": NavigationView,
                "facilities": FacilitiesView, "appointments": AppointmentsView,
                "documents": DocumentsView, "timeline": TimelineView,
                "safety": SafetyView, "settings": SettingsView,
            }[name]
            view = cls(self.content, app=self.app, main=self)
            self.views[name] = view
        view.pack(fill="both", expand=True)
        if hasattr(view, "refresh"):
            view.refresh()
        for key, btn in self.nav_buttons.items():
            btn.config(bg=theme.PALETTE["selection"] if key == name
                       else theme.PALETTE["surface"])


# ---------------------------------------------------------------------------
# Home dashboard (spec section 1)
# ---------------------------------------------------------------------------

class HomeView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main

    def refresh(self):
        for w in self.winfo_children():
            w.destroy()
        uid = self.app.user["id"]
        lat, lon, loc_label = user_location(self.app)

        heading(self, "Home Dashboard", size=18)

        now = datetime.now().hour
        greeting = ("Good morning" if now < 12 else "Good afternoon" if now < 17
                    else "Good evening")
        tk.Label(self, text=f"{greeting}, {self.app.user['name'].split()[0]}! "
                            f"(\U0001f4cd {loc_label})",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"],
                 font=(theme.FONT_FAMILY, 11)).pack(anchor="w", pady=(0, 10))

        stats = db.user_stats(uid)
        strip = tk.Frame(self, bg=theme.PALETTE["bg"])
        strip.pack(fill="x", pady=(0, 10))
        for label, value in (("Saved Facilities", stats["saved_facilities"]),
                             ("Upcoming Appointments", stats["upcoming"]),
                             ("Documents", stats["documents"]),
                             ("Consultation Notes", stats["consultation_notes"])):
            c = card(strip, width=170, height=64)
            c.pack_propagate(False)
            c.pack(side="left", padx=(0, 10))
            tk.Label(c, text=str(value), bg=theme.PALETTE["surface"],
                     fg=theme.PALETTE["accent"],
                     font=(theme.FONT_FAMILY, 20, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
            tk.Label(c, text=label, bg=theme.PALETTE["surface"],
                     fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9)
                     ).pack(anchor="w", padx=12)

        body = tk.Frame(self, bg=theme.PALETTE["bg"])
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=theme.PALETTE["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = tk.Frame(body, bg=theme.PALETTE["bg"], width=360)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        right.pack_propagate(False)

        # quick actions
        acts = card(left)
        acts.pack(fill="x", pady=(0, 10))
        heading(acts, "Main actions", size=12, pady=(8, 6))
        row = tk.Frame(acts, bg=theme.PALETTE["surface"])
        row.pack(fill="x", padx=10, pady=(0, 10))
        for text, target in (("\U0001f50d Find Healthcare", "facilities"),
                             ("\u270d\ufe0f Describe My Problem", "navigate"),
                             ("\U0001f4c5 My Appointments", "appointments"),
                             ("\U0001f4c1 My Medical Documents", "documents"),
                             ("\U0001f6a8 Emergency Help", "safety")):
            b = ttk.Button(row, text=text, command=lambda t=target: self.main.show_view(t))
            b.pack(side="left", padx=(0, 6))

        # upcoming appointment
        up = db.get_upcoming_appointments(uid)
        c = card(left)
        c.pack(fill="x", pady=(0, 10))
        heading(c, "\U0001f4c5 Upcoming appointment", size=12, pady=(8, 4))
        if up:
            a = up[0]
            tk.Label(c, text=a["title"], bg=theme.PALETTE["surface"],
                     font=(theme.FONT_FAMILY, 12, "bold")).pack(anchor="w", padx=12)
            tk.Label(c, text=f"{a.get('facility_name') or 'Facility not set'}  \u00b7  "
                             f"{fmt_when(a['when_at'])}",
                     bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"]
                     ).pack(anchor="w", padx=12)
            brow = tk.Frame(c, bg=theme.PALETTE["surface"])
            brow.pack(fill="x", padx=12, pady=(6, 10))
            ttk.Button(brow, text="View Details",
                       command=lambda aid=a["id"]: show_appointment_dialog(self, aid)
                       ).pack(side="left", padx=(0, 6))
            ttk.Button(brow, text="Prepare for Appointment",
                       command=self._prepare).pack(side="left")
        else:
            tk.Label(c, text="No upcoming appointments.",
                     bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"]
                     ).pack(anchor="w", padx=12, pady=(0, 10))

        # saved facilities
        c = card(left)
        c.pack(fill="x", pady=(0, 10))
        heading(c, "\U0001f4cc Saved healthcare facilities", size=12, pady=(8, 4))
        saved = db.get_saved_facilities(uid)
        if saved:
            for f in saved[:4]:
                dist = fac.haversine_km(lat, lon, f.lat, f.lon)
                row = tk.Frame(c, bg=theme.PALETTE["surface"])
                row.pack(fill="x", padx=12, pady=1)
                tk.Label(row, text=f"\u2713 {f.name}", bg=theme.PALETTE["surface"],
                         font=(theme.FONT_FAMILY, 10)).pack(side="left")
                tk.Label(row, text=f"{dist:.1f} km", bg=theme.PALETTE["surface"],
                         fg=theme.PALETTE["muted"]).pack(side="right")
        else:
            tk.Label(c, text="No saved facilities yet.", bg=theme.PALETTE["surface"],
                     fg=theme.PALETTE["muted"]).pack(anchor="w", padx=12, pady=(0, 8))

        # recent documents
        c = card(left)
        c.pack(fill="x")
        heading(c, "\U0001f4c1 Recently uploaded documents", size=12, pady=(8, 4))
        docs_ = db.get_documents(uid)[:4]
        if docs_:
            for d in docs_:
                tk.Label(c, text=f"\u2022 {d['title']}  \u00b7  {d['doc_type']}  \u00b7  "
                                 f"{d['date']}",
                         bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"],
                         font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=12, pady=1)
        else:
            tk.Label(c, text="No documents yet.", bg=theme.PALETTE["surface"],
                     fg=theme.PALETTE["muted"]).pack(anchor="w", padx=12, pady=(0, 8))

        # ---- right column ----
        # prep checklist
        c = card(right)
        c.pack(fill="x", pady=(0, 10))
        heading(c, "\u2705 Consultation preparation checklist", size=12, pady=(8, 4))
        saved_check = json.loads(db.get_setting(uid, "prep_checklist", "{}") or "{}")
        self.check_vars = {}
        for item in PREP_ITEMS:
            var = tk.BooleanVar(value=bool(saved_check.get(item, False)))
            self.check_vars[item] = var
            ttk.Checkbutton(c, text=item, variable=var,
                            command=lambda i=item, v=var: self._save_check(i, v)
                            ).pack(anchor="w", padx=14, pady=1)

        # reminders
        c = card(right)
        c.pack(fill="x", pady=(0, 10))
        heading(c, "\U0001f514 Reminders", size=12, pady=(8, 4))
        reminders = db.get_reminders(uid)
        if reminders:
            for r in reminders[:4]:
                tk.Label(c, text=f"\u23f0 {r['title']} \u00b7 {fmt_when(r['when_at'])}",
                         bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"],
                         font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=12, pady=1)
        else:
            tk.Label(c, text="No active reminders.", bg=theme.PALETTE["surface"],
                     fg=theme.PALETTE["muted"]).pack(anchor="w", padx=12, pady=(0, 8))

        # important notices
        c = card(right)
        c.pack(fill="x")
        heading(c, "\u26a0\ufe0f Important", size=12, pady=(8, 4))
        tk.Label(c, text=nav.DISCLAIMER, bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["muted"], wraplength=330, justify="left",
                 font=(theme.FONT_FAMILY, 9)).pack(anchor="w", padx=12, pady=(0, 10))

    def _save_check(self, item, var):
        uid = self.app.user["id"]
        saved = json.loads(db.get_setting(uid, "prep_checklist", "{}") or "{}")
        saved[item] = bool(var.get())
        db.set_setting(uid, "prep_checklist", json.dumps(saved))

    def _prepare(self):
        PrepareDialog(self, self.app)


class PrepareDialog(tk.Toplevel):
    """Consultation preparation: questions + what to bring."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.title("Prepare for your consultation")
        self.transient(parent)
        self.grab_set()
        self.app = app
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        heading(body, "\u2753 Questions to ask", size=13)
        history = db.get_navigation_history(app.user["id"], limit=1)
        questions = history[0]["questions"] if history else list(GENERIC_QUESTION_HINTS)
        self.q_vars = []
        for q in questions:
            var = tk.BooleanVar(value=True)
            self.q_vars.append((q, var))
            ttk.Checkbutton(body, text=q, variable=var).pack(anchor="w", padx=8, pady=1)

        heading(body, "\U0001f4c4 Bring with you", size=13, pady=(12, 2))
        for item in ("ID / required documents", "Previous relevant reports",
                     "Current medication list", "Allergies information",
                     "Symptoms & timeline notes"):
            tk.Label(body, text=f"\u2022 {item}", bg=theme.PALETTE["bg"],
                     font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=8)

        btns = ttk.Frame(body)
        btns.pack(pady=10)
        ttk.Button(btns, text="Copy checked questions",
                   command=self._copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=4)

    def _copy(self):
        text = "\n".join(q for q, var in self.q_vars if var.get())
        copy_to_clipboard(self.app, text)
        messagebox.showinfo("Copied", "Selected questions copied to clipboard.")


# ---------------------------------------------------------------------------
# Navigation assistant (spec sections 2-5, 11, 30, 41)
# ---------------------------------------------------------------------------

class NavigationView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main
        self.last = None
        self._build()

    def _build(self):
        heading(self, "Describe your healthcare need", size=18)
        tk.Label(self, text=nav.DISCLAIMER, bg=theme.PALETTE["bg"],
                 fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9)
                 ).pack(anchor="w", pady=(0, 8))

        box_card = card(self)
        box_card.pack(fill="x", pady=(0, 8))
        self.input_box = tk.Text(box_card, height=6, wrap="word",
                                 bg=theme.PALETTE["surface"],
                                 fg=theme.PALETTE["text"],
                                 insertbackground=theme.PALETTE["text"],
                                 relief="flat", highlightthickness=1,
                                 highlightbackground=theme.PALETTE["border"],
                                 highlightcolor=theme.PALETTE["accent"],
                                 font=(theme.FONT_FAMILY, 12))
        self.input_box.pack(fill="x", padx=8, pady=8)
        self.input_box.insert("1.0", "I have been having a sore throat and want to see someone.")

        row = tk.Frame(self, bg=theme.PALETTE["bg"])
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="\U0001f50d Navigate", style="Accent.TButton",
                   command=self._analyze).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="\U0001f4cb Guided questionnaire",
                   command=self._questionnaire).pack(side="left", padx=(0, 6))
        self.voice_btn = ttk.Button(row, text="\U0001f3a4 Voice input",
                                    command=self._voice)
        self.voice_btn.pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Clear", command=lambda: self.input_box.delete("1.0", "end")
                   ).pack(side="left")
        tk.Label(row, text="(Optional) attach a report later under Documents.",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"],
                 font=(theme.FONT_FAMILY, 9)).pack(side="left", padx=10)

        self.result_card = card(self)
        self.result_card.pack(fill="both", expand=True)
        tk.Label(self.result_card, text="Describe your concern to get navigation help.",
                 bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"],
                 font=(theme.FONT_FAMILY, 11)).pack(pady=40)

    # ------------------------------------------------------------- actions
    def _analyze(self):
        text = self.input_box.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Nothing entered", "Describe your concern first.")
            return
        payload = nav.analyze(text)
        self.last = payload
        db.add_navigation(self.app.user["id"], text, payload["urgency"],
                          [s["key"] for s in payload["service_categories"]],
                          payload["questions"])
        self._render(payload)

    def _questionnaire(self):
        dlg = QuestionnaireDialog(self, self.app)
        self.wait_window(dlg)
        if dlg.combined:
            self.input_box.delete("1.0", "end")
            self.input_box.insert("1.0", dlg.combined)
            self._analyze()

    def _voice(self):
        if not HAS_SPEECH:
            messagebox.showinfo(
                "Voice input",
                "Voice input needs the optional package:\n\n"
                "  pip install SpeechRecognition\n\n"
                "(and a working microphone).")
            return
        self.voice_btn.config(state="disabled", text="\U0001f3a4 Listening\u2026")
        threading.Thread(target=self._voice_worker, daemon=True).start()

    def _voice_worker(self):
        err, text = None, None
        try:
            import speech_recognition as sr
            rec = sr.Recognizer()
            with sr.Microphone() as source:
                rec.adjust_for_ambient_noise(source, duration=0.5)
                audio = rec.listen(source, timeout=8, phrase_time_limit=30)
            text = rec.recognize_google(audio)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        self.app.after(0, lambda: self._voice_done(text, err))

    def _voice_done(self, text, err):
        self.voice_btn.config(state="normal", text="\U0001f3a4 Voice input")
        if text:
            self.input_box.delete("1.0", "end")
            self.input_box.insert("1.0", text)
        elif err:
            messagebox.showwarning("Voice input",
                                   "Could not capture speech:\n" + err)

    # ------------------------------------------------------------ render
    def _render(self, p):
        for w in self.result_card.winfo_children():
            w.destroy()
        urgency = p["urgency"]

        colors = {nav.URGENCY_EMERGENCY: theme.PALETTE["error"],
                  nav.URGENCY_URGENT: theme.PALETTE["warning"],
                  nav.URGENCY_ROUTINE: theme.PALETTE["accent"],
                  nav.URGENCY_INFORMATION: theme.PALETTE["muted"]}
        banner = tk.Frame(self.result_card, bg=colors[urgency],
                          highlightthickness=0)
        banner.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(banner, text=f"{p['urgency_label'].upper()} \u2014 seek this "
                              f"level of care",
                 bg=colors[urgency], fg="white",
                 font=(theme.FONT_FAMILY, 11, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
        msg = p["urgency_message"] or (
            "Schedule an appropriate consultation and prepare for it.")
        tk.Label(banner, text=msg, bg=colors[urgency], fg="white",
                 wraplength=860, justify="left",
                 font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=12, pady=(0, 6))

        tk.Label(self.result_card, text=nav.NO_DIAGNOSIS_NOTE,
                 bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"],
                 font=(theme.FONT_FAMILY, 9, "italic")).pack(anchor="w", padx=14, pady=4)

        if urgency == nav.URGENCY_EMERGENCY:
            row = tk.Frame(self.result_card, bg=theme.PALETTE["surface"])
            row.pack(anchor="w", padx=14, pady=6)
            ttk.Button(row, text="\U0001f6a8 Find emergency facilities",
                       style="Danger.TButton",
                       command=self._go_emergency).pack(side="left", padx=(0, 6))
            ttk.Button(row, text="Copy emergency numbers",
                       command=lambda: (copy_to_clipboard(self.app, EMERGENCY_NUMBERS),
                                        messagebox.showinfo(
                                            "Copied", EMERGENCY_NUMBERS))).pack(side="left")
            return

        # structured view
        s = p["structure"]
        if s["onset"] or s["frequency"] or s["medications"] or s["allergies"]:
            struct = tk.Frame(self.result_card, bg=theme.PALETTE["surface2"],
                              highlightthickness=1,
                              highlightbackground=theme.PALETTE["border"])
            struct.pack(fill="x", padx=14, pady=4)
            tk.Label(struct, text="Structured information", bg=theme.PALETTE["surface2"],
                     fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9, "bold")
                     ).pack(anchor="w", padx=10, pady=(6, 2))
            for label, val in (("Started", s["onset"]), ("Frequency", s["frequency"])):
                if val:
                    tk.Label(struct, text=f"\u2022 {label}: {val}",
                             bg=theme.PALETTE["surface2"],
                             font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=10)
            for kind, items in (("Medications", s["medications"]),
                                ("Allergies", s["allergies"])):
                for it in items:
                    tk.Label(struct, text=f"\u2022 {kind}: {it}",
                             bg=theme.PALETTE["surface2"],
                             font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=10)
            tk.Label(struct, text="\u00b7 Review this before your visit \u2014 "
                                  "correct it with your clinician.",
                     bg=theme.PALETTE["surface2"], fg=theme.PALETTE["muted"],
                     font=(theme.FONT_FAMILY, 8, "italic")
                     ).pack(anchor="w", padx=10, pady=(2, 6))

        tk.Label(self.result_card, text="What you may need", bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["text"],
                 font=(theme.FONT_FAMILY, 13, "bold")).pack(anchor="w", padx=14, pady=(8, 2))
        for svc in p["service_categories"]:
            c = tk.Frame(self.result_card, bg=theme.PALETTE["surface2"],
                         highlightthickness=1,
                         highlightbackground=theme.PALETTE["border"])
            c.pack(fill="x", padx=14, pady=3)
            tk.Label(c, text=svc["label"], bg=theme.PALETTE["surface2"],
                     fg=theme.PALETTE["accent"],
                     font=(theme.FONT_FAMILY, 11, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
            tk.Label(c, text=svc["description"], bg=theme.PALETTE["surface2"],
                     fg=theme.PALETTE["muted"], wraplength=860, justify="left",
                     font=(theme.FONT_FAMILY, 9)).pack(anchor="w", padx=10, pady=(0, 6))

        tk.Label(self.result_card, text="What you can do", bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["text"],
                 font=(theme.FONT_FAMILY, 13, "bold")).pack(anchor="w", padx=14, pady=(8, 2))
        for action in p["what_you_can_do"]:
            tk.Label(self.result_card, text=f"\u261e {action}",
                     bg=theme.PALETTE["surface"], fg=theme.PALETTE["text"],
                     wraplength=860, justify="left",
                     font=(theme.FONT_FAMILY, 10)).pack(anchor="w", padx=14, pady=1)

        tk.Label(self.result_card, text="Questions to ask your clinician",
                 bg=theme.PALETTE["surface"], fg=theme.PALETTE["text"],
                 font=(theme.FONT_FAMILY, 13, "bold")).pack(anchor="w", padx=14, pady=(8, 2))
        self.q_vars = []
        for q in p["questions"]:
            var = tk.BooleanVar(value=True)
            self.q_vars.append((q, var))
            ttk.Checkbutton(self.result_card, text=q, variable=var
                            ).pack(anchor="w", padx=18, pady=1)

        row = tk.Frame(self.result_card, bg=theme.PALETTE["surface"])
        row.pack(fill="x", padx=14, pady=(10, 12))
        ttk.Button(row, text="\U0001f4cd Find nearby facilities",
                   style="Accent.TButton",
                   command=lambda: self.main.show_view("facilities")
                   ).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Copy checked questions",
                   command=self._copy_questions).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="\u2705 Preparation checklist",
                   command=lambda: PrepareDialog(self, self.app)).pack(side="left")

    def _go_emergency(self):
        self.main.show_view("facilities")
        self.main.views["facilities"].set_emergency_filter()

    def _copy_questions(self):
        text = "\n".join(q for q, var in self.q_vars if var.get())
        copy_to_clipboard(self.app, text)
        messagebox.showinfo("Copied", "Selected questions copied to clipboard.")


class QuestionnaireDialog(tk.Toplevel):
    """Guided questionnaire (spec section 3)."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.title("Guided questionnaire")
        self.transient(parent)
        self.grab_set()
        self.app = app
        self.combined = ""
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        fields = [
            ("Main concern *", "concern"),
            ("When did it start?", "onset"),
            ("How often does it occur?", "frequency"),
            ("Relevant symptoms", "symptoms"),
            ("Current medications", "medications"),
            ("Allergies", "allergies"),
        ]
        self.vars = {}
        for label, key in fields:
            ttk.Label(body, text=label).pack(anchor="w")
            var = tk.StringVar()
            ent = ttk.Entry(body, textvariable=var, width=56)
            ent.pack(fill="x", pady=(2, 8))
            self.vars[key] = var

        ttk.Label(body, text="This information is for healthcare navigation and "
                             "preparation only. It is not a medical diagnosis.",
                  fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9)).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(pady=10)
        ttk.Button(row, text="Continue", style="Accent.TButton",
                   command=self._ok).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def _ok(self):
        v = self.vars
        concern = v["concern"].get().strip()
        if not concern:
            messagebox.showwarning("Missing concern",
                                   "Please describe your main concern.")
            return
        parts = [concern]
        for label, key in (("Started", "onset"), ("Frequency", "frequency"),
                           ("Symptoms", "symptoms"), ("Medications", "medications"),
                           ("Allergies", "allergies")):
            val = v[key].get().strip()
            if val:
                parts.append(f"{label}: {val}")
        self.combined = " ".join(parts)
        self.destroy()


# ---------------------------------------------------------------------------
# Facilities + map (spec sections 6-8, 27-28)
# ---------------------------------------------------------------------------

class FacilitiesView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main
        self.facilities = []
        self.selected_id = None
        self._build()

    def _build(self):
        heading(self, "Nearby Healthcare", size=18)

        # filters
        filters = ttk.LabelFrame(self, text="Filters")
        filters.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(filters)
        row.pack(fill="x", padx=8, pady=6)
        self.f_search = tk.StringVar()
        ttk.Label(row, text="Search:").pack(side="left")
        ttk.Entry(row, textvariable=self.f_search, width=18).pack(side="left", padx=(2, 8))
        types = sorted({f.facility_type for f in fac.SAMPLE_FACILITIES})
        self.f_type = tk.StringVar(value="All types")
        ttk.Combobox(row, textvariable=self.f_type,
                     values=["All types"] + types, state="readonly",
                     width=14).pack(side="left", padx=(0, 8))
        self.f_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Open now", variable=self.f_open).pack(side="left", padx=(0, 8))
        self.f_emergency = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Emergency only",
                        variable=self.f_emergency).pack(side="left", padx=(0, 8))
        self.f_sort = tk.StringVar(value="Distance")
        ttk.Combobox(row, textvariable=self.f_sort,
                     values=["Distance", "Rating"], state="readonly",
                     width=10).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Apply", command=self.refresh).pack(side="left", padx=2)
        ttk.Button(row, text="Clear", command=self._clear_filters).pack(side="left", padx=2)

        body = tk.Frame(self, bg=theme.PALETTE["bg"])
        body.pack(fill="both", expand=True)

        # list
        left = tk.Frame(body, bg=theme.PALETTE["bg"], width=430)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        left.pack_propagate(False)
        cols = ("name", "type", "dist", "rating", "status")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="browse")
        for c, text, w in (("name", "Facility", 150), ("type", "Type", 100),
                           ("dist", "Distance", 60), ("rating", "Rating", 50),
                           ("status", "Status", 70)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self._details())

        # map + details
        right = tk.Frame(body, bg=theme.PALETTE["bg"])
        right.pack(side="left", fill="both", expand=True)
        self.map = MapCanvas(right, self.app, on_select=self._select_facility)
        self.map.pack(fill="x", pady=(0, 6))
        zoom_row = tk.Frame(right, bg=theme.PALETTE["bg"])
        zoom_row.pack(anchor="w", pady=(0, 6))
        ttk.Button(zoom_row, text="\U0001f50d Zoom in", command=self.map.zoom_in
                   ).pack(side="left", padx=(0, 4))
        ttk.Button(zoom_row, text="\U0001f50e Zoom out", command=self.map.zoom_out
                   ).pack(side="left", padx=(0, 4))
        tk.Label(zoom_row, text="You are at the blue marker. Click a pin to select.",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"],
                 font=(theme.FONT_FAMILY, 9)).pack(side="left", padx=8)

        self.detail = card(right)
        self.detail.pack(fill="both", expand=True)
        self._render_detail(None)

        self.refresh()

    def _clear_filters(self):
        self.f_search.set("")
        self.f_type.set("All types")
        self.f_open.set(False)
        self.f_emergency.set(False)
        self.f_sort.set("Distance")
        self.refresh()

    def set_emergency_filter(self):
        self.f_emergency.set(True)
        self.refresh()

    # ------------------------------------------------------------ refresh
    def refresh(self):
        uid = self.app.user["id"]
        lat, lon, _ = user_location(self.app)
        query = self.f_search.get().strip().lower()
        ftype = self.f_type.get()
        open_only = self.f_open.get()
        emergency_only = self.f_emergency.get()

        facilities = db.get_all_facilities()
        rows = []
        for f in facilities:
            if ftype != "All types" and f.facility_type != ftype:
                continue
            if emergency_only and not f.emergency:
                continue
            if query and query not in f.name.lower() and \
                    query not in f.facility_type.lower():
                continue
            dist = fac.haversine_km(lat, lon, f.lat, f.lon)
            open_now = fac.is_open_now(f.opening_hours)
            if open_only and open_now is not True:
                continue
            rows.append((f, dist, open_now))

        if self.f_sort.get() == "Rating":
            rows.sort(key=lambda r: -r[0].rating)
        else:
            rows.sort(key=lambda r: r[1])
        self.facilities = [r[0] for r in rows]

        self.tree.delete(*self.tree.get_children())
        for f, dist, open_now in rows:
            status = ("Open" if open_now is True else
                      "Closed" if open_now is False else "\u2014")
            self.tree.insert("", "end", iid=str(f.id),
                             values=(f.name, f.facility_type,
                                     f"{dist:.1f} km", f"{f.rating:.1f}", status))
        self.map.set_facilities(self.facilities, self.selected_id)
        if self.facilities and self.selected_id is None:
            self.selected_id = self.facilities[0].id
            self.map.set_facilities(self.facilities, self.selected_id)
        self._render_detail(self._selected())

    def _selected(self):
        for f in self.facilities:
            if f.id == self.selected_id:
                return f
        return self.facilities[0] if self.facilities else None

    # ------------------------------------------------------------ events
    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if sel:
            self.selected_id = int(sel[0])
            self.map.set_facilities(self.facilities, self.selected_id)
            self._render_detail(self._selected())

    def _select_facility(self, fid):
        self.selected_id = fid
        if self.tree.exists(str(fid)):
            self.tree.selection_set(str(fid))
            self.tree.see(str(fid))
        self._render_detail(self._selected())

    def _details(self):
        f = self._selected()
        if f:
            show_facility_dialog(self, self.app, f)

    def _render_detail(self, f):
        for w in self.detail.winfo_children():
            w.destroy()
        if not f:
            tk.Label(self.detail, text="Select a facility to see details.",
                     bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"]
                     ).pack(pady=20)
            return
        uid = self.app.user["id"]
        lat, lon, _ = user_location(self.app)
        dist = fac.haversine_km(lat, lon, f.lat, f.lon)
        open_now = fac.is_open_now(f.opening_hours)
        status = ("\u25cf Open now" if open_now is True else
                  "\u25cf Closed now" if open_now is False else
                  "Hours unknown")

        head = tk.Frame(self.detail, bg=theme.PALETTE["surface"])
        head.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(head, text=f.name, bg=theme.PALETTE["surface"],
                 font=(theme.FONT_FAMILY, 13, "bold")).pack(side="left")
        badge = f.verification_label()
        if f.verification_level == "officially_verified":
            tk.Label(head, text="\u2713 Verified", bg="#E4F4EF",
                     fg=theme.PALETTE["accent"], font=(theme.FONT_FAMILY, 8, "bold")
                     ).pack(side="left", padx=8)

        info = tk.Frame(self.detail, bg=theme.PALETTE["surface"])
        info.pack(fill="x", padx=10)
        lines = [
            f"{f.facility_type}  \u00b7  {f'{dist:.1f} km'}  \u00b7  {status}",
            f"\u2b50 {f.rating:.1f} ({f.review_count} reviews)",
            f"\U0001f4cd {f.address}",
            f"\U0001f4de {f.phone}",
            " \u00b7 ".join(f.services),
        ]
        for line in lines:
            tk.Label(info, text=line, bg=theme.PALETTE["surface"],
                     fg=theme.PALETTE["text"], wraplength=560, justify="left",
                     font=(theme.FONT_FAMILY, 10)).pack(anchor="w", pady=1)

        row = tk.Frame(self.detail, bg=theme.PALETTE["surface"])
        row.pack(fill="x", padx=10, pady=(8, 10))
        ttk.Button(row, text="Details", style="Accent.TButton",
                   command=self._details).pack(side="left", padx=(0, 5))
        ttk.Button(row, text="Directions",
                   command=lambda: open_browser(directions_url(
                       f.lat, f.lon, lat, lon))).pack(side="left", padx=(0, 5))
        ttk.Button(row, text="\U0001f4de Call",
                   command=lambda: self._call(f)).pack(side="left", padx=(0, 5))
        saved = db.is_saved(uid, f.id)
        ttk.Button(row, text="\u2605 Saved" if saved else "\u2606 Save",
                   command=lambda: self._toggle_save(f)).pack(side="left")

    def _call(self, f):
        if not dial_number(f.phone):
            copy_to_clipboard(self.app, f.phone)
            messagebox.showinfo("Call", f"Dial {f.phone}\n(Number copied to clipboard.)")

    def _toggle_save(self, f):
        uid = self.app.user["id"]
        if db.is_saved(uid, f.id):
            db.unsave_facility(uid, f.id)
        else:
            db.save_facility(uid, f.id)
        self._render_detail(self._selected())


class MapCanvas(tk.Canvas):
    """Schematic offline map: user at centre, facility pins around it."""

    def __init__(self, parent, app, on_select=None, height=300):
        super().__init__(parent, bg="#E9F4F2", highlightthickness=0, height=height)
        self.app = app
        self.on_select = on_select
        self.facilities = []
        self.markers = {}
        self.selected_id = None
        self.zoom = 1.0
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._click)

    def user_center(self):
        return user_location(self.app)[:2]

    def set_facilities(self, facilities, selected_id=None):
        self.facilities = facilities
        self.selected_id = selected_id
        self.draw()

    def draw(self):
        self.delete("all")
        self.markers = {}
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        u_lat, u_lon = self.user_center()
        deg = 0.045 / self.zoom
        px_per_lat = h / deg
        px_per_lon = w / deg

        def to_xy(lat, lon):
            return ((lon - u_lon) * px_per_lon + w / 2,
                    (u_lat - lat) * px_per_lat + h / 2)

        step = 80
        for x in range(0, w, step):
            self.create_line(x, 0, x, h, fill="#D8E8E5")
        for y in range(0, h, step):
            self.create_line(0, y, w, y, fill="#D8E8E5")

        ux, uy = to_xy(u_lat, u_lon)
        self.create_oval(ux - 7, uy - 7, ux + 7, uy + 7, fill="#2563EB",
                         outline="white", width=2)
        self.create_text(ux, uy - 14, text="You", fill="#1E3A8A",
                         font=(theme.FONT_FAMILY, 9, "bold"))

        for f in self.facilities:
            x, y = to_xy(f.lat, f.lon)
            if x < -40 or x > w + 40 or y < -40 or y > h + 40:
                continue
            color = (theme.PALETTE["error"] if f.emergency else
                     ("#F59E0B" if f.id == self.selected_id else
                      theme.PALETTE["accent"]))
            r = 8 if f.emergency else 6
            oval = self.create_oval(x - r, y - r, x + r, y + r, fill=color,
                                    outline="white", width=2)
            lab = self.create_text(x, y, text=f.name[0].upper(), fill="white",
                                   font=(theme.FONT_FAMILY, 8, "bold"))
            self.create_text(x, y + 14, text=f.name[:20], fill="#334155",
                             font=(theme.FONT_FAMILY, 8))
            self.markers[oval] = f.id
            self.markers[lab] = f.id

    def _click(self, event):
        item = self.find_withtag("current")
        if item:
            fid = self.markers.get(item[0])
            if fid is not None and self.on_select:
                self.selected_id = fid
                self.draw()
                self.on_select(fid)

    def zoom_in(self):
        self.zoom = min(self.zoom * 1.6, 10.0)
        self.draw()

    def zoom_out(self):
        self.zoom = max(self.zoom / 1.6, 0.35)
        self.draw()


def show_facility_dialog(parent, app, f):
    """Full facility profile (spec section 8) as a dialog."""
    uid = app.user["id"]
    db.record_facility_view(uid, f.id)
    lat, lon, _ = user_location(app)
    dist = fac.haversine_km(lat, lon, f.lat, f.lon)
    open_now = fac.is_open_now(f.opening_hours)

    dlg = tk.Toplevel(parent)
    dlg.title(f.name)
    dlg.geometry("640x640")
    dlg.transient(parent)
    dlg.grab_set()
    body = ttk.Frame(dlg)
    body.pack(fill="both", expand=True, padx=14, pady=12)

    tk.Label(body, text=f.name, font=(theme.FONT_FAMILY, 17, "bold"),
             bg=theme.PALETTE["bg"]).pack(anchor="w")
    tk.Label(body, text=f"{f.facility_type}  \u00b7  {f'{dist:.1f} km'}",
             fg=theme.PALETTE["muted"], bg=theme.PALETTE["bg"]
             ).pack(anchor="w", pady=(0, 6))

    sec = ttk.LabelFrame(body, text="Basic information")
    sec.pack(fill="x", pady=4)
    status = ("Open now" if open_now is True else
              "Closed now" if open_now is False else "Hours unknown")
    for label, val in (("Address", f.address), ("Phone", f.phone),
                       ("Website", f.website or "\u2014"),
                       ("Opening hours", status),
                       ("Rating", f"\u2b50 {f.rating:.1f} ({f.review_count} reviews)"),
                       ("Verification", f.verification_label()),
                       ("Last verified", f.verified_date or "\u2014")):
        row = ttk.Frame(sec)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label + ":", width=14).pack(side="left")
        tk.Label(row, text=str(val), bg=theme.PALETTE["bg"],
                 font=(theme.FONT_FAMILY, 10)).pack(side="left")

    sec = ttk.LabelFrame(body, text="Healthcare information")
    sec.pack(fill="x", pady=4)
    for label, val in (("Services", ", ".join(f.services)),
                       ("Appointment methods", ", ".join(f.appointment_methods) or "\u2014"),
                       ("Accessibility", ", ".join(f.accessibility) or "\u2014"),
                       ("Emergency services", "Yes" if f.emergency else "No"),
                       ("Description", f.description or "\u2014")):
        row = ttk.Frame(sec)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label + ":", width=18).pack(side="left")
        tk.Label(row, text=str(val), bg=theme.PALETTE["bg"], wraplength=420,
                 justify="left", font=(theme.FONT_FAMILY, 10)).pack(side="left")

    acts = ttk.Frame(body)
    acts.pack(fill="x", pady=(10, 4))
    ttk.Button(acts, text="\U0001f4de Call",
               command=lambda: _dlg_call(app, dlg, f)).pack(side="left", padx=(0, 5))
    ttk.Button(acts, text="\U0001f5fa Directions",
               command=lambda: open_browser(directions_url(f.lat, f.lon, lat, lon))
               ).pack(side="left", padx=(0, 5))
    ttk.Button(acts, text="\U0001f310 Website",
               command=lambda: open_browser(f.website)).pack(side="left", padx=(0, 5))
    ttk.Button(acts, text="\U0001f4c5 Book appointment",
               command=lambda: _dlg_book(app, f)).pack(side="left", padx=(0, 5))
    ttk.Button(acts, text="\U0001f4e4 Share",
               command=lambda: _dlg_share(app, f, dist)).pack(side="left", padx=(0, 5))
    saved = db.is_saved(uid, f.id)
    ttk.Button(acts, text="\u2605 Saved" if saved else "\u2606 Save",
               command=lambda: _dlg_save(app, dlg, f)).pack(side="left", padx=(0, 5))
    ttk.Button(acts, text="Feedback",
               command=lambda: FeedbackDialog(dlg, app, f)).pack(side="left")

    tk.Label(body, text="Hours: " + fac.opening_hours_text(f.opening_hours),
             fg=theme.PALETTE["muted"], bg=theme.PALETTE["bg"], wraplength=580,
             justify="left", font=(theme.FONT_FAMILY, 9)).pack(anchor="w", pady=(10, 0))


def _dlg_call(app, dlg, f):
    if not dial_number(f.phone):
        copy_to_clipboard(app, f.phone)
        messagebox.showinfo("Call", f"Dial {f.phone}\n(Number copied to clipboard.)",
                            parent=dlg)


def _dlg_book(app, f):
    methods = ", ".join(f.appointment_methods) or "not listed"
    messagebox.showinfo(
        "Book appointment",
        f"{f.name}\n\nBooking options: {methods}\n"
        f"Phone: {f.phone}\nWebsite: {f.website or 'n/a'}\n\n"
        "Open the facility details and use the website/phone to book. "
        "This app does not yet provide its own booking system.")


def _dlg_share(app, f, dist):
    text = (f"{f.name} \u2014 {f.facility_type}\n"
            f"Distance: {dist:.1f} km\nAddress: {f.address}\n"
            f"Phone: {f.phone}\nRating: {f.rating:.1f} ({f.review_count} reviews)")
    copy_to_clipboard(app, text)
    messagebox.showinfo("Shared", "Facility details copied to clipboard.")


def _dlg_save(app, dlg, f):
    if db.is_saved(app.user["id"], f.id):
        db.unsave_facility(app.user["id"], f.id)
    else:
        db.save_facility(app.user["id"], f.id)
    dlg.destroy()
    messagebox.showinfo("Saved", f"{f.name} updated in My Healthcare.")


class FeedbackDialog(tk.Toplevel):
    """Facility feedback / information-accuracy feedback (spec section 28)."""

    def __init__(self, parent, app, f):
        super().__init__(parent)
        self.title("Feedback")
        self.transient(parent)
        self.grab_set()
        self.app = app
        self.f = f
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=10)
        ttk.Label(body, text=f"Feedback for {f.name}").pack(anchor="w")
        self.category = tk.StringVar(value="general")
        ttk.Combobox(body, textvariable=self.category, state="readonly", width=22,
                     values=["general", "information accuracy", "appointment experience",
                             "accessibility"]).pack(anchor="w", pady=(6, 2))
        self.comment = tk.Text(body, height=4, width=52)
        self.comment.pack(anchor="w", pady=4)
        row = ttk.Frame(body)
        row.pack(anchor="w", pady=6)
        ttk.Button(row, text="Submit", style="Accent.TButton",
                   command=self._submit).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")

    def _submit(self):
        comment = self.comment.get("1.0", "end").strip()
        if not comment:
            messagebox.showwarning("Empty feedback", "Write something first.")
            return
        db.add_feedback(self.app.user["id"], self.f.id, self.category.get(),
                        comment)
        messagebox.showinfo("Thank you", "Feedback submitted.")
        self.destroy()


# ---------------------------------------------------------------------------
# Appointments (spec sections 9-10)
# ---------------------------------------------------------------------------

class AppointmentsView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main
        self._build()

    def _build(self):
        heading(self, "My Appointments", size=18)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 6))
        ttk.Button(row, text="\u2795 Add appointment", style="Accent.TButton",
                   command=lambda: AppointmentDialog(self, self.app,
                                                     on_save=self.refresh)
                   ).pack(side="left")

        cols = ("title", "facility", "when", "doctor", "status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                 selectmode="browse")
        for c, text, w in (("title", "Title", 200), ("facility", "Facility", 160),
                           ("when", "Date & time", 170), ("doctor", "Doctor", 140),
                           ("status", "Status", 80)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w" if c in ("title", "facility") else "center")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._details())

        acts = ttk.Frame(self)
        acts.pack(fill="x", pady=6)
        ttk.Button(acts, text="View details", command=self._details).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="Prepare for appointment",
                   command=lambda: PrepareDialog(self, self.app)).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="\U0001f514 Reminder",
                   command=self._reminder).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="Cancel", command=self._cancel).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="Delete", style="Danger.TButton",
                   command=self._delete).pack(side="left")

        rem = card(self)
        rem.pack(fill="x", pady=(4, 0))
        heading(rem, "\U0001f514 Reminders", size=12, pady=(8, 4))
        self.rem_label = tk.Label(rem, text="", bg=theme.PALETTE["surface"],
                                  fg=theme.PALETTE["muted"], justify="left")
        self.rem_label.pack(anchor="w", padx=12, pady=(0, 8))

    def refresh(self):
        uid = self.app.user["id"]
        self.tree.delete(*self.tree.get_children())
        for a in db.get_appointments(uid):
            self.tree.insert("", "end", iid=str(a["id"]),
                             values=(a["title"], a.get("facility_name") or "",
                                     fmt_when(a["when_at"]), a["doctor"],
                                     a["status"].title()))
        now = datetime.now()
        due, upcoming = [], []
        for r in db.get_reminders(uid):
            when = parse_when(r["when_at"])
            if not when:
                continue
            delta = timedelta(minutes=r["reminder_minutes"])
            if now >= when - delta and now <= when:
                due.append(f"\u26a0\ufe0f {r['title']} \u00b7 {fmt_when(r['when_at'])}"
                           f" \u00b7 in {int((when - now).total_seconds() // 60)} min")
            else:
                upcoming.append(f"\U0001f514 {r['title']} \u00b7 {fmt_when(r['when_at'])}")
        lines = due + upcoming
        self.rem_label.config(text="\n".join(lines) if lines
                              else "No reminders set. Add one from an appointment.")

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _details(self):
        aid = self._selected()
        if aid is None:
            messagebox.showinfo("Nothing selected", "Select an appointment first.")
            return
        show_appointment_dialog(self, aid)

    def _reminder(self):
        aid = self._selected()
        if aid is None:
            return
        dlg = ReminderDialog(self, self.app, aid, on_save=self.refresh)
        self.wait_window(dlg)

    def _cancel(self):
        aid = self._selected()
        if aid is None:
            return
        if messagebox.askyesno("Cancel appointment",
                               "Mark this appointment as cancelled?"):
            db.update_appointment(aid, status="cancelled")
            self.refresh()

    def _delete(self):
        aid = self._selected()
        if aid is None:
            return
        if messagebox.askyesno("Delete appointment",
                               "Delete this appointment permanently?"):
            db.delete_appointment(aid)
            self.refresh()


def show_appointment_dialog(parent, aid):
    a = db.get_appointment(aid)
    if not a:
        return
    dlg = tk.Toplevel(parent)
    dlg.title("Appointment details")
    dlg.geometry("520x480")
    dlg.transient(parent)
    dlg.grab_set()
    body = ttk.Frame(dlg)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    tk.Label(body, text=a["title"], font=(theme.FONT_FAMILY, 15, "bold"),
             bg=theme.PALETTE["bg"]).pack(anchor="w")
    rows = (("Facility", a.get("facility_name") or "\u2014"),
            ("Doctor", a["doctor"] or "\u2014"),
            ("When", fmt_when(a["when_at"])),
            ("Reason for visit", a["reason"] or "\u2014"),
            ("Notes", a["notes"] or "\u2014"),
            ("Booking method", a["booking_method"] or "\u2014"),
            ("Status", a["status"].title()))
    for label, val in rows:
        r = ttk.Frame(body)
        r.pack(fill="x", pady=2)
        ttk.Label(r, text=label + ":", width=16).pack(side="left")
        tk.Label(r, text=str(val), bg=theme.PALETTE["bg"], wraplength=360,
                 justify="left").pack(side="left")
    ttk.Button(body, text="Close", command=dlg.destroy).pack(pady=10)


class AppointmentDialog(tk.Toplevel):
    def __init__(self, parent, app, on_save=None, appointment=None):
        super().__init__(parent)
        self.title("Edit appointment" if appointment else "Add appointment")
        self.transient(parent)
        self.grab_set()
        self.app = app
        self.on_save = on_save
        self.appointment = appointment
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        self.title_var = tk.StringVar(value=appointment["title"] if appointment else "")
        self.doctor_var = tk.StringVar(value=appointment["doctor"] if appointment else "")
        self.when_var = tk.StringVar(
            value=appointment["when_at"] if appointment else
            (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 10:00"))
        self.reason_var = tk.StringVar(value=appointment["reason"] if appointment else "")
        self.notes_var = tk.StringVar(value=appointment["notes"] if appointment else "")
        self.method_var = tk.StringVar(
            value=appointment["booking_method"] if appointment else "Phone booking")
        self.reminder_var = tk.StringVar(
            value=self._reminder_label(appointment["reminder_minutes"])
            if appointment else "None")
        self.facility_var = tk.StringVar()

        facilities = db.get_all_facilities()
        self.facility_map = {f.name: f.id for f in facilities}
        if appointment and appointment.get("facility_id"):
            f = db.get_facility(appointment["facility_id"])
            if f:
                self.facility_var.set(f.name)

        fields = [("Title *", self.title_var, None),
                  ("Facility", self.facility_var, [""] + list(self.facility_map)),
                  ("Doctor", self.doctor_var, None),
                  ("Date & time (YYYY-MM-DD HH:MM)", self.when_var, None),
                  ("Reason for visit", self.reason_var, None),
                  ("Notes", self.notes_var, None),
                  ("Booking method", self.method_var,
                   ["Phone booking", "Website booking", "Walk-in", "External link"]),
                  ("Reminder", self.reminder_var,
                   ["None", "1 hour before", "1 day before", "2 days before"])]
        for label, var, values in fields:
            ttk.Label(body, text=label).pack(anchor="w", pady=(4, 0))
            if values is not None:
                ttk.Combobox(body, textvariable=var, values=values,
                             state="readonly").pack(fill="x")
            else:
                ttk.Entry(body, textvariable=var).pack(fill="x")

        row = ttk.Frame(body)
        row.pack(pady=12)
        ttk.Button(row, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    @staticmethod
    def _reminder_label(minutes):
        return {60: "1 hour before", 1440: "1 day before",
                2880: "2 days before"}.get(minutes, "None")

    @staticmethod
    def _reminder_minutes(label):
        return {"None": 0, "1 hour before": 60, "1 day before": 1440,
                "2 days before": 2880}.get(label, 0)

    def _save(self):
        title = self.title_var.get().strip()
        when = self.when_var.get().strip()
        if not title:
            messagebox.showwarning("Missing title", "Enter a title.")
            return
        if parse_when(when) is None:
            messagebox.showwarning("Bad date",
                                   "Date must be YYYY-MM-DD HH:MM (24h).")
            return
        fid = self.facility_map.get(self.facility_var.get())
        reminder = self._reminder_minutes(self.reminder_var.get())
        if self.appointment:
            db.update_appointment(
                self.appointment["id"], title=title, facility_id=fid,
                doctor=self.doctor_var.get().strip(), when_at=when,
                reason=self.reason_var.get().strip(),
                notes=self.notes_var.get().strip(),
                booking_method=self.method_var.get(),
                reminder_minutes=reminder)
        else:
            db.add_appointment(self.app.user["id"], title, when, facility_id=fid,
                               doctor=self.doctor_var.get().strip(),
                               reason=self.reason_var.get().strip(),
                               notes=self.notes_var.get().strip(),
                               booking_method=self.method_var.get(),
                               reminder_minutes=reminder)
        if self.on_save:
            self.on_save()
        self.destroy()


class ReminderDialog(tk.Toplevel):
    def __init__(self, parent, app, appointment_id, on_save=None):
        super().__init__(parent)
        self.title("Set reminder")
        self.transient(parent)
        self.grab_set()
        self.aid = appointment_id
        self.on_save = on_save
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=10)
        a = db.get_appointment(appointment_id)
        ttk.Label(body, text=f"Reminder for: {a['title']}").pack(anchor="w", pady=(0, 6))
        self.var = tk.StringVar(value=AppointmentDialog._reminder_label(
            a["reminder_minutes"]))
        ttk.Combobox(body, textvariable=self.var, state="readonly",
                     values=["None", "1 hour before", "1 day before", "2 days before"]
                     ).pack(fill="x")
        row = ttk.Frame(body)
        row.pack(pady=10)
        ttk.Button(row, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def _save(self):
        minutes = AppointmentDialog._reminder_minutes(self.var.get())
        db.update_appointment(self.aid, reminder_minutes=minutes)
        if self.on_save:
            self.on_save()
        self.destroy()


# ---------------------------------------------------------------------------
# Documents (spec sections 13-16)
# ---------------------------------------------------------------------------

class DocumentsView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main
        self._build()

    def _build(self):
        heading(self, "My Medical Documents", size=18)

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="\U0001f5bc\ufe0f Upload image\u2026",
                   style="Accent.TButton", command=self._upload_image
                   ).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar, text="\U0001f4c4 Upload PDF\u2026",
                   command=self._upload_pdf).pack(side="left", padx=(0, 5))
        if HAS_CV2:
            ttk.Button(toolbar, text="\U0001f4f7 Camera scan",
                       command=self._camera).pack(side="left", padx=(0, 5))

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, 6))
        self.f_search = tk.StringVar()
        ttk.Label(filters, text="Search:").pack(side="left")
        ttk.Entry(filters, textvariable=self.f_search, width=20).pack(side="left", padx=(0, 8))
        self.f_type = tk.StringVar(value="All types")
        ttk.Combobox(filters, textvariable=self.f_type,
                     values=["All types"] + list(docs.DOC_TYPES),
                     state="readonly", width=18).pack(side="left", padx=(0, 8))
        self.f_from = tk.StringVar()
        self.f_to = tk.StringVar()
        ttk.Label(filters, text="From:").pack(side="left")
        ttk.Entry(filters, textvariable=self.f_from, width=10).pack(side="left", padx=(0, 4))
        ttk.Label(filters, text="To:").pack(side="left")
        ttk.Entry(filters, textvariable=self.f_to, width=10).pack(side="left", padx=(0, 8))
        ttk.Button(filters, text="Apply", command=self.refresh).pack(side="left", padx=2)
        ttk.Button(filters, text="Clear", command=self._clear_filters).pack(side="left", padx=2)

        cols = ("title", "type", "date", "facility", "tags")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                 selectmode="browse")
        for c, text, w in (("title", "Title", 220), ("type", "Type", 140),
                           ("date", "Date", 90), ("facility", "Facility", 150),
                           ("tags", "Tags", 120)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._details())

        acts = ttk.Frame(self)
        acts.pack(fill="x", pady=6)
        ttk.Button(acts, text="View / edit", command=self._details).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="\U0001f9e0 Summary",
                   command=self._summary).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="\U0001f4ce Open file",
                   command=self._open_file).pack(side="left", padx=(0, 5))
        ttk.Button(acts, text="Delete", style="Danger.TButton",
                   command=self._delete).pack(side="left")

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        q = self.f_search.get().strip()
        ftype = self.f_type.get()
        if ftype == "All types":
            ftype = ""
        docs_ = db.search_documents(
            self.app.user["id"], query=q, doc_type=ftype,
            date_from=self.f_from.get().strip(), date_to=self.f_to.get().strip())
        for d in docs_:
            self.tree.insert("", "end", iid=str(d["id"]),
                             values=(d["title"], d["doc_type"], d["date"],
                                     d["facility"], ", ".join(d["tags"])))

    def _clear_filters(self):
        self.f_search.set("")
        self.f_type.set("All types")
        self.f_from.set("")
        self.f_to.set("")
        self.refresh()

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    # ------------------------------------------------------------ upload
    def _upload_image(self):
        path = filedialog.askopenfilename(
            title="Select a document image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tiff"),
                       ("All files", "*.*")])
        if path:
            self._ingest(path)

    def _upload_pdf(self):
        path = filedialog.askopenfilename(
            title="Select a PDF",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if path:
            self._ingest(path)

    def _ingest(self, path):
        dest = os.path.join(DOCS_DIR, f"doc_{datetime.now():%Y%m%d_%H%M%S}"
                                       f"{os.path.splitext(path)[1].lower() or '.jpg'}")
        try:
            shutil.copy2(path, dest)
        except OSError as e:
            messagebox.showerror("Copy failed", str(e))
            return
        meta = DocumentMetadataDialog(self, self.app, file_path=dest)
        self.wait_window(meta)
        if not meta.saved and os.path.exists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass

    def _camera(self):
        try:
            import cv2
        except ImportError:
            messagebox.showerror("Missing dependency",
                                 "Camera scanning needs opencv-python.")
            return
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            messagebox.showerror("Camera", "Could not open the camera (index 0).")
            return
        win = tk.Toplevel(self)
        win.title("Camera \u2014 press SPACE to capture, ESC to cancel")
        win.resizable(False, False)
        ttk.Label(win, text="Press SPACE to capture \u00b7 ESC to cancel").pack()
        label = tk.Label(win, bg="black")
        label.pack(padx=6, pady=6)
        running = {"stop": False}
        self._cam_photo = None

        def update():
            if running["stop"]:
                return
            ok, frame = cap.read()
            if ok and HAS_PIL:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                img.thumbnail((640, 480))
                photo = ImageTk.PhotoImage(img)
                self._cam_photo = photo
                label.config(image=photo)
            win.after(50, update)

        def finish(path):
            if running["stop"]:
                return
            running["stop"] = True
            cap.release()
            win.destroy()
            if path:
                self._ingest(path)

        def on_key(event):
            if event.keysym == "Escape":
                finish(None)
            elif event.keysym in ("space", "Return"):
                ok, frame = cap.read()
                if ok:
                    path = os.path.join(DOCS_DIR,
                                        f"camera_{datetime.now():%Y%m%d_%H%M%S}.jpg")
                    cv2.imwrite(path, frame)
                    finish(path)

        win.bind("<KeyPress>", on_key)
        win.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        win.focus_force()
        update()

    # ------------------------------------------------------------- actions
    def _details(self):
        did = self._selected_id()
        if did is None:
            messagebox.showinfo("Nothing selected", "Select a document first.")
            return
        doc = db.get_document(did)
        if doc:
            DocumentMetadataDialog(self, self.app, document=doc)

    def _summary(self):
        did = self._selected_id()
        if did is None:
            messagebox.showinfo("Nothing selected", "Select a document first.")
            return
        doc = db.get_document(did)
        if doc:
            SummaryDialog(self, doc)

    def _open_file(self):
        did = self._selected_id()
        if did is None:
            return
        doc = db.get_document(did)
        if not doc or not doc["file_path"] or not os.path.exists(doc["file_path"]):
            messagebox.showinfo("No file", "This document has no file on disk.")
            return
        try:
            if os.name == "nt":
                os.startfile(doc["file_path"])  # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", doc["file_path"]])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Open failed", str(e))

    def _delete(self):
        did = self._selected_id()
        if did is None:
            return
        if not messagebox.askyesno("Delete document",
                                   "Delete this document and its stored copy?"):
            return
        doc = db.get_document(did)
        if doc and doc["file_path"] and os.path.exists(doc["file_path"]):
            try:
                os.remove(doc["file_path"])
            except OSError:
                pass
        db.delete_document(did)
        self.refresh()


class DocumentMetadataDialog(tk.Toplevel):
    """Review/edit extracted metadata before saving (or edit after)."""

    def __init__(self, parent, app, file_path=None, document=None):
        super().__init__(parent)
        self.app = app
        self.file_path = file_path
        self.document = document
        self.saved = False
        self.title("Document details" if document else "Add document")
        self.geometry("640x620")
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        default_title = (document["title"] if document else
                         os.path.basename(file_path or "document"))
        self.title_var = tk.StringVar(value=default_title)
        self.type_var = tk.StringVar(value=document["doc_type"] if document else "Other")
        self.date_var = tk.StringVar(value=document["date"] if document else docs.today_iso())
        self.facility_var = tk.StringVar(value=document["facility"] if document else "")
        self.tags_var = tk.StringVar(
            value=", ".join(document["tags"]) if document else "")
        self.notes_var = tk.StringVar(value=document["notes"] if document else "")
        self.text_var = document["ocr_text"] if document else ""

        ttk.Label(body, text="Title *").pack(anchor="w")
        ttk.Entry(body, textvariable=self.title_var).pack(fill="x", pady=(2, 6))
        r = ttk.Frame(body)
        r.pack(fill="x", pady=(0, 6))
        ttk.Label(r, text="Type:").pack(side="left")
        ttk.Combobox(r, textvariable=self.type_var, values=list(docs.DOC_TYPES),
                     state="readonly", width=22).pack(side="left", padx=(6, 14))
        ttk.Label(r, text="Date:").pack(side="left")
        ttk.Entry(r, textvariable=self.date_var, width=12).pack(side="left", padx=(6, 0))
        ttk.Label(body, text="Facility / provider").pack(anchor="w")
        ttk.Entry(body, textvariable=self.facility_var).pack(fill="x", pady=(2, 6))
        ttk.Label(body, text="Tags (comma separated)").pack(anchor="w")
        ttk.Entry(body, textvariable=self.tags_var).pack(fill="x", pady=(2, 6))
        ttk.Label(body, text="Notes").pack(anchor="w")
        ttk.Entry(body, textvariable=self.notes_var).pack(fill="x", pady=(2, 6))

        ttk.Label(body, text="Extracted text (OCR)".lower().title()).pack(anchor="w")
        txt_frame = ttk.Frame(body)
        txt_frame.pack(fill="both", expand=True, pady=(2, 6))
        self.ocr_box = tk.Text(txt_frame, height=8, wrap="word",
                               bg=theme.PALETTE["surface2"],
                               fg=theme.PALETTE["text"], relief="flat",
                               highlightthickness=1,
                               highlightbackground=theme.PALETTE["border"],
                               font=(theme.FONT_FAMILY, 10))
        vsb = ttk.Scrollbar(txt_frame, command=self.ocr_box.yview)
        self.ocr_box.configure(yscrollcommand=vsb.set)
        self.ocr_box.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        if self.text_var:
            self.ocr_box.insert("1.0", self.text_var)

        self.status = tk.Label(body, text="", bg=theme.PALETTE["bg"],
                               fg=theme.PALETTE["muted"])
        self.status.pack(anchor="w")

        row = ttk.Frame(body)
        row.pack(fill="x", pady=8)
        if file_path and not document:
            ttk.Button(row, text="\U0001f50d Scan & classify (OCR)",
                       command=self._ocr).pack(side="left", padx=(0, 6))
            ttk.Button(row, text="\U0001f4d1 Auto-classify text",
                       command=self._classify).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")

    # -------------------------------------------------------------- OCR
    def _ocr(self):
        if (self.file_path or "").lower().endswith(".pdf"):
            messagebox.showinfo(
                "PDF scanning",
                "OCR of PDFs needs a PDF-to-image step that is not built in yet. "
                "Convert the page to an image and upload it, or enter the text "
                "manually.")
            return
        if not docs.ocr_available():
            messagebox.showinfo(
                "OCR not installed",
                "Install EasyOCR to scan documents:\n\n  pip install easyocr\n\n"
                "You can still add the document manually.")
            return
        self.status.config(text="Scanning\u2026 (first run loads the OCR model).")
        threading.Thread(target=self._ocr_worker, daemon=True).start()

    def _ocr_worker(self):
        try:
            result = docs.ocr_image(self.file_path)
        except Exception as e:  # noqa: BLE001
            self.app.after(0, lambda: self._ocr_done(None, str(e)))
            return
        self.app.after(0, lambda: self._ocr_done(result, None))

    def _ocr_done(self, result, err):
        self.status.config(text="")
        if err:
            messagebox.showerror("OCR failed", err)
            return
        text = result["text"]
        self.ocr_box.delete("1.0", "end")
        self.ocr_box.insert("1.0", text)
        self.status.config(
            text=f"OCR complete \u00b7 confidence {result['confidence']:.0f}%")
        self._classify()

    def _classify(self):
        text = self.ocr_box.get("1.0", "end")
        doc_type, conf, terms = docs.classify_document(text)
        self.type_var.set(doc_type)
        self.status.config(
            text=f"Classified as '{doc_type}' (confidence {conf:.0%}). "
                 f"Match: {', '.join(terms[:3]) or 'none'}")
        if not self.title_var.get() or self.title_var.get() == \
                os.path.basename(self.file_path or "document"):
            self.title_var.set(f"{doc_type} - {self.date_var.get()}")

    def _save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Missing title", "Enter a title.")
            return
        tags = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]
        text = self.ocr_box.get("1.0", "end").strip()
        if self.document:
            db.update_document(self.document["id"], title=title,
                               doc_type=self.type_var.get(),
                               date=self.date_var.get().strip(),
                               facility=self.facility_var.get().strip(),
                               tags=tags, ocr_text=text,
                               notes=self.notes_var.get().strip())
        else:
            db.add_document(self.app.user["id"], title, self.type_var.get(),
                            self.date_var.get().strip(),
                            self.facility_var.get().strip(), tags,
                            self.file_path or "", text, 0.0,
                            self.notes_var.get().strip())
        self.saved = True
        self.destroy()


class SummaryDialog(tk.Toplevel):
    """AI-style document summary — conservative framing (spec section 16)."""

    def __init__(self, parent, doc):
        super().__init__(parent)
        self.title("Document summary")
        self.geometry("560x560")
        self.transient(parent)
        self.grab_set()
        summary = docs.summarize_document(doc["doc_type"], doc["date"],
                                          doc["ocr_text"])
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        tk.Label(body, text=doc["title"], font=(theme.FONT_FAMILY, 15, "bold"),
                 bg=theme.PALETTE["bg"]).pack(anchor="w")
        tk.Label(body, text=f"{summary['document_type']} \u00b7 {summary['date']}",
                 fg=theme.PALETTE["muted"], bg=theme.PALETTE["bg"]
                 ).pack(anchor="w", pady=(0, 8))

        tk.Label(body, text=summary["framing"], fg=theme.PALETTE["warning"],
                 bg=theme.PALETTE["bg"], wraplength=520, justify="left",
                 font=(theme.FONT_FAMILY, 10, "italic")).pack(anchor="w", pady=(0, 8))

        tk.Label(body, text="Measurements explicitly present",
                 font=(theme.FONT_FAMILY, 11, "bold"), bg=theme.PALETTE["bg"]
                 ).pack(anchor="w")
        for m in summary["measurements"] or ["(none detected)"]:
            tk.Label(body, text=f"\u2022 {m}", bg=theme.PALETTE["bg"]
                     ).pack(anchor="w", padx=8)

        tk.Label(body, text="Terms appearing in the report",
                 font=(theme.FONT_FAMILY, 11, "bold"), bg=theme.PALETTE["bg"]
                 ).pack(anchor="w", pady=(8, 0))
        tk.Label(body, text=", ".join(summary["terms"]) or "(none)",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"], wraplength=500,
                 justify="left").pack(anchor="w", padx=8)

        tk.Label(body, text="Questions you may want to ask your clinician",
                 font=(theme.FONT_FAMILY, 11, "bold"), bg=theme.PALETTE["bg"]
                 ).pack(anchor="w", pady=(8, 0))
        for q in summary["questions"]:
            tk.Label(body, text=f"\u2753 {q}", bg=theme.PALETTE["bg"],
                     wraplength=500, justify="left").pack(anchor="w", padx=8)

        ttk.Button(body, text="Close", command=self.destroy).pack(pady=10)


# ---------------------------------------------------------------------------
# Timeline (spec section 17)
# ---------------------------------------------------------------------------

class TimelineView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main

    def refresh(self):
        for w in self.winfo_children():
            w.destroy()
        heading(self, "Health Timeline", size=18)
        tk.Label(self, text="Your appointments and documents in one place \u2014 "
                            "bring this to consultations.",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"]
                 ).pack(anchor="w", pady=(0, 8))

        items = db.get_timeline(self.app.user["id"])
        if not items:
            tk.Label(self, text="Nothing yet \u2014 add appointments or documents "
                                "to see your timeline.",
                     bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"]
                     ).pack(pady=30)
            return

        cols = ("date", "kind", "title", "detail")
        tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, text, w in (("date", "Date", 110), ("kind", "Type", 110),
                           ("title", "Title", 260), ("detail", "Detail", 220)):
            tree.heading(c, text=text)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="both", expand=True)

        icons = {"appointment": "\U0001f4c5 Appointment",
                 "document": "\U0001f4c1 Document"}
        for item in items:
            tree.insert("", "end", values=(item["date"], icons[item["kind"]],
                                           item["title"], item["detail"]))


# ---------------------------------------------------------------------------
# Safety / emergency (spec sections 5, 31)
# ---------------------------------------------------------------------------

class SafetyView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main

    def refresh(self):
        for w in self.winfo_children():
            w.destroy()

        banner = tk.Frame(self, bg=theme.PALETTE["error"])
        banner.pack(fill="x", pady=(0, 12))
        tk.Label(banner, text="\U0001f6a8 Need urgent medical attention?",
                 bg=theme.PALETTE["error"], fg="white",
                 font=(theme.FONT_FAMILY, 20, "bold")).pack(pady=(16, 2))
        tk.Label(banner, text=EMERGENCY_NUMBERS, bg=theme.PALETTE["error"],
                 fg="white", font=(theme.FONT_FAMILY, 12, "bold")).pack(pady=(0, 4))
        tk.Label(banner, text=nav._EMERGENCY_MESSAGE, bg=theme.PALETTE["error"],
                 fg="white", wraplength=760, justify="center",
                 font=(theme.FONT_FAMILY, 10)).pack(pady=(0, 16))

        body = tk.Frame(self, bg=theme.PALETTE["bg"])
        body.pack(fill="both", expand=True)
        cols = tk.Frame(body, bg=theme.PALETTE["bg"])
        cols.pack(fill="x")
        col1 = tk.Frame(cols, bg=theme.PALETTE["bg"])
        col1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        col2 = tk.Frame(cols, bg=theme.PALETTE["bg"])
        col2.pack(side="left", fill="x", expand=True, padx=(8, 0))

        c = card(col1)
        c.pack(fill="x")
        heading(c, "\U0001f4f1 Emergency contacts", size=13, pady=(8, 4))
        tk.Label(c, text="India: 112 (all emergencies)  \u00b7  108 (ambulance)",
                 bg=theme.PALETTE["surface"]).pack(anchor="w", padx=12)
        ttk.Button(c, text="Copy emergency numbers",
                   command=lambda: (copy_to_clipboard(self.app, EMERGENCY_NUMBERS),
                                    messagebox.showinfo("Copied", EMERGENCY_NUMBERS))
                   ).pack(anchor="w", padx=12, pady=(6, 10))

        c = card(col1)
        c.pack(fill="x", pady=(10, 0))
        heading(c, "\U0001f3e5 Emergency facilities", size=13, pady=(8, 4))
        em = [f for f in db.get_all_facilities() if f.emergency]
        for f in em[:3]:
            tk.Label(c, text=f"\U0001f4cd {f.name} \u00b7 {f.phone}",
                     bg=theme.PALETTE["surface"]).pack(anchor="w", padx=12)
        ttk.Button(c, text="Find emergency facilities",
                   command=self._find_emergency).pack(anchor="w", padx=12, pady=(6, 10))
        if em:
            ttk.Button(c, text="Directions to nearest",
                       command=lambda: open_browser(directions_url(
                           em[0].lat, em[0].lon, *user_location(self.app)[:2]))
                       ).pack(anchor="w", padx=12, pady=(0, 10))

        c = card(col2)
        c.pack(fill="x")
        heading(c, "\U0001f469\U0001f3fb\U0000200d\U0001f4bb My emergency contact",
                size=13, pady=(8, 4))
        self.contact_var = tk.StringVar(
            value=db.get_setting(self.app.user["id"], "emergency_contact"))
        ttk.Entry(c, textvariable=self.contact_var).pack(fill="x", padx=12, pady=(0, 6))
        ttk.Button(c, text="Save emergency contact",
                   command=self._save_contact).pack(anchor="w", padx=12, pady=(0, 10))

        c = card(col2)
        c.pack(fill="x", pady=(10, 0))
        heading(c, "\u26a0\ufe0f Urgent, but not an emergency", size=13, pady=(8, 4))
        tk.Label(c, text=nav._URGENT_MESSAGE, bg=theme.PALETTE["surface"],
                 wraplength=360, justify="left").pack(anchor="w", padx=12)
        tk.Label(c, text="Examples: high fever, vomiting, severe pain, "
                         "signs of infection, worsening symptoms.",
                 bg=theme.PALETTE["surface"], fg=theme.PALETTE["muted"],
                 wraplength=360, justify="left",
                 font=(theme.FONT_FAMILY, 9)).pack(anchor="w", padx=12, pady=(4, 10))

        tk.Label(self, text=nav.DISCLAIMER, bg=theme.PALETTE["bg"],
                 fg=theme.PALETTE["muted"], font=(theme.FONT_FAMILY, 9)
                 ).pack(side="bottom", pady=8)

    def _save_contact(self):
        db.set_setting(self.app.user["id"], "emergency_contact",
                       self.contact_var.get().strip())
        messagebox.showinfo("Saved", "Emergency contact saved.")

    def _find_emergency(self):
        self.main.show_view("facilities")
        self.main.views["facilities"].set_emergency_filter()


# ---------------------------------------------------------------------------
# Profile, settings & privacy centre (spec sections 19-20, 24-25)
# ---------------------------------------------------------------------------

class SettingsView(tk.Frame):
    def __init__(self, parent, app, main):
        super().__init__(parent)
        self.app = app
        self.main = main
        self._build()

    def _build(self):
        heading(self, "Profile & Privacy", size=18)
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, pady=(6, 0))

        self._account_tab(nb)
        self._privacy_tab(nb)
        self._consent_tab(nb)
        self._security_tab(nb)
        self._preferences_tab(nb)

    # ------------------------------------------------------------- account
    def _account_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Account")
        body = ttk.Frame(tab)
        body.pack(fill="x", padx=12, pady=10)
        user = self.app.user

        ttk.Label(body, text="Name").pack(anchor="w")
        self.name_var = tk.StringVar(value=user["name"])
        ttk.Entry(body, textvariable=self.name_var).pack(fill="x", pady=(2, 8))
        ttk.Label(body, text="Email").pack(anchor="w")
        self.email_var = tk.StringVar(value=user["email"] or "")
        ttk.Entry(body, textvariable=self.email_var).pack(fill="x", pady=(2, 8))
        ttk.Label(body, text="Phone").pack(anchor="w")
        self.phone_var = tk.StringVar(value=user["phone"] or "")
        ttk.Entry(body, textvariable=self.phone_var).pack(fill="x", pady=(2, 8))
        ttk.Button(body, text="Save account", style="Accent.TButton",
                   command=self._save_account).pack(anchor="w", pady=(4, 14))

        ttk.Separator(body).pack(fill="x", pady=6)
        ttk.Label(body, text="Change password",
                  font=(theme.FONT_FAMILY, 11, "bold")).pack(anchor="w", pady=(6, 4))
        self.old_pass = tk.StringVar()
        self.new_pass = tk.StringVar()
        self.new_pass2 = tk.StringVar()
        for label, var in (("Current password", self.old_pass),
                           ("New password", self.new_pass),
                           ("Confirm new password", self.new_pass2)):
            ttk.Label(body, text=label).pack(anchor="w")
            ttk.Entry(body, textvariable=var, show="\u2022").pack(fill="x", pady=(2, 6))
        ttk.Button(body, text="Change password", command=self._change_password
                   ).pack(anchor="w", pady=(0, 10))

    def _save_account(self):
        uid = self.app.user["id"]
        try:
            db.update_user(uid, name=self.name_var.get(),
                           email=self.email_var.get(), phone=self.phone_var.get())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Save failed", str(e))
            return
        self.app.user = db.get_user(uid)
        messagebox.showinfo("Saved", "Account updated.")

    def _change_password(self):
        if self.new_pass.get() != self.new_pass2.get():
            messagebox.showwarning("Mismatch", "New passwords do not match.")
            return
        try:
            ok = db.change_password(self.app.user["id"], self.old_pass.get(),
                                    self.new_pass.get())
        except ValueError as e:
            messagebox.showwarning("Password", str(e))
            return
        if not ok:
            messagebox.showwarning("Password", "Current password is incorrect.")
            return
        messagebox.showinfo("Done", "Password changed.")
        self.old_pass.set("")
        self.new_pass.set("")
        self.new_pass2.set("")

    # ------------------------------------------------------------- privacy
    def _privacy_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Privacy Center")
        body = ttk.Frame(tab)
        body.pack(fill="x", padx=12, pady=10)

        ttk.Label(body, text="Data collected", font=(theme.FONT_FAMILY, 11, "bold")
                  ).pack(anchor="w", pady=(0, 4))
        for line in ("\u2022 Account information (name, email/phone)",
                     "\u2022 Healthcare searches (your navigation requests)",
                     "\u2022 Documents you upload (stored locally on this device)",
                     "\u2022 Appointment information you enter",
                     "\u2022 Preferences and consent choices"):
            tk.Label(body, text=line, bg=theme.PALETTE["bg"]).pack(anchor="w")

        ttk.Separator(body).pack(fill="x", pady=8)
        ttk.Label(body, text="Permissions & processing", font=(theme.FONT_FAMILY, 11, "bold")
                  ).pack(anchor="w", pady=(0, 4))
        self.pref_vars = {}
        prefs = db.get_privacy_prefs(self.app.user["id"])
        labels = {"location": "Location permission",
                  "camera": "Camera permission",
                  "microphone": "Microphone permission",
                  "notifications": "Notification permission",
                  "ai_processing": "Allow AI processing of my input",
                  "analytics": "Share usage analytics",
                  "data_sharing": "Share data with third parties"}
        for key, label in labels.items():
            var = tk.BooleanVar(value=prefs.get(key, False))
            self.pref_vars[key] = var
            ttk.Checkbutton(body, text=label, variable=var,
                            command=lambda k=key, v=var: self._save_pref(k, v)
                            ).pack(anchor="w", pady=1)

        ttk.Separator(body).pack(fill="x", pady=8)
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="\U0001f4e4 Export my data (JSON)",
                   command=self._export_data).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="\U0001f5d1\ufe0f Delete all documents",
                   command=self._delete_docs).pack(side="left")

    def _save_pref(self, key, var):
        db.set_privacy_pref(self.app.user["id"], key, bool(var.get()))

    def _export_data(self):
        data = db.export_user_data(self.app.user["id"])
        path = filedialog.asksaveasfilename(
            title="Export my data", defaultextension=".json",
            initialfile="healthnav-export.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        messagebox.showinfo("Exported", f"Your data was exported to:\n{path}")

    def _delete_docs(self):
        if not messagebox.askyesno(
                "Delete documents",
                "Delete ALL your documents? This cannot be undone."):
            return
        paths = [d["file_path"] for d in db.get_documents(self.app.user["id"])]
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        db.delete_all_documents(self.app.user["id"])
        messagebox.showinfo("Done", "All documents deleted.")

    # ------------------------------------------------------------- consent
    def _consent_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Consent & Data")
        body = ttk.Frame(tab)
        body.pack(fill="x", padx=12, pady=10)

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="Review & update consent",
                   command=self._reconsent).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="\U0001f36a Cookie settings",
                   command=self._cookies).pack(side="left")

        ttk.Label(body, text="Consent records", font=(theme.FONT_FAMILY, 11, "bold")
                  ).pack(anchor="w", pady=(0, 4))
        cols = ("type", "version", "granted", "timestamp")
        tree = ttk.Treeview(body, columns=cols, show="headings", height=10)
        for c, text, w in (("type", "Consent type", 220), ("version", "Version", 70),
                           ("granted", "Granted", 60), ("timestamp", "Timestamp", 170)):
            tree.heading(c, text=text)
            tree.column(c, width=w, anchor="w")
        tree.pack(fill="x")
        for rec in db.get_consent_records(self.app.user["id"]):
            info = db.CONSENT_TYPES.get(rec["consent_type"], {})
            tree.insert("", "end", values=(info.get("label", rec["consent_type"]),
                                           rec["version"],
                                           "Yes" if rec["granted"] else "No",
                                           rec["timestamp"]))

        prefs = db.get_cookie_prefs(self.app.user["id"])
        tk.Label(body, text="\nCookie preferences: " +
                            "Analytics " + ("on" if prefs["analytics"] else "off") +
                            " \u00b7 Preferences " +
                            ("on" if prefs["preferences"] else "off") +
                            " \u00b7 Marketing " +
                            ("on" if prefs["marketing"] else "off"),
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"]).pack(anchor="w")

    def _reconsent(self):
        ConsentDialog(self, self.app)

    def _cookies(self):
        dlg = CookieDialog(self, self.app,
                           initial=db.get_cookie_prefs(self.app.user["id"]))
        self.wait_window(dlg)
        if dlg.result:
            db.set_cookie_prefs(self.app.user["id"], **dlg.result)
            messagebox.showinfo("Saved", "Cookie preferences updated.")

    # ------------------------------------------------------------ security
    def _security_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Security")
        body = ttk.Frame(tab)
        body.pack(fill="x", padx=12, pady=10)

        ttk.Label(body, text="Sessions", font=(theme.FONT_FAMILY, 11, "bold")
                  ).pack(anchor="w", pady=(0, 4))
        user = self.app.user
        tk.Label(body, text="Last login: " + (user["last_login"] or "\u2014"),
                 bg=theme.PALETTE["bg"]).pack(anchor="w", pady=2)
        tk.Label(body, text="This is a local desktop app \u2014 your session "
                            "ends when you log out.",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"],
                 font=(theme.FONT_FAMILY, 9)).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="Log out", command=self.app.logout).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Delete my account",
                   style="Danger.TButton", command=self._delete_account
                   ).pack(side="left")

    def _delete_account(self):
        if not messagebox.askyesno(
                "Delete account",
                "This permanently deletes your account, appointments, documents "
                "and preferences. Continue?"):
            return
        db.delete_account(self.app.user["id"])
        messagebox.showinfo("Deleted", "Your account and data were deleted.")
        self.app.logout()

    # --------------------------------------------------------- preferences
    def _preferences_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Preferences")
        body = ttk.Frame(tab)
        body.pack(fill="x", padx=12, pady=10)
        uid = self.app.user["id"]
        lat, lon, label = user_location(self.app)

        ttk.Label(body, text="My location (used for nearby facilities & the map)",
                  font=(theme.FONT_FAMILY, 11, "bold")).pack(anchor="w", pady=(0, 4))
        r = ttk.Frame(body)
        r.pack(fill="x", pady=2)
        ttk.Label(r, text="Latitude:").pack(side="left")
        self.loc_lat = tk.StringVar(value=str(lat))
        ttk.Entry(r, textvariable=self.loc_lat, width=10).pack(side="left", padx=(4, 14))
        ttk.Label(r, text="Longitude:").pack(side="left")
        self.loc_lon = tk.StringVar(value=str(lon))
        ttk.Entry(r, textvariable=self.loc_lon, width=10).pack(side="left", padx=(4, 14))
        ttk.Label(body, text="Label").pack(anchor="w", pady=(6, 0))
        self.loc_label = tk.StringVar(value=label)
        ttk.Entry(body, textvariable=self.loc_label).pack(fill="x", pady=(2, 6))
        ttk.Button(body, text="Save location", command=self._save_location
                   ).pack(anchor="w", pady=(0, 12))

        ttk.Separator(body).pack(fill="x", pady=6)
        ttk.Label(body, text="Interface", font=(theme.FONT_FAMILY, 11, "bold")
                  ).pack(anchor="w", pady=(6, 4))
        self.lang_var = tk.StringVar(value=db.get_setting(uid, "language", "en"))
        ttk.Combobox(body, textvariable=self.lang_var, state="readonly",
                     values=["en"]).pack(anchor="w")
        tk.Label(body, text="Multilingual UI (Hindi, Punjabi, Bengali, ...) and "
                            "screen-reader/high-contrast support are on the "
                            "roadmap.",
                 bg=theme.PALETTE["bg"], fg=theme.PALETTE["muted"], wraplength=500,
                 justify="left", font=(theme.FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 0))

    def _save_location(self):
        try:
            lat = float(self.loc_lat.get().strip())
            lon = float(self.loc_lon.get().strip())
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Invalid location",
                                   "Latitude must be -90..90 and longitude -180..180.")
            return
        db.set_setting(self.app.user["id"], "location_lat", str(lat))
        db.set_setting(self.app.user["id"], "location_lon", str(lon))
        db.set_setting(self.app.user["id"], "location_label",
                       self.loc_label.get().strip())
        messagebox.showinfo("Saved", "Location saved.")


class ConsentDialog(tk.Toplevel):
    """Reopen the consent screen from Settings."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.title("Consent")
        self.transient(parent)
        self.grab_set()
        self.app = app
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        self.vars = {}
        current = db.current_consents(app.user["id"])
        for ctype, info in db.CONSENT_TYPES.items():
            granted = bool(current.get(ctype, {}).get("granted"))
            var = tk.BooleanVar(value=granted)
            self.vars[ctype] = var
            ttk.Checkbutton(
                body, text=f"{info['label']} (v{info['version']})"
                           f"{'  [required]' if info['required'] else ''}",
                variable=var).pack(anchor="w", pady=1)

        row = ttk.Frame(body)
        row.pack(pady=10)
        ttk.Button(row, text="Save consent choices", style="Accent.TButton",
                   command=self._save).pack(side="left", padx=4)
        ttk.Button(row, text="Close", command=self.destroy).pack(side="left", padx=4)

    def _save(self):
        uid = self.app.user["id"]
        for ctype, var in self.vars.items():
            db.record_consent(uid, ctype, bool(var.get()))
        messagebox.showinfo("Saved", "Consent choices recorded.")
        self.destroy()


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    db.init()
    db.init_facilities(fac.SAMPLE_FACILITIES)
    root = HealthApp()
    root.mainloop()


if __name__ == "__main__":
    main()
