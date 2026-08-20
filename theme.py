"""HealthNav theme: calm medical teal on white.

Mirrors the fintech theme used by the other apps in this workspace — same
structure, palette adjusted for a healthcare feel:

    --background: #F6FAF9   --page: #0F3D3E (dark teal)
    --surface: #FFFFFF      --surface-soft: #F6FAF9
    --accent: #0E6E6B (medical teal)   --text: #12211F
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

PALETTE = {
    "bg": "#F6FAF9",           # app background (soft off-white)
    "page": "#0F3D3E",         # dark teal (auth / emergency screens)
    "surface": "#FFFFFF",      # cards / panels
    "surface2": "#F2F7F6",     # inputs, rows, soft pills
    "border": "#DDE8E6",
    "accent": "#0E6E6B",       # primary medical teal
    "positive": "#16A085",     # green
    "warning": "#D97706",      # amber
    "error": "#DC2626",        # red
    "yellow": "#F4C94E",
    "text": "#12211F",         # near-black text
    "muted": "#5F7370",        # secondary text
    "selection": "#D5EDEA",    # teal-tinted selected rows
    "today": "#E0F2EF",
    "accent_dark": "#FFFFFF",  # text on teal buttons
}

FONT_FAMILY = "Segoe UI"

_FONT_CANDIDATES = ("Inter", "Manrope", "Plus Jakarta Sans", "Poppins",
                    "Segoe UI", "Helvetica", "Arial")


def _pick_font(root):
    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()
    for name in _FONT_CANDIDATES:
        if name in families:
            return name
    return "TkDefaultFont"


def apply_theme(root):
    global FONT_FAMILY
    FONT_FAMILY = _pick_font(root)

    root.configure(bg=PALETTE["bg"])
    root.option_add("*background", PALETTE["bg"])
    root.option_add("*foreground", PALETTE["text"])
    root.option_add("*selectBackground", PALETTE["selection"])
    root.option_add("*selectForeground", "#0B3B40")
    root.option_add("*activeBackground", PALETTE["border"])
    root.option_add("*activeForeground", PALETTE["text"])
    root.option_add("*highlightBackground", PALETTE["bg"])
    root.option_add("*highlightColor", PALETTE["border"])

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=PALETTE["bg"],
                    foreground=PALETTE["text"], font=(FONT_FAMILY, 10),
                    borderwidth=0)
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("TLabel", background=PALETTE["bg"],
                    foreground=PALETTE["text"])
    style.configure("TLabelframe", background=PALETTE["bg"],
                    bordercolor=PALETTE["border"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=PALETTE["bg"],
                    foreground=PALETTE["text"])

    style.configure("TButton",
                    background=PALETTE["surface"], foreground=PALETTE["text"],
                    bordercolor=PALETTE["border"], borderwidth=1,
                    focuscolor=PALETTE["accent"], focusthickness=0,
                    padding=(14, 8), relief="flat")
    style.map("TButton",
              background=[("active", PALETTE["border"]),
                          ("pressed", PALETTE["accent"])],
              foreground=[("active", PALETTE["text"]),
                          ("pressed", PALETTE["accent_dark"])])

    style.configure("Accent.TButton",
                    background=PALETTE["accent"],
                    foreground=PALETTE["accent_dark"],
                    bordercolor=PALETTE["accent"], borderwidth=0,
                    padding=(14, 8), relief="flat")
    style.map("Accent.TButton",
              background=[("active", "#0F7D79"), ("pressed", "#0E6E6B")],
              foreground=[("active", PALETTE["accent_dark"]),
                          ("pressed", PALETTE["accent_dark"])])

    style.configure("Danger.TButton",
                    background="#FDECEC", foreground="#DC2626",
                    bordercolor="#F5C2C2", borderwidth=1,
                    padding=(14, 8), relief="flat")
    style.map("Danger.TButton",
              background=[("active", PALETTE["error"])],
              foreground=[("active", "#FFFFFF")])

    style.configure("TEntry",
                    fieldbackground=PALETTE["surface"],
                    foreground=PALETTE["text"],
                    bordercolor=PALETTE["border"],
                    insertcolor=PALETTE["text"],
                    lightcolor=PALETTE["surface"],
                    darkcolor=PALETTE["surface"],
                    padding=(8, 6), relief="flat")
    style.map("TEntry",
              bordercolor=[("focus", PALETTE["accent"])],
              lightcolor=[("focus", PALETTE["accent"])],
              darkcolor=[("focus", PALETTE["accent"])])

    style.configure("TCombobox",
                    fieldbackground=PALETTE["surface"],
                    background=PALETTE["surface"],
                    foreground=PALETTE["text"],
                    arrowcolor=PALETTE["muted"],
                    bordercolor=PALETTE["border"],
                    padding=(8, 6), relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", PALETTE["surface"])],
              foreground=[("readonly", PALETTE["text"])],
              bordercolor=[("focus", PALETTE["accent"])])

    style.configure("TCheckbutton", background=PALETTE["bg"],
                    foreground=PALETTE["text"])
    style.map("TCheckbutton",
              background=[("active", PALETTE["bg"])],
              foreground=[("active", PALETTE["text"])])

    style.configure("TNotebook", background=PALETTE["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=PALETTE["surface"],
                    foreground=PALETTE["muted"], padding=(18, 10), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", "#E0F2EF")],
              foreground=[("selected", PALETTE["accent"])])

    style.configure("Treeview", background=PALETTE["surface"],
                    fieldbackground=PALETTE["surface"],
                    foreground=PALETTE["text"], rowheight=32, borderwidth=0)
    style.configure("Treeview.Heading", background=PALETTE["surface2"],
                    foreground=PALETTE["muted"], relief="flat", padding=(8, 7))
    style.map("Treeview.Heading", background=[("active", PALETTE["border"])])
    style.map("Treeview",
              background=[("selected", PALETTE["selection"])],
              foreground=[("selected", "#0B3B40")])

    style.configure("TProgressbar", background=PALETTE["positive"],
                    troughcolor=PALETTE["border"],
                    bordercolor=PALETTE["border"],
                    lightcolor=PALETTE["positive"],
                    darkcolor=PALETTE["positive"])

    style.configure("Vertical.TScrollbar", background=PALETTE["border"],
                    troughcolor=PALETTE["bg"], bordercolor=PALETTE["bg"],
                    arrowcolor=PALETTE["muted"], relief="flat")
    style.configure("Horizontal.TScrollbar", background=PALETTE["border"],
                    troughcolor=PALETTE["bg"], bordercolor=PALETTE["bg"],
                    arrowcolor=PALETTE["muted"], relief="flat")

    root.option_add("*TCombobox*Listbox.background", PALETTE["surface"])
    root.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["selection"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#0B3B40")
