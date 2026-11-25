"""
Modern Windows 11 / Fluent-inspired GUI for AppData to Program Files Migrator.
Features DWM dark title bar, sidebar navigation, borderless elevated cards,
custom canvas toggle switches, and resilient background scanning.
"""
import sys
import os
import shutil
import subprocess
import winreg
import ctypes
import threading
import queue
from typing import List, Optional, Callable
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.models import InstalledApp, MigrationOptions, MigrationResult
from core.detector import scan_for_installed_apps
from core.migrator import Migrator
from core.registry import delete_registry_tree, UNINSTALL_SUBKEY
from core.rollback import RollbackManager
from core.elevation import is_admin, request_admin


def enable_high_dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def apply_dark_titlebar(window: tk.Tk):
    """Enables Windows 10/11 native dark title bar via DWM API."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        # DWMWA_USE_IMMUTABLE_DARK_MODE = 20 (Win11 / Win10 20H1+), 19 (older Win10)
        value = ctypes.c_int(1)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
        )
        if res != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 19, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        pass


class Palette:
    """Modern Fluent-inspired Dark Color Palette."""
    BG = "#0f1117"            # Main window background
    SIDEBAR = "#13161f"       # Left navigation rail
    CARD = "#191d28"          # Elevated card surface
    CARD_ALT = "#1d2230"      # Alternating row / subtle contrast
    INPUT = "#222736"         # Input fields & secondary buttons
    INPUT_HOVER = "#2c3246"   # Hover state for secondary buttons
    BORDER = "#262b3d"        # Subtle hairline border

    ACCENT = "#3b82f6"        # Primary blue
    ACCENT_HOVER = "#60a5fa"  # Primary blue hover

    SUCCESS = "#34d399"       # Emerald text
    SUCCESS_BG = "#064e3b"    # Emerald pill bg
    WARNING = "#fbbf24"       # Amber text
    WARNING_BG = "#451a03"    # Amber pill bg
    DANGER = "#f87171"        # Coral red

    TEXT = "#f1f5f9"          # Primary crisp white-slate
    TEXT_DIM = "#94a3b8"      # Secondary muted slate
    TEXT_MUTED = "#64748b"    # Subtle captions
    SELECT_BG = "#253352"     # Treeview selection


class ToggleSwitch(tk.Frame):
    """Modern pill-shaped toggle switch drawn on Canvas with label."""

    def __init__(self, parent, text: str, variable: tk.BooleanVar, bg=Palette.CARD, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.variable = variable
        self.bg = bg

        self.canvas = tk.Canvas(
            self, width=36, height=20, bg=bg, highlightthickness=0, cursor="hand2"
        )
        self.canvas.pack(side=tk.LEFT, padx=(0, 10))

        self.label = tk.Label(
            self, text=text, bg=bg, fg=Palette.TEXT, font=("Segoe UI", 9), cursor="hand2"
        )
        self.label.pack(side=tk.LEFT, fill=tk.X)

        self.canvas.bind("<Button-1>", self._toggle)
        self.label.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())

    def _draw(self):
        self.canvas.delete("all")
        on = self.variable.get()
        track_color = Palette.ACCENT if on else "#334155"
        knob_x = 18 if on else 2

        # Pill track (rounded rectangle via two circles + rect)
        self.canvas.create_oval(1, 2, 17, 18, fill=track_color, outline="")
        self.canvas.create_oval(19, 2, 35, 18, fill=track_color, outline="")
        self.canvas.create_rectangle(9, 2, 27, 18, fill=track_color, outline="")

        # White knob
        self.canvas.create_oval(knob_x + 1, 4, knob_x + 13, 16, fill="#ffffff", outline="")


class FlatButton(tk.Label):
    """Sleek flat button with smooth hover color transition."""

    def __init__(
        self,
        parent,
        text: str,
        command: Callable,
        bg: str = Palette.INPUT,
        hover_bg: str = Palette.INPUT_HOVER,
        fg: str = Palette.TEXT,
        font=("Segoe UI", 9, "bold"),
        padx: int = 14,
        pady: int = 7,
        **kwargs
    ):
        super().__init__(
            parent, text=text, bg=bg, fg=fg, font=font,
            padx=padx, pady=pady, cursor="hand2", **kwargs
        )
        self._bg = bg
        self._hover_bg = hover_bg
        self._command = command
        self._enabled = True

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _e):
        if self._enabled:
            self.configure(bg=self._hover_bg)

    def _on_leave(self, _e):
        if self._enabled:
            self.configure(bg=self._bg)

    def _on_click(self, _e):
        if self._enabled and self._command:
            self._command()


class AppDataMigratorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("WinMigrate — AppData to Program Files")
        self.root.geometry("1120x740")
        self.root.minsize(920, 620)
        self.root.configure(bg=Palette.BG)

        apply_dark_titlebar(self.root)

        self.apps: List[InstalledApp] = []
        self.selected_app: Optional[InstalledApp] = None
        self.log_queue: queue.Queue = queue.Queue()
        self.ui_queue: queue.Queue = queue.Queue()
        self.is_migrating = False
        self.is_scanning = False
        self.current_page = "apps"

        self._setup_ttk_styles()
        self._build_layout()
        self._start_log_consumer()

        # Start initial scan after window renders
        self.root.after(150, self.refresh_apps)

    def _setup_ttk_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Modern.Treeview",
            background=Palette.CARD,
            foreground=Palette.TEXT,
            fieldbackground=Palette.CARD,
            rowheight=32,
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 9),
        )
        style.map(
            "Modern.Treeview",
            background=[("selected", Palette.SELECT_BG)],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Modern.Treeview.Heading",
            background=Palette.SIDEBAR,
            foreground=Palette.TEXT_DIM,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
            padding=[10, 8],
        )
        style.map(
            "Modern.Treeview.Heading",
            background=[("active", Palette.INPUT)],
            foreground=[("active", Palette.TEXT)],
        )

        # Minimal dark scrollbar
        style.configure(
            "Dark.Vertical.TScrollbar",
            gripcount=0,
            background=Palette.INPUT,
            darkcolor=Palette.CARD,
            lightcolor=Palette.CARD,
            troughcolor=Palette.CARD,
            bordercolor=Palette.CARD,
            arrowcolor=Palette.TEXT_DIM,
            relief="flat",
            width=10,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", Palette.INPUT_HOVER)],
        )

    def _build_layout(self):
        # Main horizontal split: Left Sidebar + Right Content
        container = tk.Frame(self.root, bg=Palette.BG)
        container.pack(fill=tk.BOTH, expand=True)

        # === LEFT SIDEBAR ===
        self.sidebar = tk.Frame(container, bg=Palette.SIDEBAR, width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        # Subtle vertical divider
        tk.Frame(container, bg=Palette.BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        # Brand Header
        brand_frame = tk.Frame(self.sidebar, bg=Palette.SIDEBAR)
        brand_frame.pack(fill=tk.X, padx=18, pady=(22, 24))

        tk.Label(
            brand_frame, text="WinMigrate", font=("Segoe UI", 16, "bold"),
            bg=Palette.SIDEBAR, fg=Palette.TEXT
        ).pack(anchor=tk.W)
        tk.Label(
            brand_frame, text="AppData -> Program Files", font=("Segoe UI", 8),
            bg=Palette.SIDEBAR, fg=Palette.TEXT_MUTED
        ).pack(anchor=tk.W, pady=(2, 0))

        # Navigation items
        self.nav_buttons = {}
        self._add_nav_item("apps", "Applications", "0")
        self._add_nav_item("rollback", "Rollback & History", "")
        self._add_nav_item("log", "Execution Log", "")

        # Bottom Admin Badge in Sidebar
        admin_container = tk.Frame(self.sidebar, bg=Palette.SIDEBAR)
        admin_container.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=18)

        if is_admin():
            badge = tk.Frame(admin_container, bg=Palette.SUCCESS_BG, padx=12, pady=8)
            badge.pack(fill=tk.X)
            tk.Label(
                badge, text="● Administrator Mode", font=("Segoe UI", 9, "bold"),
                bg=Palette.SUCCESS_BG, fg=Palette.SUCCESS
            ).pack(anchor=tk.W)
            tk.Label(
                badge, text="Full system & HKLM access", font=("Segoe UI", 8),
                bg=Palette.SUCCESS_BG, fg="#a7f3d0"
            ).pack(anchor=tk.W)
        else:
            badge = tk.Frame(admin_container, bg=Palette.WARNING_BG, padx=12, pady=8, cursor="hand2")
            badge.pack(fill=tk.X)
            lbl1 = tk.Label(
                badge, text="▲ Standard User", font=("Segoe UI", 9, "bold"),
                bg=Palette.WARNING_BG, fg=Palette.WARNING, cursor="hand2"
            )
            lbl1.pack(anchor=tk.W)
            lbl2 = tk.Label(
                badge, text="Click to Elevate (UAC)", font=("Segoe UI", 8, "underline"),
                bg=Palette.WARNING_BG, fg="#fde68a", cursor="hand2"
            )
            lbl2.pack(anchor=tk.W)
            for w in (badge, lbl1, lbl2):
                w.bind("<Button-1>", lambda _e: self._elevate_prompt())

        # === RIGHT CONTENT AREA ===
        right_area = tk.Frame(container, bg=Palette.BG)
        right_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.pages_container = tk.Frame(right_area, bg=Palette.BG)
        self.pages_container.pack(fill=tk.BOTH, expand=True, padx=22, pady=(18, 10))

        # Status Footer
        footer = tk.Frame(right_area, bg=Palette.SIDEBAR, height=30)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)
        tk.Frame(footer, bg=Palette.BORDER, height=1).pack(fill=tk.X, side=tk.TOP)

        self.status_lbl = tk.Label(
            footer, text="Ready", font=("Segoe UI", 8),
            bg=Palette.SIDEBAR, fg=Palette.TEXT_DIM
        )
        self.status_lbl.pack(side=tk.LEFT, padx=16, pady=4)

        # Build Pages
        self.pages = {}
        self.pages["apps"] = self._build_apps_page()
        self.pages["rollback"] = self._build_rollback_page()
        self.pages["log"] = self._build_log_page()

        self._switch_page("apps")

    def _add_nav_item(self, key: str, title: str, badge_text: str):
        row = tk.Frame(self.sidebar, bg=Palette.SIDEBAR, cursor="hand2")
        row.pack(fill=tk.X, padx=10, pady=2)

        indicator = tk.Frame(row, bg=Palette.SIDEBAR, width=3, height=22)
        indicator.pack(side=tk.LEFT, fill=tk.Y, pady=6)

        lbl = tk.Label(
            row, text=title, font=("Segoe UI", 10),
            bg=Palette.SIDEBAR, fg=Palette.TEXT_DIM, padx=12, pady=9, anchor=tk.W, cursor="hand2"
        )
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        badge = None
        if badge_text != "":
            badge = tk.Label(
                row, text=badge_text, font=("Segoe UI", 8, "bold"),
                bg=Palette.INPUT, fg=Palette.TEXT_DIM, padx=7, pady=2
            )
            badge.pack(side=tk.RIGHT, padx=10)

        def on_click(_e):
            self._switch_page(key)

        def on_enter(_e):
            if self.current_page != key:
                row.configure(bg=Palette.CARD)
                lbl.configure(bg=Palette.CARD, fg=Palette.TEXT)

        def on_leave(_e):
            if self.current_page != key:
                row.configure(bg=Palette.SIDEBAR)
                lbl.configure(bg=Palette.SIDEBAR, fg=Palette.TEXT_DIM)

        for w in ([row, lbl] + ([badge] if badge else [])):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        self.nav_buttons[key] = (row, indicator, lbl, badge)

    def _switch_page(self, key: str):
        self.current_page = key
        for k, frame in self.pages.items():
            if k == key:
                frame.pack(fill=tk.BOTH, expand=True)
            else:
                frame.pack_forget()

        for k, (row, indicator, lbl, badge) in self.nav_buttons.items():
            if k == key:
                row.configure(bg=Palette.CARD)
                indicator.configure(bg=Palette.ACCENT)
                lbl.configure(bg=Palette.CARD, fg=Palette.TEXT, font=("Segoe UI", 10, "bold"))
            else:
                row.configure(bg=Palette.SIDEBAR)
                indicator.configure(bg=Palette.SIDEBAR)
                lbl.configure(bg=Palette.SIDEBAR, fg=Palette.TEXT_DIM, font=("Segoe UI", 10))

        if key == "rollback":
            self.refresh_backups()

    def _build_apps_page(self) -> tk.Frame:
        page = tk.Frame(self.pages_container, bg=Palette.BG)

        # Page Header
        hdr = tk.Frame(page, bg=Palette.BG)
        hdr.pack(fill=tk.X, pady=(0, 14))

        tk.Label(
            hdr, text="Installed User Applications", font=("Segoe UI", 15, "bold"),
            bg=Palette.BG, fg=Palette.TEXT
        ).pack(side=tk.LEFT)

        # Toolbar actions on right of header
        actions = tk.Frame(hdr, bg=Palette.BG)
        actions.pack(side=tk.RIGHT)

        # Search input pill
        search_box = tk.Frame(actions, bg=Palette.INPUT, padx=10, pady=4)
        search_box.pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(search_box, text="Search", font=("Segoe UI", 8), bg=Palette.INPUT, fg=Palette.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_apps_tree())
        tk.Entry(
            search_box, textvariable=self.search_var, bg=Palette.INPUT, fg=Palette.TEXT,
            insertbackground=Palette.TEXT, relief="flat", font=("Segoe UI", 9), width=20
        ).pack(side=tk.LEFT)

        FlatButton(actions, text="Refresh", command=self.refresh_apps).pack(side=tk.LEFT, padx=3)
        FlatButton(actions, text="Select All", command=self._select_all_apps).pack(side=tk.LEFT, padx=3)
        FlatButton(actions, text="Clear", command=self._clear_selection).pack(side=tk.LEFT, padx=3)
        FlatButton(
            actions, text="Migrate to Program Files",
            command=lambda: self._start_migration(dry_run=False),
            bg=Palette.ACCENT, hover_bg=Palette.ACCENT_HOVER, fg="#ffffff"
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Bottom Cards Row (packed BEFORE expanding table so buttons are never clipped)
        bottom = tk.Frame(page, bg=Palette.BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        # Left Card: Inspector
        inspector = tk.Frame(
            bottom, bg=Palette.CARD, highlightbackground=Palette.BORDER, highlightthickness=1, padx=16, pady=12
        )
        inspector.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        tk.Label(
            inspector, text="Application Inspector", font=("Segoe UI", 10, "bold"),
            bg=Palette.CARD, fg=Palette.TEXT
        ).pack(anchor=tk.W, pady=(0, 6))

        self.details_text = tk.Text(
            inspector, bg=Palette.CARD, fg=Palette.TEXT_DIM, relief="flat",
            font=("Consolas", 9), wrap=tk.WORD, state="disabled", height=9, highlightthickness=0
        )
        self.details_text.pack(fill=tk.BOTH, expand=True)
        self._set_inspector_placeholder()

        # Right Card: Options & Execute
        opts_card = tk.Frame(
            bottom, bg=Palette.CARD, highlightbackground=Palette.BORDER, highlightthickness=1,
            padx=16, pady=12
        )
        opts_card.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

        tk.Label(
            opts_card, text="Migration Tweaks", font=("Segoe UI", 10, "bold"),
            bg=Palette.CARD, fg=Palette.TEXT
        ).pack(anchor=tk.W, pady=(0, 8))

        self.opt_junction = tk.BooleanVar(value=True)
        self.opt_common_shortcuts = tk.BooleanVar(value=True)
        self.opt_system_path = tk.BooleanVar(value=True)
        self.opt_backup = tk.BooleanVar(value=True)
        self.opt_kill_procs = tk.BooleanVar(value=False)

        ToggleSwitch(opts_card, "Create NTFS Junction in AppData", self.opt_junction).pack(anchor=tk.W, pady=3)
        ToggleSwitch(opts_card, "Promote shortcuts to All Users", self.opt_common_shortcuts).pack(anchor=tk.W, pady=3)
        ToggleSwitch(opts_card, "Promote PATH to System Environment", self.opt_system_path).pack(anchor=tk.W, pady=3)
        ToggleSwitch(opts_card, "Backup registry & rollback manifest", self.opt_backup).pack(anchor=tk.W, pady=3)
        ToggleSwitch(opts_card, "Terminate running app processes", self.opt_kill_procs).pack(anchor=tk.W, pady=3)

        btn_row = tk.Frame(opts_card, bg=Palette.CARD)
        btn_row.pack(fill=tk.X, pady=(12, 0))

        FlatButton(
            btn_row, text="Dry Run", command=lambda: self._start_migration(dry_run=True),
            bg=Palette.INPUT, hover_bg=Palette.INPUT_HOVER, fg=Palette.TEXT
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        FlatButton(
            btn_row, text="Migrate Selected", command=lambda: self._start_migration(dry_run=False),
            bg=Palette.ACCENT, hover_bg=Palette.ACCENT_HOVER, fg="#ffffff"
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

        # Apps Table Card (fills remaining space between header and bottom cards)
        table_card = tk.Frame(page, bg=Palette.CARD, highlightbackground=Palette.BORDER, highlightthickness=1)
        table_card.pack(fill=tk.BOTH, expand=True)

        cols = ("Name", "Size", "Status", "Source", "Target", "Shortcuts")
        self.tree = ttk.Treeview(
            table_card, columns=cols, show="headings", selectmode="extended", style="Modern.Treeview"
        )
        self.tree.heading("Name", text="Application")
        self.tree.heading("Size", text="Size")
        self.tree.heading("Status", text="Status")
        self.tree.heading("Source", text="Current Location (AppData)")
        self.tree.heading("Target", text="Target (Program Files)")
        self.tree.heading("Shortcuts", text="Links")

        self.tree.column("Name", width=165, minwidth=120)
        self.tree.column("Size", width=85, anchor="center", stretch=False)
        self.tree.column("Status", width=85, anchor="center", stretch=False)
        self.tree.column("Source", width=260)
        self.tree.column("Target", width=220)
        self.tree.column("Shortcuts", width=60, anchor="center", stretch=False)

        self.tree.tag_configure("odd", background=Palette.CARD)
        self.tree.tag_configure("even", background=Palette.CARD_ALT)
        self.tree.tag_configure("running", foreground=Palette.WARNING)

        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Right-click context menu on table
        self._ctx_row: Optional[str] = None
        ctx_menu = tk.Menu(
            self.tree, tearoff=0, bg=Palette.CARD, fg=Palette.TEXT,
            activebackground=Palette.ACCENT, activeforeground="#ffffff",
            font=("Segoe UI", 9)
        )
        ctx_menu.add_command(
            label="Migrate to Program Files",
            command=lambda: self._start_migration(dry_run=False)
        )
        ctx_menu.add_command(
            label="Dry Run Preview",
            command=lambda: self._start_migration(dry_run=True)
        )
        ctx_menu.add_separator()
        ctx_menu.add_command(
            label="Select / Deselect",
            command=self._ctx_toggle_select
        )
        ctx_menu.add_command(
            label="Select All",
            command=self._select_all_apps
        )
        ctx_menu.add_command(
            label="Clear Selection",
            command=self._clear_selection
        )
        ctx_menu.add_separator()
        ctx_menu.add_command(
            label="Show in Folder",
            command=self._show_in_folder
        )
        ctx_menu.add_separator()
        ctx_menu.add_command(
            label="Delete App...",
            foreground=Palette.DANGER,
            command=self._delete_selected_apps
        )

        def _show_ctx(event):
            row = self.tree.identify_row(event.y)
            self._ctx_row = row if row else None
            if row and row not in self.tree.selection():
                self.tree.selection_set(row)
                self._on_tree_select(None)
            if row or self.tree.selection():
                ctx_menu.tk_popup(event.x_root, event.y_root)

        self.tree.bind("<Button-3>", _show_ctx)

        return page

    def _build_rollback_page(self) -> tk.Frame:
        page = tk.Frame(self.pages_container, bg=Palette.BG)

        hdr = tk.Frame(page, bg=Palette.BG)
        hdr.pack(fill=tk.X, pady=(0, 14))

        tk.Label(
            hdr, text="Rollback & Migration History", font=("Segoe UI", 15, "bold"),
            bg=Palette.BG, fg=Palette.TEXT
        ).pack(side=tk.LEFT)

        actions = tk.Frame(hdr, bg=Palette.BG)
        actions.pack(side=tk.RIGHT)
        FlatButton(actions, text="Refresh", command=self.refresh_backups).pack(side=tk.LEFT, padx=4)
        FlatButton(
            actions, text="Restore Selected to AppData", command=self._perform_rollback,
            bg=Palette.ACCENT, hover_bg=Palette.ACCENT_HOVER, fg="#ffffff"
        ).pack(side=tk.LEFT, padx=(4, 0))

        table_card = tk.Frame(page, bg=Palette.CARD, highlightbackground=Palette.BORDER, highlightthickness=1)
        table_card.pack(fill=tk.BOTH, expand=True)

        cols = ("BackupID", "App Name", "Source (AppData)", "Timestamp", "RolledBack")
        self.rollback_tree = ttk.Treeview(
            table_card, columns=cols, show="headings", selectmode="browse", style="Modern.Treeview"
        )
        self.rollback_tree.heading("BackupID", text="Backup ID")
        self.rollback_tree.heading("App Name", text="Application")
        self.rollback_tree.heading("Source (AppData)", text="Original AppData Path")
        self.rollback_tree.heading("Timestamp", text="Migrated At")
        self.rollback_tree.heading("RolledBack", text="Restored?")

        self.rollback_tree.column("BackupID", width=210)
        self.rollback_tree.column("App Name", width=170)
        self.rollback_tree.column("Source (AppData)", width=310)
        self.rollback_tree.column("Timestamp", width=140)
        self.rollback_tree.column("RolledBack", width=90, anchor="center", stretch=False)

        scroll = ttk.Scrollbar(table_card, orient="vertical", command=self.rollback_tree.yview, style="Dark.Vertical.TScrollbar")
        self.rollback_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.rollback_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        return page

    def _build_log_page(self) -> tk.Frame:
        page = tk.Frame(self.pages_container, bg=Palette.BG)

        hdr = tk.Frame(page, bg=Palette.BG)
        hdr.pack(fill=tk.X, pady=(0, 14))

        tk.Label(
            hdr, text="Execution Log", font=("Segoe UI", 15, "bold"),
            bg=Palette.BG, fg=Palette.TEXT
        ).pack(side=tk.LEFT)

        actions = tk.Frame(hdr, bg=Palette.BG)
        actions.pack(side=tk.RIGHT)
        FlatButton(actions, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT, padx=4)
        FlatButton(actions, text="Export Log...", command=self._export_log).pack(side=tk.LEFT, padx=(4, 0))

        log_card = tk.Frame(page, bg="#0b0d13", highlightbackground=Palette.BORDER, highlightthickness=1, padx=12, pady=12)
        log_card.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_card, bg="#0b0d13", fg=Palette.TEXT, insertbackground="#ffffff",
            relief="flat", font=("Consolas", 9), wrap=tk.WORD, highlightthickness=0
        )
        self.log_text.tag_configure("err", foreground=Palette.DANGER)
        self.log_text.tag_configure("ok", foreground=Palette.SUCCESS)
        self.log_text.tag_configure("warn", foreground=Palette.WARNING)
        self.log_text.tag_configure("hdr", foreground=Palette.ACCENT_HOVER)

        scroll = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview, style="Dark.Vertical.TScrollbar")
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        return page

    def _set_inspector_placeholder(self):
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert(
            tk.END,
            "Select an application from the list above to inspect its\n"
            "executable, registry uninstall key, shortcuts, and PATH entries."
        )
        self.details_text.configure(state="disabled")

    def _elevate_prompt(self):
        if messagebox.askyesno(
            "Administrator Elevation",
            "Writing to Program Files and HKLM requires Administrator privileges.\n\n"
            "Restart the application as Administrator now?"
        ):
            if request_admin():
                self.root.destroy()
                sys.exit(0)
            else:
                messagebox.showerror("Elevation Failed", "Could not request Administrator rights.")

    def log(self, message: str):
        self.log_queue.put(message)

    def _start_log_consumer(self):
        def check_queues():
            while not self.log_queue.empty():
                try:
                    msg = self.log_queue.get_nowait()
                    tag = None
                    upper = msg.upper()
                    if "ERROR" in upper or "FAILED" in upper:
                        tag = "err"
                    elif "SUCCESS" in upper or "COMPLETED" in upper:
                        tag = "ok"
                    elif "WARNING" in upper or "DRY RUN" in upper:
                        tag = "warn"
                    elif msg.startswith("====") or msg.startswith("Starting"):
                        tag = "hdr"

                    if tag:
                        self.log_text.insert(tk.END, msg + "\n", tag)
                    else:
                        self.log_text.insert(tk.END, msg + "\n")
                    self.log_text.see(tk.END)
                except queue.Empty:
                    break

            while not self.ui_queue.empty():
                try:
                    cb = self.ui_queue.get_nowait()
                    cb()
                except queue.Empty:
                    break

            self.root.after(50, check_queues)

        self.root.after(50, check_queues)

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def _export_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text Files", "*.txt")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.log_text.get("1.0", tk.END))
                messagebox.showinfo("Log Exported", f"Log saved to {path}")
            except Exception as e:
                messagebox.showerror("Error", f"Could not save log: {e}")

    def refresh_apps(self):
        if self.is_scanning:
            return
        self.is_scanning = True
        self.status_lbl.configure(text="Scanning AppData & HKCU registry for user-installed applications...", fg=Palette.ACCENT_HOVER)
        self.tree.delete(*self.tree.get_children())
        self._set_inspector_placeholder()

        def scan_worker():
            try:
                apps = scan_for_installed_apps()
                self.ui_queue.put(lambda: self._on_scan_completed(apps, None))
            except Exception as e:
                err_msg = str(e)
                self.ui_queue.put(lambda: self._on_scan_completed([], err_msg))

        threading.Thread(target=scan_worker, daemon=True).start()

    def _on_scan_completed(self, apps: List[InstalledApp], error: Optional[str]):
        self.is_scanning = False
        if error:
            self.log(f"ERROR during application scan: {error}")
            self.status_lbl.configure(text=f"Scan error: {error}", fg=Palette.DANGER)
            return

        self.apps = apps
        self._filter_apps_tree()

        # Update sidebar badge
        badge = self.nav_buttons["apps"][3]
        if badge:
            badge.configure(text=str(len(apps)))

        self.status_lbl.configure(
            text=f"Ready — Discovered {len(apps)} user-installed application(s) in AppData.",
            fg=Palette.TEXT_DIM
        )

    def _filter_apps_tree(self):
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().lower().strip()

        row_num = 0
        for idx, app in enumerate(self.apps):
            disp = app.display_name or app.name
            if query and query not in disp.lower() and query not in app.source_path.lower():
                continue

            status = "Running" if app.is_running else "Stopped"
            tag = "running" if app.is_running else ("even" if row_num % 2 == 0 else "odd")
            row_num += 1

            self.tree.insert(
                "", tk.END, iid=str(idx),
                values=(
                    disp,
                    app.formatted_size,
                    status,
                    app.source_path,
                    app.target_path,
                    str(len(app.shortcuts)),
                ),
                tags=(tag,),
            )

    def _select_all_apps(self):
        for item in self.tree.get_children():
            self.tree.selection_add(item)

    def _clear_selection(self):
        self.tree.selection_remove(self.tree.selection())
        self._set_inspector_placeholder()

    def _ctx_toggle_select(self):
        if not self._ctx_row:
            return
        if self._ctx_row in self.tree.selection():
            self.tree.selection_remove(self._ctx_row)
        else:
            self.tree.selection_add(self._ctx_row)
            self._on_tree_select(None)

    def _show_in_folder(self):
        sel = self.tree.selection()
        if not sel:
            return
        app = self.apps[int(sel[0])]
        folder = app.source_path
        if os.path.exists(folder):
            try:
                os.startfile(folder)
            except Exception:
                subprocess.Popen(["explorer.exe", folder])
        else:
            messagebox.showwarning("Folder Not Found", f"Directory does not exist:\n{folder}")

    def _delete_selected_apps(self):
        sel = self.tree.selection()
        if not sel:
            return
        selected_apps: List[InstalledApp] = [self.apps[int(i)] for i in sel]
        summary = "\n".join(
            f"• {a.display_name or a.name} ({a.formatted_size})\n  {a.source_path}"
            for a in selected_apps
        )
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete the following {len(selected_apps)} app(s) from AppData?\n\n"
            f"{summary}\n\n"
            "This removes the application folder and its HKCU uninstall registry entry (if any). "
            "This action cannot be undone.",
            icon="warning"
        ):
            return

        self._switch_page("log")
        self.status_lbl.configure(text=f"Deleting {len(selected_apps)} app(s)...", fg=Palette.WARNING)

        def delete_worker():
            deleted = 0
            failed = 0
            for app in selected_apps:
                name = app.display_name or app.name
                self.log(f"\n==== Deleting {name} ====")
                try:
                    if app.running_pids:
                        for pid in app.running_pids:
                            self.log(f"Terminating PID {pid}...")
                            subprocess.run(["taskkill.exe", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                    if os.path.exists(app.source_path):
                        shutil.rmtree(app.source_path)
                        self.log(f"Deleted folder: {app.source_path}")
                    if app.registry_key_name:
                        sub = f"{UNINSTALL_SUBKEY}\\{app.registry_key_name}"
                        ok, msg = delete_registry_tree(winreg.HKEY_CURRENT_USER, sub)
                        self.log(f"Registry cleanup: {msg}")
                    deleted += 1
                    self.log(f"SUCCESS: Deleted {name}")
                except Exception as e:
                    failed += 1
                    self.log(f"ERROR deleting {name}: {e}")

            self.ui_queue.put(lambda: self._on_delete_completed(deleted, failed))

        threading.Thread(target=delete_worker, daemon=True).start()

    def _on_delete_completed(self, deleted: int, failed: int):
        if failed == 0:
            self.status_lbl.configure(text=f"Deleted {deleted} app(s).", fg=Palette.SUCCESS)
            messagebox.showinfo("Delete Complete", f"Successfully deleted {deleted} app(s).")
        else:
            self.status_lbl.configure(text=f"Delete finished: {deleted} deleted, {failed} failed.", fg=Palette.WARNING)
            messagebox.showwarning("Delete Finished with Errors", f"{deleted} deleted, {failed} failed. Check Execution Log.")
        self.refresh_apps()

    def _on_tree_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < len(self.apps):
            self.selected_app = self.apps[idx]
            self._display_app_details(self.selected_app)

    def _display_app_details(self, app: InstalledApp):
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)

        lines = [
            f"Name         : {app.display_name} ({app.name})",
            f"Publisher    : {app.publisher}   |   Version: {app.version}",
            f"Architecture : {'64-bit (Program Files)' if app.is_64bit else '32-bit (Program Files x86)'}",
            f"Main Binary  : {app.main_exe or 'None detected'}",
            f"Uninstall Key: {app.registry_key_name or 'None (folder-only install)'}",
            f"Running PIDs : {app.running_pids or 'None'}",
            f"Shortcuts    : {len(app.shortcuts)} detected",
        ]
        for sc in app.shortcuts[:5]:
            loc = "Desktop" if sc.is_user_desktop else "Start Menu"
            lines.append(f"  - [{loc}] {os.path.basename(sc.lnk_path)}")

        if app.path_entries:
            lines.append(f"PATH Entries : {len(app.path_entries)}")
            for pe in app.path_entries:
                lines.append(f"  - {pe}")

        self.details_text.insert(tk.END, "\n".join(lines))
        self.details_text.configure(state="disabled")

    def refresh_backups(self):
        self.rollback_tree.delete(*self.rollback_tree.get_children())
        mgr = RollbackManager()
        backups = mgr.list_available_backups()
        for idx, b in enumerate(backups):
            is_rb = "Yes" if b.get("rolled_back_at") else "No"
            tag = "even" if idx % 2 == 0 else "odd"
            self.rollback_tree.insert(
                "", tk.END, iid=b.get("backup_id"),
                values=(
                    b.get("backup_id", ""),
                    b.get("app_name", ""),
                    b.get("source_path", ""),
                    b.get("timestamp", ""),
                    is_rb,
                ),
                tags=(tag,),
            )

    def _start_migration(self, dry_run: bool = False):
        if self.is_migrating:
            messagebox.showwarning("In Progress", "A migration task is already running.")
            return

        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Please select at least one application to migrate.")
            return

        selected_apps: List[InstalledApp] = [self.apps[int(i)] for i in sel]

        if not is_admin() and not dry_run:
            self._elevate_prompt()
            return

        if not dry_run:
            app_names = "\n".join(f"• {a.display_name or a.name} ({a.formatted_size})" for a in selected_apps)
            msg = (
                f"Migrate {len(selected_apps)} app(s) to Program Files?\n\n"
                f"{app_names}\n\n"
                f"This will move files, create junctions (if enabled), replicate HKCU registry to HKLM, "
                f"update shortcuts & PATH, and save a rollback backup."
            )
            if not messagebox.askyesno("Confirm Migration", msg):
                return

        self._switch_page("log")

        options = MigrationOptions(
            create_junction=self.opt_junction.get(),
            migrate_to_all_users_shortcuts=self.opt_common_shortcuts.get(),
            update_system_path=self.opt_system_path.get(),
            create_backup=self.opt_backup.get(),
            kill_running_processes=self.opt_kill_procs.get(),
            dry_run=dry_run,
        )

        self.is_migrating = True
        self.status_lbl.configure(
            text=f"{'Simulating' if dry_run else 'Migrating'} {len(selected_apps)} application(s)...",
            fg=Palette.ACCENT_HOVER
        )

        def migration_worker():
            migrator = Migrator(log_callback=self.log)
            results = []
            for app in selected_apps:
                try:
                    results.append(migrator.migrate(app, options))
                except Exception as e:
                    self.log(f"ERROR migrating {app.name}: {e}")
                    results.append(MigrationResult(
                        app_name=app.name, success=False,
                        source_path=app.source_path, target_path=app.target_path,
                        error_message=str(e)
                    ))
            self.ui_queue.put(lambda: self._on_migration_completed(results, dry_run))

        threading.Thread(target=migration_worker, daemon=True).start()

    def _on_migration_completed(self, results: List[MigrationResult], dry_run: bool):
        self.is_migrating = False
        successes = sum(1 for r in results if r.success)
        failures = len(results) - successes

        if dry_run:
            self.status_lbl.configure(text="Dry run simulation completed.", fg=Palette.SUCCESS)
            messagebox.showinfo("Dry Run Complete", f"Simulated {len(results)} application(s). See Execution Log.")
        else:
            self.status_lbl.configure(
                text=f"Migration finished: {successes} succeeded, {failures} failed.",
                fg=Palette.SUCCESS if failures == 0 else Palette.WARNING
            )
            if failures == 0:
                messagebox.showinfo("Migration Complete", f"Successfully migrated {successes} application(s)!")
            else:
                messagebox.showwarning(
                    "Migration Completed with Errors",
                    f"{successes} succeeded, {failures} failed. Check Execution Log for details."
                )
            self.refresh_apps()
            self.refresh_backups()

    def _perform_rollback(self):
        sel = self.rollback_tree.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Please select a backup to restore.")
            return

        backup_id = sel[0]
        if not is_admin():
            self._elevate_prompt()
            return

        if not messagebox.askyesno(
            "Confirm Rollback",
            f"Restore '{backup_id}' back to AppData?\n\n"
            "This removes the Program Files copy and restores files, HKCU registry, and shortcuts."
        ):
            return

        self._switch_page("log")
        self.status_lbl.configure(text=f"Rolling back {backup_id}...", fg=Palette.ACCENT_HOVER)

        def rollback_worker():
            mgr = RollbackManager(log_callback=self.log)
            ok, msg = mgr.rollback(backup_id)
            self.ui_queue.put(lambda: self._on_rollback_completed(ok, msg))

        threading.Thread(target=rollback_worker, daemon=True).start()

    def _on_rollback_completed(self, ok: bool, msg: str):
        if ok:
            self.status_lbl.configure(text=msg, fg=Palette.SUCCESS)
            messagebox.showinfo("Rollback Succeeded", msg)
        else:
            self.status_lbl.configure(text=msg, fg=Palette.DANGER)
            messagebox.showerror("Rollback Failed", msg)
        self.refresh_apps()
        self.refresh_backups()


def run_gui():
    enable_high_dpi()
    root = tk.Tk()
    AppDataMigratorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
