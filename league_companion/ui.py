"""Desktop controls for the local League client companion."""

from __future__ import annotations

from dataclasses import fields, replace
from importlib.resources import as_file, files
from pathlib import Path
from queue import Empty
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .config import ROLES, Settings, save_settings


SPELLS = {
    "Barrier": 21,
    "Cleanse": 1,
    "Exhaust": 3,
    "Flash": 4,
    "Ghost": 6,
    "Heal": 7,
    "Ignite": 14,
    "Smite": 11,
    "Teleport": 12,
    "Clarity (mode dependent)": 13,
    "Mark / Snowball (ARAM)": 32,
}
PHASE_LABELS = {
    "readycheck": "Ready Check",
    "champselect": "Champion Select",
    "inprogress": "In Progress",
    "endofgame": "End of Game",
}

BACKGROUND = "#0b1420"
SIDEBAR = "#101d2b"
CARD = "#162638"
CARD_RAISED = "#1d3145"
FIELD = "#102031"
BORDER = "#2b4153"
TEXT = "#f4f4f0"
MUTED = "#adbecb"
SUBTLE = "#8298aa"
GOLD = "#d9b878"
GOLD_HOVER = "#ecd098"
GREEN = "#76d5ae"
AMBER = "#edc379"


class ToggleRow(tk.Frame):
    """A large, keyboard-operable switch with a useful description."""

    def __init__(self, parent: tk.Widget, variable: tk.BooleanVar, title: str, detail: str) -> None:
        super().__init__(parent, background=CARD)
        self.variable = variable
        self.columnconfigure(0, weight=1)
        self.title = tk.Label(
            self, text=title, background=CARD, foreground=TEXT,
            font=("Segoe UI", 11, "bold"), cursor="hand2", anchor="w",
        )
        self.title.grid(row=0, column=0, sticky="w")
        self.detail = tk.Label(
            self, text=detail, background=CARD, foreground=MUTED,
            font=("Segoe UI", 10), wraplength=430, justify="left",
            cursor="hand2", anchor="w",
        )
        self.detail.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.switch = tk.Canvas(
            self, width=50, height=30, background=CARD, borderwidth=0,
            highlightthickness=1, highlightbackground=CARD,
            highlightcolor=GOLD, takefocus=1, cursor="hand2",
        )
        self.switch.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
        for widget in (self.title, self.detail, self.switch):
            widget.bind("<Button-1>", self._toggle)
        self.switch.bind("<space>", self._toggle)
        self.switch.bind("<Return>", self._toggle)
        self.variable.trace_add("write", self._draw)
        self._draw()

    def _toggle(self, _event: tk.Event) -> None:
        self.variable.set(not self.variable.get())

    def _draw(self, *_: str) -> None:
        self.switch.delete("all")
        enabled = self.variable.get()
        track = GOLD if enabled else "#496176"
        self.switch.create_oval(2, 4, 24, 26, fill=track, outline=track)
        self.switch.create_oval(26, 4, 48, 26, fill=track, outline=track)
        self.switch.create_rectangle(13, 4, 37, 26, fill=track, outline=track)
        x = 25 if enabled else 5
        self.switch.create_oval(x, 6, x + 18, 24, fill=BACKGROUND if enabled else TEXT, outline="")


class CompanionUI:
    PAGES = (
        ("match", "Match", "Match readiness", "Handle ready checks, queue limits, and reconnects."),
        ("draft", "Draft", "Champion select", "Set your champion priorities and loadout."),
        ("lobby", "Lobby", "Lobby preferences", "Choose roles and what happens after a game."),
        ("connection", "Connection", "Client connection", "Manage local client discovery and refresh data."),
        ("activity", "Activity", "Activity", "See what the companion has done recently."),
    )

    def __init__(self, settings: Settings, config_path: Path, controller: Any) -> None:
        self.settings = settings
        self.config_path = config_path
        self.controller = controller
        self.root = tk.Tk()
        self.root.title("League Companion")
        self._icon_large = self._load_image("poro.png")
        self._icon_small = self._load_image("poro-small.png")
        self._sidebar_logo = self._load_image("poro-sidebar.png")
        self.root.iconphoto(True, self._icon_large, self._icon_small)
        self.root.geometry("1100x760")
        self.root.minsize(900, 640)
        self.root.configure(background=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.closed = False
        self.champions: list[dict[str, Any]] = []
        self.rune_pages: dict[str, int] = {}
        self.variables: dict[str, tk.Variable] = {}
        self.status = tk.StringVar(value="Connecting to League")
        self.phase = tk.StringVar(value="Waiting for client")
        self.paused = tk.BooleanVar(value=self.controller.paused)
        self.feedback = tk.StringVar(value="Settings loaded")
        self.page_title = tk.StringVar()
        self.page_description = tk.StringVar()
        self._suppress_change = False
        self._active_canvas: tk.Canvas | None = None
        self._pages: dict[str, tk.Frame] = {}
        self._page_canvases: dict[str, tk.Canvas] = {}
        self._nav_buttons: dict[str, tuple[tk.Frame, tk.Button]] = {}
        self._theme()
        self._variables()
        self._build()
        for variable in self.variables.values():
            variable.trace_add("write", self._mark_changed)
        self.controller.start()
        self.root.after(150, self._drain_events)

    def _load_image(self, name: str) -> tk.PhotoImage:
        asset = files("league_companion").joinpath("assets", name)
        with as_file(asset) as path:
            return tk.PhotoImage(master=self.root, file=str(path))

    def _theme(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=CARD, foreground=TEXT)
        style.configure(
            "App.TEntry", fieldbackground=FIELD, foreground=TEXT,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
            insertcolor=TEXT, padding=(10, 8),
        )
        style.map("App.TEntry", bordercolor=[("focus", GOLD)])
        style.configure(
            "App.TCombobox", fieldbackground=FIELD, background=FIELD,
            foreground=TEXT, arrowcolor=GOLD, bordercolor=BORDER,
            lightcolor=BORDER, darkcolor=BORDER, padding=(10, 7),
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", FIELD)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", FIELD)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", GOLD)],
        )
        style.configure(
            "Primary.TButton", background=GOLD, foreground=BACKGROUND,
            borderwidth=0, padding=(17, 10), font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", GOLD_HOVER), ("pressed", GOLD_HOVER)],
            foreground=[("active", BACKGROUND)],
        )
        style.configure(
            "Secondary.TButton", background=CARD_RAISED, foreground=TEXT,
            borderwidth=0, padding=(13, 9), font=("Segoe UI", 10),
        )
        style.map("Secondary.TButton", background=[("active", "#2d465b")])
        style.configure(
            "Quiet.TButton", background=BACKGROUND, foreground=MUTED,
            borderwidth=0, padding=(12, 8), font=("Segoe UI", 10),
        )
        style.map("Quiet.TButton", background=[("active", CARD_RAISED)], foreground=[("active", TEXT)])
        style.configure(
            "App.Vertical.TScrollbar", background=CARD_RAISED,
            troughcolor=BACKGROUND, bordercolor=BACKGROUND,
            arrowcolor=MUTED, darkcolor=CARD_RAISED, lightcolor=CARD_RAISED,
        )
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#405a6c")
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    def _variables(self) -> None:
        for field in fields(self.settings):
            name = field.name
            value = getattr(self.settings, name)
            if isinstance(value, bool):
                self.variables[name] = tk.BooleanVar(value=value)
            elif isinstance(value, tuple):
                self.variables[name] = tk.StringVar(value=", ".join(map(str, value)))
            else:
                self.variables[name] = tk.StringVar(value=str(value))
        for name in ("spell1_id", "spell2_id"):
            spell_id = getattr(self.settings, name)
            self.variables[name].set(
                next((label for label, value in SPELLS.items() if value == spell_id), str(spell_id))
            )

    def _build(self) -> None:
        shell = tk.Frame(self.root, background=BACKGROUND)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)

        self._build_sidebar(shell)
        main = tk.Frame(shell, background=BACKGROUND)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        header = tk.Frame(main, background=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 15))
        tk.Label(
            header, text="AUTOMATION CONTROL", background=BACKGROUND,
            foreground=GOLD, font=("Segoe UI", 9, "bold"), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            header, textvariable=self.page_title, background=BACKGROUND,
            foreground=TEXT, font=("Segoe UI", 25, "bold"), anchor="w",
        ).pack(anchor="w", pady=(3, 1))
        tk.Label(
            header, textvariable=self.page_description, background=BACKGROUND,
            foreground=MUTED, font=("Segoe UI", 10), anchor="w",
        ).pack(anchor="w")

        status_card = tk.Frame(
            main, background=CARD, highlightbackground=BORDER, highlightthickness=1,
        )
        status_card.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 16))
        status_card.columnconfigure(1, weight=1)
        self.status_dot = tk.Canvas(
            status_card, width=14, height=14, background=CARD, highlightthickness=0,
        )
        self.status_dot.grid(row=0, column=0, padx=(17, 9), pady=17)
        tk.Label(
            status_card, textvariable=self.status, background=CARD,
            foreground=TEXT, font=("Segoe UI", 10, "bold"), anchor="w",
        ).grid(row=0, column=1, sticky="w")
        tk.Label(
            status_card, text="PHASE", background=CARD, foreground=SUBTLE,
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=2, padx=(12, 8))
        tk.Label(
            status_card, textvariable=self.phase, background=CARD,
            foreground=MUTED, font=("Segoe UI", 10),
        ).grid(row=0, column=3, padx=(0, 17))
        self.pause_button = ttk.Button(status_card, command=self._toggle_pause)
        self.pause_button.grid(row=0, column=4, padx=(0, 12), pady=9)
        self._sync_pause_state()
        self._update_status_visual()

        self.page_holder = tk.Frame(main, background=BACKGROUND)
        self.page_holder.grid(row=2, column=0, sticky="nsew", padx=(28, 20))
        self.page_holder.rowconfigure(0, weight=1)
        self.page_holder.columnconfigure(0, weight=1)
        for key, _nav, _title, _description in self.PAGES:
            self._build_page(key)

        actions = tk.Frame(main, background=SIDEBAR, highlightbackground=BORDER, highlightthickness=1)
        actions.grid(row=3, column=0, sticky="ew", padx=28, pady=(14, 23))
        actions.columnconfigure(0, weight=1)
        feedback_area = tk.Frame(actions, background=SIDEBAR)
        feedback_area.grid(row=0, column=0, sticky="w", padx=17, pady=12)
        tk.Label(
            feedback_area, text="SETTINGS", background=SIDEBAR, foreground=SUBTLE,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        self.feedback_label = tk.Label(
            feedback_area, textvariable=self.feedback, background=SIDEBAR,
            foreground=MUTED, font=("Segoe UI", 10),
        )
        self.feedback_label.pack(anchor="w", pady=(2, 0))
        ttk.Button(
            actions, text="Apply for now", style="Secondary.TButton",
            command=lambda: self._apply(False),
        ).grid(row=0, column=1, padx=(0, 9), pady=12)
        ttk.Button(
            actions, text="Apply & save", style="Primary.TButton",
            command=lambda: self._apply(True),
        ).grid(row=0, column=2, padx=(0, 12), pady=12)

        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel)
        self.root.bind_all("<Button-5>", self._on_mousewheel)
        self._select_page("match")

    def _build_sidebar(self, parent: tk.Frame) -> None:
        sidebar = tk.Frame(parent, width=226, background=SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        brand = tk.Frame(sidebar, background=SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(25, 37))
        mark = tk.Label(
            brand, image=self._sidebar_logo, background=SIDEBAR,
            borderwidth=0, highlightthickness=0,
        )
        mark.pack(side="left", padx=(0, 11))
        names = tk.Frame(brand, background=SIDEBAR)
        names.pack(side="left", fill="y")
        tk.Label(
            names, text="LEAGUE", background=SIDEBAR, foreground=TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(3, 0))
        tk.Label(
            names, text="COMPANION", background=SIDEBAR, foreground=GOLD,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            sidebar, text="WORKSPACE", background=SIDEBAR, foreground=SUBTLE,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=22, pady=(0, 11))
        for index, (key, label, _title, _description) in enumerate(self.PAGES, start=1):
            row = tk.Frame(sidebar, background=SIDEBAR)
            row.pack(fill="x", padx=(10, 11), pady=3)
            bar = tk.Frame(row, background=SIDEBAR, width=3)
            bar.pack(side="left", fill="y")
            button = tk.Button(
                row, text=f"{index:02d}    {label}", command=lambda page=key: self._select_page(page),
                background=SIDEBAR, foreground=MUTED, activebackground=CARD_RAISED,
                activeforeground=TEXT, relief="flat", borderwidth=0,
                highlightthickness=0, anchor="w", padx=14, pady=12,
                font=("Segoe UI", 10, "bold"), cursor="hand2",
            )
            button.pack(side="left", fill="x", expand=True)
            self._nav_buttons[key] = (bar, button)
        bottom = tk.Frame(sidebar, background=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=20, pady=(0, 19))
        tk.Frame(bottom, height=1, background=BORDER).pack(fill="x", pady=(0, 14))
        tk.Label(
            bottom, text="The local client API is unsupported and may change.",
            background=SIDEBAR, foreground=SUBTLE, font=("Segoe UI", 9),
            justify="left", wraplength=180,
        ).pack(anchor="w")
        ttk.Button(
            bottom, text="About League Companion", style="Quiet.TButton",
            command=self._about,
        ).pack(anchor="w", pady=(9, 0))

    def _build_page(self, key: str) -> None:
        page = tk.Frame(self.page_holder, background=BACKGROUND)
        page.grid(row=0, column=0, sticky="nsew")
        canvas = tk.Canvas(page, background=BACKGROUND, highlightthickness=0, borderwidth=0)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(
            page, orient="vertical", command=canvas.yview, style="App.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(canvas, background=BACKGROUND)
        content.columnconfigure(0, weight=1)
        item = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event, area=canvas: area.configure(scrollregion=area.bbox("all")))
        canvas.bind(
            "<Configure>",
            lambda event, area=canvas, window=item: area.itemconfigure(window, width=event.width),
        )
        builders: dict[str, Callable[[tk.Frame], None]] = {
            "match": self._match_page,
            "draft": self._draft_page,
            "lobby": self._lobby_page,
            "connection": self._connection_page,
            "activity": self._activity_page,
        }
        builders[key](content)
        self._pages[key] = page
        self._page_canvases[key] = canvas

    def _select_page(self, key: str) -> None:
        for page_key, _label, title, description in self.PAGES:
            bar, button = self._nav_buttons[page_key]
            selected = page_key == key
            bar.configure(background=GOLD if selected else SIDEBAR)
            button.configure(
                background=CARD_RAISED if selected else SIDEBAR,
                foreground=TEXT if selected else MUTED,
            )
            if selected:
                self.page_title.set(title)
                self.page_description.set(description)
        self._pages[key].tkraise()
        self._active_canvas = self._page_canvases[key]

    def _on_mousewheel(self, event: tk.Event) -> None:
        canvas = self._active_canvas
        if canvas is None:
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not canvas:
            if isinstance(widget, (tk.Text, tk.Listbox, ttk.Combobox)):
                return
            widget = widget.master
        if widget is not canvas:
            return
        if getattr(event, "num", None) in (4, 5):
            direction = -1 if event.num == 4 else 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return
            direction = -1 if delta > 0 else 1
        canvas.yview_scroll(direction * 3, "units")

    def _section(
        self, parent: tk.Frame, eyebrow: str, title: str, description: str,
        action: tuple[str, Callable[[], None]] | None = None,
    ) -> tk.Frame:
        card = tk.Frame(parent, background=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 14))
        body = tk.Frame(card, background=CARD)
        body.pack(fill="x", padx=21, pady=20)
        tk.Label(
            body, text=eyebrow, background=CARD, foreground=GOLD,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        heading = tk.Frame(body, background=CARD)
        heading.pack(fill="x", pady=(4, 0))
        heading.columnconfigure(0, weight=1)
        tk.Label(
            heading, text=title, background=CARD, foreground=TEXT,
            font=("Segoe UI", 15, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        if action:
            ttk.Button(
                heading, text=action[0], command=action[1],
                style="Secondary.TButton",
            ).grid(row=0, column=1, padx=(10, 0))
        tk.Label(
            body, text=description, background=CARD, foreground=MUTED,
            font=("Segoe UI", 10), wraplength=520, justify="left", anchor="w",
        ).pack(anchor="w", pady=(6, 11))
        return body

    @staticmethod
    def _divider(parent: tk.Widget) -> None:
        tk.Frame(parent, height=1, background=BORDER).pack(fill="x", pady=(14, 13))

    def _toggle(self, parent: tk.Frame, name: str, title: str, detail: str) -> None:
        ToggleRow(parent, self.variables[name], title, detail).pack(fill="x", pady=(6, 4))

    def _field_row(
        self, parent: tk.Frame, title: str, detail: str, name: str, suffix: str = "",
    ) -> None:
        row = tk.Frame(parent, background=CARD)
        row.pack(fill="x", pady=(4, 5))
        row.columnconfigure(0, weight=1)
        tk.Label(
            row, text=title, background=CARD, foreground=TEXT,
            font=("Segoe UI", 10, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            row, text=detail, background=CARD, foreground=MUTED,
            font=("Segoe UI", 9), wraplength=390, justify="left", anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        control = tk.Frame(row, background=CARD)
        control.grid(row=0, column=1, rowspan=2, sticky="e", padx=(14, 0))
        ttk.Entry(control, textvariable=self.variables[name], width=8, style="App.TEntry").pack(side="left")
        if suffix:
            tk.Label(
                control, text=suffix, background=CARD, foreground=MUTED,
                font=("Segoe UI", 10),
            ).pack(side="left", padx=(9, 0))

    def _caption(self, parent: tk.Frame, text: str) -> None:
        tk.Label(
            parent, text=text, background=CARD, foreground=SUBTLE,
            font=("Segoe UI", 9), wraplength=510, justify="left", anchor="w",
        ).pack(anchor="w", pady=(7, 0))

    def _match_page(self, parent: tk.Frame) -> None:
        ready = self._section(
            parent, "READY CHECK", "Be ready when a match is found",
            "Choose how the companion handles the ready check.",
        )
        self._toggle(
            ready, "auto_accept", "Accept found matches",
            "Accept an unanswered ready check after the delay below.",
        )
        self._divider(ready)
        self._field_row(
            ready, "Accept delay", "Wait after the ready check appears.", "accept_delay", "seconds",
        )

        queue = self._section(
            parent, "QUEUE & RECOVERY", "Stay in control of your session",
            "Set a queue limit and choose whether to reconnect to a game.",
        )
        self._field_row(
            queue, "Stop queue after", "Use 0 to leave the queue running.", "queue_timeout", "seconds",
        )
        self._divider(queue)
        self._toggle(
            queue, "auto_reconnect", "Reconnect to an active game",
            "Use the client's reconnect action when it becomes available.",
        )
        self._caption(queue, "You start matchmaking in the League client yourself.")

    def _priority_input(self, parent: tk.Frame, name: str, label: str) -> None:
        tk.Label(
            parent, text=label, background=CARD, foreground=SUBTLE,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(8, 5))
        ttk.Entry(
            parent, textvariable=self.variables[name], style="App.TEntry",
        ).pack(fill="x")

    def _draft_page(self, parent: tk.Frame) -> None:
        champions = self._section(
            parent, "CHAMPION SELECT", "Champion priorities",
            "The first available champion in each list is chosen.",
            action=("Open champion picker", self._champion_picker),
        )
        self._toggle(
            champions, "auto_pick", "Select a champion",
            "Make a selection during your active pick turn.",
        )
        self._priority_input(champions, "pick_ids", "PICK PRIORITY IDs")
        self._divider(champions)
        self._toggle(
            champions, "auto_ban", "Ban a champion",
            "Avoid teammates' picks and hovers during your active ban turn.",
        )
        self._priority_input(champions, "ban_ids", "BAN PRIORITY IDs")
        self._caption(champions, "Use the picker to add champions, or enter numeric IDs separated by commas.")
        self._divider(champions)
        self._toggle(
            champions, "auto_lock", "Lock in my selected champion",
            "Complete a pick made by the companion.",
        )
        self._divider(champions)
        self._field_row(
            champions, "Draft action delay", "Wait before making a draft choice.", "action_delay", "seconds",
        )

        loadout = self._section(
            parent, "LOADOUT", "Spells & runes",
            "Apply your preferred spells and an existing saved rune page.",
        )
        self._toggle(
            loadout, "auto_spells", "Set summoner spells",
            "Use these two spells once per champion select session.",
        )
        spell_row = tk.Frame(loadout, background=CARD)
        spell_row.pack(fill="x", pady=(9, 3))
        for column, (label, name) in enumerate((("FIRST SPELL", "spell1_id"), ("SECOND SPELL", "spell2_id"))):
            spell_row.columnconfigure(column, weight=1)
            group = tk.Frame(spell_row, background=CARD)
            group.grid(row=0, column=column, sticky="ew", padx=(0, 9) if column == 0 else (9, 0))
            tk.Label(
                group, text=label, background=CARD, foreground=SUBTLE,
                font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", pady=(0, 5))
            ttk.Combobox(
                group, textvariable=self.variables[name], values=tuple(SPELLS),
                state="readonly", style="App.TCombobox",
            ).pack(fill="x")
        self._divider(loadout)
        self._toggle(
            loadout, "auto_runes", "Select a saved rune page",
            "Choose a page already available in your League client.",
        )
        rune_row = tk.Frame(loadout, background=CARD)
        rune_row.pack(fill="x", pady=(9, 3))
        rune_row.columnconfigure(0, weight=1)
        self.rune_combo = ttk.Combobox(
            rune_row, textvariable=self.variables["rune_page_id"], style="App.TCombobox",
        )
        self.rune_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            rune_row, text="Refresh", style="Secondary.TButton",
            command=self.controller.refresh_catalog,
        ).grid(row=0, column=1, padx=(9, 0))
        self._caption(loadout, "Spell and rune availability depends on the game mode and your account.")

    def _lobby_page(self, parent: tk.Frame) -> None:
        roles = self._section(
            parent, "POSITIONS", "Your preferred roles",
            "Apply your primary and secondary roles in lobbies that offer position selection.",
        )
        self._toggle(
            roles, "auto_roles", "Set positions in the lobby",
            "Choose the roles to request before you start matchmaking.",
        )
        role_row = tk.Frame(roles, background=CARD)
        role_row.pack(fill="x", pady=(12, 3))
        for column, (label, name) in enumerate((("PRIMARY ROLE", "primary_role"), ("SECONDARY ROLE", "secondary_role"))):
            role_row.columnconfigure(column, weight=1)
            group = tk.Frame(role_row, background=CARD)
            group.grid(row=0, column=column, sticky="ew", padx=(0, 9) if column == 0 else (9, 0))
            tk.Label(
                group, text=label, background=CARD, foreground=SUBTLE,
                font=("Segoe UI", 9, "bold"),
            ).pack(anchor="w", pady=(0, 5))
            ttk.Combobox(
                group, textvariable=self.variables[name], values=ROLES,
                state="readonly", style="App.TCombobox",
            ).pack(fill="x")
        self._caption(roles, "UTILITY is the support position.")

        after = self._section(
            parent, "AFTER THE GAME", "Return to the lobby",
            "Choose what happens when the match ends.",
        )
        self._toggle(
            after, "auto_play_again", "Use Play Again",
            "Return to the lobby after a game. You still start the next queue yourself.",
        )

    def _connection_page(self, parent: tk.Frame) -> None:
        connection = self._section(
            parent, "LOCAL CONNECTION", "Find your League client",
            "Open League and sign in. The companion normally discovers the client automatically.",
        )
        tk.Label(
            connection, text="LOCKFILE PATH  ·  OPTIONAL", background=CARD,
            foreground=SUBTLE, font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(6, 6))
        path_row = tk.Frame(connection, background=CARD)
        path_row.pack(fill="x")
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(
            path_row, textvariable=self.variables["lockfile_path"], style="App.TEntry",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            path_row, text="Browse", style="Secondary.TButton",
            command=self._browse_lockfile,
        ).grid(row=0, column=1, padx=(9, 0))
        self._caption(
            connection, "Leave this blank for automatic discovery. For a custom install, choose its lockfile while League is running.",
        )
        self._divider(connection)
        self._field_row(
            connection, "Check client every", "How often to read the game state.", "poll_interval", "seconds",
        )
        self._divider(connection)
        ttk.Button(
            connection, text="Refresh champions & rune pages", style="Secondary.TButton",
            command=self.controller.refresh_catalog,
        ).pack(anchor="w")

        privacy = self._section(
            parent, "ON THIS COMPUTER", "Local by design",
            "Settings contain your preferences. The client's temporary authentication token stays in memory and is never saved.",
        )
        self._caption(privacy, "The companion must run on the same computer as League.")

    def _activity_page(self, parent: tk.Frame) -> None:
        activity = self._section(
            parent, "EVENT LOG", "Recent activity",
            "Connection events and automation actions appear here.",
            action=("Clear activity", self._clear_activity),
        )
        log_frame = tk.Frame(activity, background=FIELD, highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.activity = tk.Text(
            log_frame, height=18, wrap="word", background=FIELD, foreground=MUTED,
            insertbackground=TEXT, selectbackground=CARD_RAISED, relief="flat",
            font=("Consolas", 10), padx=14, pady=13, state="disabled",
            takefocus=False, borderwidth=0,
        )
        self.activity.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.activity.yview,
            style="App.Vertical.TScrollbar",
        )
        scroll.pack(side="right", fill="y")
        self.activity.configure(yscrollcommand=scroll.set)
        self._caption(activity, "Keep this window open while queueing. Closing it stops the companion.")

    def _clear_activity(self) -> None:
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.configure(state="disabled")

    def _toggle_pause(self) -> None:
        self.paused.set(not self.paused.get())
        self.controller.pause(self.paused.get())
        self._sync_pause_state()
        self._update_status_visual()

    def _sync_pause_state(self) -> None:
        paused = self.paused.get()
        self.pause_button.configure(
            text="Resume automation" if paused else "Pause automation",
            style="Primary.TButton" if paused else "Secondary.TButton",
        )

    def _update_status_visual(self) -> None:
        status = self.status.get().lower()
        if self.paused.get() or "paused" in status:
            color = AMBER
        elif status == "connected":
            color = GREEN
        else:
            color = SUBTLE
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 12, 12, fill=color, outline=color)

    def _mark_changed(self, *_: str) -> None:
        if not self._suppress_change:
            self.feedback.set("Unsaved changes")
            self.feedback_label.configure(foreground=AMBER)

    def _read_settings(self) -> Settings:
        values: dict[str, Any] = {}
        for field in fields(self.settings):
            name = field.name
            variable = self.variables[name]
            value = variable.get()
            original = field.default
            if isinstance(original, bool):
                values[name] = bool(value)
            elif isinstance(original, tuple):
                try:
                    values[name] = tuple(int(item.strip()) for item in str(value).split(",") if item.strip())
                except ValueError as error:
                    raise ValueError("Champion priorities must be comma-separated numeric IDs.") from error
            elif name in ("spell1_id", "spell2_id"):
                values[name] = SPELLS[str(value)] if str(value) in SPELLS else self._number(name, value, int)
            elif name == "rune_page_id" and str(value) in self.rune_pages:
                values[name] = self.rune_pages[str(value)]
            elif isinstance(original, int):
                values[name] = self._number(name, value, int)
            elif isinstance(original, float):
                values[name] = self._number(name, value, float)
            else:
                values[name] = str(value).strip()
        settings = replace(self.settings, **values)
        settings.validate()
        return settings

    @staticmethod
    def _number(name: str, value: Any, numeric_type: type) -> Any:
        try:
            return numeric_type(value)
        except (TypeError, ValueError) as error:
            label = name.replace("_", " ").capitalize()
            raise ValueError(f"{label} must be a valid number.") from error

    def _apply(self, save: bool) -> None:
        try:
            settings = self._read_settings()
            if save:
                save_settings(settings, self.config_path)
            self.controller.update_settings(settings)
        except (ValueError, OSError) as error:
            messagebox.showerror("Could not apply settings", str(error), parent=self.root)
            return
        self.settings = settings
        self.feedback.set("Applied and saved" if save else "Applied for this session")
        self.feedback_label.configure(foreground=GREEN)

    def _browse_lockfile(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose the League client lockfile", parent=self.root,
            filetypes=(("League lockfile", "lockfile"), ("All files", "*")),
        )
        if path:
            self.variables["lockfile_path"].set(path)

    def _champion_picker(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Champion priorities")
        window.geometry("610x590")
        window.minsize(500, 450)
        window.configure(background=BACKGROUND)
        window.transient(self.root)
        frame = tk.Frame(window, background=BACKGROUND)
        frame.pack(fill="both", expand=True, padx=24, pady=23)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)
        tk.Label(
            frame, text="CHAMPION SELECT", background=BACKGROUND, foreground=GOLD,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            frame, text="Choose your priorities", background=BACKGROUND,
            foreground=TEXT, font=("Segoe UI", 20, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(3, 3))
        tk.Label(
            frame, text="Search your local champion catalog and add a pick or ban.",
            background=BACKGROUND, foreground=MUTED, font=("Segoe UI", 10),
        ).grid(row=2, column=0, sticky="w", pady=(0, 16))
        query = tk.StringVar()
        search = ttk.Entry(frame, textvariable=query, style="App.TEntry")
        search.grid(row=3, column=0, sticky="ew", pady=(0, 11))
        list_frame = tk.Frame(
            frame, background=FIELD, highlightbackground=BORDER, highlightthickness=1,
        )
        list_frame.grid(row=4, column=0, sticky="nsew")
        listing = tk.Listbox(
            list_frame, background=FIELD, foreground=TEXT, selectbackground=CARD_RAISED,
            selectforeground=TEXT, relief="flat", borderwidth=0,
            font=("Segoe UI", 11), activestyle="none", exportselection=False,
            highlightthickness=0, selectmode="browse",
        )
        listing.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=9)
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=listing.yview,
            style="App.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        listing.configure(yscrollcommand=scrollbar.set)
        note = tk.StringVar(value="Choose a champion, then add it to a priority list.")
        tk.Label(
            frame, textvariable=note, background=BACKGROUND, foreground=MUTED,
            font=("Segoe UI", 10), wraplength=520, justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(12, 13))
        rows: list[dict[str, Any]] = []
        catalog_signature: tuple[tuple[int, str], ...] = ()
        after_id = ""

        def populate(*_: str) -> None:
            nonlocal rows
            needle = query.get().strip().casefold()
            rows = [
                champion for champion in self.champions
                if needle in str(champion["name"]).casefold() or needle in str(champion["id"])
            ]
            listing.delete(0, "end")
            for champion in rows:
                listing.insert("end", f"{champion['name']}  ·  {champion['id']}")
            if not self.champions:
                note.set("Open League, then refresh to load your champion catalog.")
            elif not rows:
                note.set("No matching champions.")
            else:
                note.set("Choose a champion, then add it to a priority list.")

        def append_to(name: str) -> None:
            selection = listing.curselection()
            if not selection:
                note.set("Choose a champion from the list first.")
                return
            champion = rows[selection[0]]
            current = [item.strip() for item in str(self.variables[name].get()).split(",") if item.strip()]
            champion_id = str(champion["id"])
            if champion_id not in current:
                current.append(champion_id)
                self.variables[name].set(", ".join(current))
            note.set(
                f"{champion['name']} added to {'pick' if name == 'pick_ids' else 'ban'} priorities. "
                "Apply your settings when ready."
            )

        def check_catalog() -> None:
            nonlocal catalog_signature, after_id
            if not window.winfo_exists():
                return
            signature = tuple((int(champion["id"]), str(champion["name"])) for champion in self.champions)
            if signature != catalog_signature:
                catalog_signature = signature
                populate()
            after_id = window.after(500, check_catalog)

        def close_picker(_event: tk.Event | None = None) -> None:
            if after_id:
                window.after_cancel(after_id)
            window.destroy()

        buttons = tk.Frame(frame, background=BACKGROUND)
        buttons.grid(row=6, column=0, sticky="ew")
        ttk.Button(
            buttons, text="Add to picks", style="Primary.TButton",
            command=lambda: append_to("pick_ids"),
        ).pack(side="left")
        ttk.Button(
            buttons, text="Add to bans", style="Secondary.TButton",
            command=lambda: append_to("ban_ids"),
        ).pack(side="left", padx=9)
        ttk.Button(
            buttons, text="Refresh catalog", style="Quiet.TButton",
            command=self.controller.refresh_catalog,
        ).pack(side="right")
        query.trace_add("write", populate)
        populate()
        self.controller.refresh_catalog()
        window.protocol("WM_DELETE_WINDOW", close_picker)
        window.bind("<Escape>", close_picker)
        after_id = window.after(500, check_catalog)
        search.focus_set()

    def _drain_events(self) -> None:
        if self.closed:
            return
        for _ in range(200):
            try:
                kind, payload = self.controller.events.get_nowait()
            except Empty:
                break
            if kind == "status":
                self.status.set(str(payload))
                self._update_status_visual()
            elif kind == "phase":
                phase = str(payload)
                self.phase.set(PHASE_LABELS.get(phase.casefold(), phase))
            elif kind == "log":
                self._log(str(payload))
            elif kind == "champions":
                self.champions = sorted(payload, key=lambda champion: str(champion["name"]).casefold())
            elif kind == "runes":
                self.rune_pages = {f"{page['id']} · {page['name']}": int(page["id"]) for page in payload}
                self.rune_combo.configure(values=tuple(self.rune_pages))
                current = str(self.variables["rune_page_id"].get())
                if current.isdigit():
                    selected = next(
                        (label for label, page_id in self.rune_pages.items() if str(page_id) == current),
                        None,
                    )
                    if selected:
                        self._suppress_change = True
                        try:
                            self.variables["rune_page_id"].set(selected)
                        finally:
                            self._suppress_change = False
        if self.paused.get() != self.controller.paused:
            self.paused.set(self.controller.paused)
            self._sync_pause_state()
            self._update_status_visual()
        self.root.after(150, self._drain_events)

    def _log(self, message: str) -> None:
        self.activity.configure(state="normal")
        self.activity.insert("end", message + "\n")
        lines = int(self.activity.index("end-1c").split(".")[0])
        if lines > 500:
            self.activity.delete("1.0", f"{lines - 500}.0")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _about(self) -> None:
        messagebox.showinfo(
            "About League Companion",
            "League Companion automates selected out-of-game actions through "
            "the local League Client API (LCU).\n\n"
            "The LCU API is unsupported and may change without notice. "
            "Use remains subject to Riot's terms and policies.\n\n"
            "This project is not endorsed by or affiliated with Riot Games. "
            "League of Legends and Riot Games are trademarks of Riot Games, Inc.",
            parent=self.root,
        )

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.controller.stop()
        self.root.destroy()


def run_ui(settings: Settings, config_path: Path, controller: Any) -> None:
    """Run the desktop UI and its worker until the window closes."""
    app = CompanionUI(settings, config_path, controller)
    app.root.mainloop()
