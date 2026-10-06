"""
Site Harvester - the Find tab (the window half; the work is in find_engine.py).

Describe what you are after, press Search, and the tab fills with what the
web search turned up that matches - each row scored, with the reason it
matched. Tick the rows you want and press "Download ticked". Nothing is
downloaded until then.

The tab is added by site_harvester.App._add_find_tab(), which is wrapped so
that a failure here leaves the Harvest tab working exactly as before.
"""

import os
import queue
import re
import subprocess
import threading
import webbrowser
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import find_engine as fe

# Plain characters on purpose: the ballot-box glyphs are missing from enough
# fonts that a ticked row can end up looking exactly like an unticked one.
CHECK_ON, CHECK_OFF = "[x]", "[  ]"

TYPE_BOXES = ["Documents", "Images", "Videos", "Audio", "Archives"]
MAX_CHECK_OPTIONS = ["20", "40", "80", "150"]
MIN_SCORE_OPTIONS = ["0", "30", "50", "70", "90"]
AUTO_OPTIONS = ["Off", "60", "70", "80", "90"]

COLUMNS = [
    # id,      heading,   width, anchor, stretch
    ("tick",   CHECK_OFF,  40,  "center", False),
    ("score",  "Score",    54,  "e",      False),
    ("type",   "Type",     58,  "w",      False),
    ("size",   "Size",     74,  "e",      False),
    ("title",  "Name",     250, "w",      True),
    ("site",   "Site",     140, "w",      False),
    ("status", "Status",   150, "w",      False),
]


class FindTab(ttk.Frame):
    def __init__(self, parent, app, sh):
        super().__init__(parent)
        self.app = app
        self.sh = sh                      # the site_harvester module
        self.settings = dict(fe.DEFAULTS)
        saved = sh._load_ui_settings().get("find")
        if isinstance(saved, dict):
            self.settings.update(saved)

        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.mode = None                  # None | "search" | "download"
        self.items = {}                   # id -> item (every result found)
        self.order = []                   # ids in the order they arrived
        self.ticked = set()
        self.sort_col, self.sort_desc = "score", True
        self.last_description = ""
        self.pal = None
        self._settings_win = None
        self._log_path = os.path.join(sh.APP_DIR, "find-last-log.txt")

        self._build()
        self.after(120, self._drain)

    # ------------------------------------------------------------------ UI
    def _build(self):
        s = self.settings
        px = {"padx": 12}

        ttk.Label(self, text="What are you looking for?  "
                             "(\"quotes\" = exact phrase, -word = must not contain)"
                  ).pack(anchor="w", padx=12, pady=(8, 4))
        self.desc_text = tk.Text(self, height=3, wrap="word", undo=True)
        self.desc_text.pack(fill="x", **px)
        self.desc_text.bind("<Control-Return>", lambda _e: (self._search(), "break")[1])
        if self.sh.IS_MAC:
            # Mac only: elsewhere Tk reads "Command" as Mod1, which on
            # Windows is NumLock - a plain Return would start a search.
            self.desc_text.bind("<Command-Return>",
                                lambda _e: (self._search(), "break")[1])

        # --- what kind of thing -------------------------------------------
        types = ttk.Frame(self)
        types.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(types, text="Find:").pack(side="left", padx=(0, 8))
        self.type_vars = {}
        for cat in TYPE_BOXES:
            v = tk.BooleanVar(value=cat in (s.get("types") or []))
            self.type_vars[cat] = v
            ttk.Checkbutton(types, text=cat, variable=v).pack(side="left", padx=(0, 10))
        self.pages_var = tk.BooleanVar(value=bool(s.get("pages")))
        ttk.Checkbutton(types, text="Web pages", variable=self.pages_var
                        ).pack(side="left", padx=(0, 10))

        # --- filters --------------------------------------------------------
        filt = ttk.Frame(self)
        filt.pack(fill="x", padx=12, pady=(8, 0))
        filt.columnconfigure(0, weight=3)
        filt.columnconfigure(1, weight=3)
        filt.columnconfigure(2, weight=2)
        ttk.Label(filt, text="Only these sites (optional)").grid(row=0, column=0, sticky="w")
        ttk.Label(filt, text="Never these sites (optional)").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(filt, text="Other file endings").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.include_var = tk.StringVar(value=s.get("include", ""))
        self.exclude_var = tk.StringVar(value=s.get("exclude", ""))
        self.exts_var = tk.StringVar(value=s.get("extra_exts", ""))
        ttk.Entry(filt, textvariable=self.include_var).grid(row=1, column=0, sticky="ew")
        ttk.Entry(filt, textvariable=self.exclude_var).grid(row=1, column=1, sticky="ew", padx=(10, 0))
        ttk.Entry(filt, textvariable=self.exts_var).grid(row=1, column=2, sticky="ew", padx=(10, 0))

        # --- options --------------------------------------------------------
        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=12, pady=(8, 0))
        self._combos = []

        def combo(label, var, values, width):
            box = ttk.Frame(opts)
            box.pack(side="left", padx=(0, 14))
            ttk.Label(box, text=label).pack(anchor="w")
            cb = ttk.Combobox(box, textvariable=var, values=values,
                              state="readonly", width=width)
            cb.pack(anchor="w")
            self._combos.append(cb)
            return cb

        self.max_check_var = tk.StringVar(value=self._one_of(
            s.get("max_check"), MAX_CHECK_OPTIONS, "40"))
        combo("Results to check", self.max_check_var, MAX_CHECK_OPTIONS, 6)
        self.min_score_var = tk.StringVar(value=self._one_of(
            s.get("min_score"), MIN_SCORE_OPTIONS, "0"))
        cb = combo("Show score from", self.min_score_var, MIN_SCORE_OPTIONS, 6)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._rebuild_tree())
        self.auto_var = tk.StringVar(value=self._one_of(
            s.get("auto"), AUTO_OPTIONS, "Off"))
        combo("Auto-download from score", self.auto_var, AUTO_OPTIONS, 6)
        self.inside_var = tk.BooleanVar(value=bool(s.get("inside", True)))
        ttk.Checkbutton(opts, text="Look inside result pages for files",
                        variable=self.inside_var).pack(side="left", pady=(14, 0))

        # --- save to --------------------------------------------------------
        ttk.Label(self, text="Save to folder (a Find/<search> folder is made inside)"
                  ).pack(anchor="w", padx=12, pady=(8, 2))
        out = ttk.Frame(self)
        out.pack(fill="x", **px)
        # The same variable as the Harvest tab's folder box, so File >
        # Default save folder and "Choose…" keep the two in step.
        ttk.Entry(out, textvariable=self.app.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(out, text="Choose…", command=self.app._choose_folder
                   ).pack(side="left", padx=(8, 0))

        # --- buttons --------------------------------------------------------
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(10, 6))
        self.search_btn = ttk.Button(btns, text="Search", command=self._search,
                                     style="Accent.TButton")
        self.search_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.dl_btn = ttk.Button(btns, text="Download ticked", command=self._download,
                                 style="Accent.TButton", state="disabled")
        self.dl_btn.pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Settings…", command=self._open_settings).pack(side="right")
        ttk.Button(btns, text="Open folder", command=self._open_folder
                   ).pack(side="right", padx=(0, 8))

        row2 = ttk.Frame(self)
        row2.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Button(row2, text="Tick all shown", command=lambda: self._tick_all(True)
                   ).pack(side="left")
        ttk.Button(row2, text="Untick all", command=lambda: self._tick_all(False)
                   ).pack(side="left", padx=(8, 0))
        ttk.Button(row2, text="Open in browser", command=self._open_selected
                   ).pack(side="left", padx=(8, 0))
        ttk.Button(row2, text="Send to Harvest tab", command=self._send_to_harvest
                   ).pack(side="left", padx=(8, 0))
        self.count_var = tk.StringVar(value="")
        ttk.Label(row2, textvariable=self.count_var, style="Dim.TLabel").pack(side="right")

        # --- bottom (packed first so the results list gets what is left) ---
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x")
        log_frame = ttk.Frame(bottom)
        log_frame.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=4, wrap="word", state="disabled")
        self.log_text.pack(side="left", fill="x", expand=True)
        sc = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sc.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=sc.set)
        self.status_var = tk.StringVar(value="Idle. Describe what you want and press Search.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="bottom", anchor="w", padx=12)
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(side="bottom", fill="x", padx=12, pady=(4, 4))
        # The selected row in full (the columns cut long names and
        # addresses short). A fixed-height box, so the list above does not
        # jump about as rows are clicked, and the address can be copied.
        self.detail = tk.Text(bottom, height=4, wrap="char", state="disabled")
        self.detail.pack(side="bottom", fill="x", padx=12, pady=(4, 0))
        self.detail.bind("<1>", lambda _e: self.detail.focus_set())

        # --- results --------------------------------------------------------
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=12)
        self.tree = ttk.Treeview(tree_frame, columns=[c[0] for c in COLUMNS],
                                 show="headings", selectmode="extended", height=6)
        for cid, head, width, anchor, stretch in COLUMNS:
            self.tree.heading(cid, text=head,
                              command=lambda c=cid: self._heading_clicked(c))
            self.tree.column(cid, width=width, minwidth=30, anchor=anchor,
                             stretch=stretch)
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", self._tree_double)
        self.tree.bind("<space>", self._tree_space)
        self.tree.bind("<Return>", lambda _e: self._open_selected())
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.tree.bind("<Button-3>", self._tree_menu)
        if self.sh.IS_MAC:
            # A Mac's right button is Button-2, and Control-click is the
            # one-button way to ask for the same menu. Bound only there:
            # on Windows Control-click has to keep adding to the selection.
            self.tree.bind("<Button-2>", self._tree_menu)
            self.tree.bind("<Control-Button-1>", self._tree_menu)
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Open in browser", command=self._open_selected)
        self.menu.add_command(label="Open the page it was found on",
                              command=lambda: self._open_selected(source=True))
        self.menu.add_command(label="Copy address", command=self._copy_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Tick selected", command=lambda: self._tick_selected(True))
        self.menu.add_command(label="Untick selected", command=lambda: self._tick_selected(False))
        self.menu.add_separator()
        self.menu.add_command(label="Send to Harvest tab", command=self._send_to_harvest)

    @staticmethod
    def _one_of(value, options, default):
        value = str(value) if value is not None else ""
        return value if value in options else default

    def _set_detail(self, text):
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")

    # --------------------------------------------------------------- theme
    def apply_theme(self, pal, mono):
        """Called by the app whenever light/dark changes (and once at start)."""
        self.pal = pal
        self.desc_text.configure(
            background=pal["field"], foreground=pal["field_text"],
            insertbackground=pal["accent"], selectbackground=pal["sel"],
            selectforeground=pal["text"], relief="flat", highlightthickness=1,
            highlightbackground=pal["field_border"], highlightcolor=pal["accent"])
        self.log_text.configure(
            background=pal["panel"], foreground=pal["text"],
            insertbackground=pal["accent"], selectbackground=pal["sel"],
            selectforeground=pal["text"], relief="flat", highlightthickness=1,
            highlightbackground=pal["border"], highlightcolor=pal["accent"],
            font=(mono, 10))
        self.detail.configure(
            background=pal["bg"], foreground=pal["dim"],
            selectbackground=pal["sel"], selectforeground=pal["text"],
            relief="flat", highlightthickness=0, borderwidth=0)
        self.tree.tag_configure("hi", foreground=pal["good"])
        self.tree.tag_configure("mid", foreground=pal["text"])
        self.tree.tag_configure("lo", foreground=pal["dim"])
        self.tree.tag_configure("saved", foreground=pal["good"])
        self.tree.tag_configure("bad", foreground=pal["bad"])
        try:
            self.menu.configure(bg=pal["panel"], fg=pal["text"],
                                activebackground=pal["sel"],
                                activeforeground=pal["text"])
        except tk.TclError:
            pass
        for cb in self._combos:
            try:
                lb = self.tk.call("ttk::combobox::PopdownWindow", cb) + ".f.l"
                self.tk.call(lb, "configure",
                             "-background", pal["field"],
                             "-foreground", pal["field_text"],
                             "-selectbackground", pal["accent"],
                             "-selectforeground", pal["accent_text"])
            except Exception:
                pass
        win = self._settings_win
        if win is not None and win.winfo_exists():
            win.configure(bg=pal["bg"])

    # ------------------------------------------------------------ settings
    def _remember(self):
        """Fold what is on screen into the saved settings."""
        s = self.settings
        s["types"] = [c for c, v in self.type_vars.items() if v.get()]
        s["pages"] = bool(self.pages_var.get())
        s["inside"] = bool(self.inside_var.get())
        s["include"] = self.include_var.get().strip()
        s["exclude"] = self.exclude_var.get().strip()
        s["extra_exts"] = self.exts_var.get().strip()
        s["max_check"] = self.max_check_var.get()
        s["min_score"] = self.min_score_var.get()
        s["auto"] = self.auto_var.get()
        data = self.sh._load_ui_settings()
        data["find"] = s
        self.sh._save_ui_settings(data)

    def _open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        self._settings_win = SettingsWindow(self)

    # ---------------------------------------------------------------- log
    LOG_MAX_LINES = 1500

    def _log(self, msg):
        # Also kept in a file beside the app (find-last-log.txt, one search's
        # worth) so that a search which went wrong can be looked at afterwards.
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except OSError:
            pass
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        try:
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > self.LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{lines - self.LOG_MAX_LINES}.0")
        except Exception:
            pass
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # ------------------------------------------------------------- search
    def _busy(self, mode):
        self.mode = mode
        running = mode is not None
        self.search_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled", text="Stop")
        self._refresh_counts()
        if not running:
            self.progress.configure(value=0)

    def _search(self):
        if self.mode is not None:
            return
        description = " ".join(self.desc_text.get("1.0", "end").split())
        if not description:
            messagebox.showwarning("Site Harvester",
                                   "Describe what you are looking for first.")
            return
        cats = [c for c, v in self.type_vars.items() if v.get()]
        spec = fe.Spec(description)
        if (not cats and not self.pages_var.get()
                and not fe.parse_exts(self.exts_var.get()) and not spec.hinted_exts):
            messagebox.showwarning(
                "Site Harvester",
                "Tick at least one thing to find (Documents, Images, … or Web pages).")
            return
        self._remember()

        self.items.clear()
        self.order.clear()
        self.ticked.clear()
        self.tree.delete(*self.tree.get_children())
        self._set_detail("")
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        try:
            import datetime
            with open(self._log_path, "w", encoding="utf-8") as fh:
                fh.write("Find - " + datetime.datetime.now().isoformat(
                    timespec="seconds") + "\n")
        except OSError:
            pass
        self.last_description = description

        job = {
            "description": description, "categories": cats,
            "extra_exts": self.exts_var.get(),
            "include": self.include_var.get(), "exclude": self.exclude_var.get(),
            "want_pages": self.pages_var.get(), "inside": self.inside_var.get(),
            "max_check": self.max_check_var.get(),
        }
        self.stop_event = threading.Event()
        engine = fe.FindEngine(self.sh, self.settings, self.events, self.stop_event)
        self.worker = threading.Thread(target=engine.run_search, args=(job,), daemon=True)
        self._busy("search")
        self.status_var.set("Searching…")
        self.worker.start()

    def _stop(self):
        if self.mode is None:
            return
        self.stop_event.set()
        self.stop_btn.config(text="Stopping…", state="disabled")
        self._log("Stopping — nothing new will start; a file part-way through "
                  "is removed.")

    def shutdown(self):
        """The window is closing. A download that is part-way through is
        given a moment to notice and remove its partial file - the worker is
        a daemon thread, so without the wait it would simply be cut off."""
        self.stop_event.set()
        try:
            self._remember()
        except Exception:
            pass
        worker = self.worker
        if worker is not None and worker.is_alive():
            worker.join(3.0)

    # ------------------------------------------------------------- events
    def _drain(self):
        try:
            for _ in range(300):
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    done, total = payload
                    self.progress.configure(maximum=max(1, total), value=done)
                elif kind == "result":
                    self.items[payload["id"]] = payload
                    self.order.append(payload["id"])
                    self._insert_row(payload)
                    self._refresh_counts()
                elif kind == "update":
                    self._update_row(payload)
                elif kind == "search_done":
                    self._search_done(payload)
                elif kind == "download_done":
                    self._download_done(payload)
        except queue.Empty:
            pass
        except Exception as e:                 # never let the poll loop die
            try:
                self._log(f"(display error: {type(e).__name__}: {e})")
            except Exception:
                pass
        self.after(120, self._drain)

    def _search_done(self, summary):
        self._busy(None)
        self._rebuild_tree()
        n = len(self.items)
        if summary.get("error"):
            self.status_var.set(f"Search failed — {summary['error']}")
        elif summary.get("stopped"):
            self.status_var.set(f"Stopped — {n} match(es) listed.")
        elif n:
            self.status_var.set(
                f"Found {n} match(es) from {summary.get('checked', 0)} result(s) "
                "checked. Tick what you want, then Download ticked.")
        elif summary.get("rate_limited"):
            self.status_var.set("Every free search source turned us away. Wait a "
                                "few minutes, or add a Brave API key in Settings.")
        else:
            self.status_var.set("Nothing matched. Try different words, more "
                                "types, or a higher 'Results to check'.")
        self._log("=" * 48)
        self._log(f"Search finished — {n} match(es).")

        auto = self.auto_var.get()
        if auto != "Off" and n and not summary.get("stopped"):
            floor = int(auto)
            picked = [i for i, it in self.items.items()
                      if it["score"] >= floor and self._downloadable(it)]
            if picked:
                self.ticked.update(picked)
                self._rebuild_tree()
                self._log(f"Auto-download: {len(picked)} result(s) scored "
                          f"{floor} or more.")
                self._download()

    def _download_done(self, summary):
        # A saved row has nothing left to download, so it gives up its tick -
        # otherwise the button would still count it and then do nothing.
        self.ticked -= {i for i in self.ticked
                        if self.items.get(i, {}).get("status") == "saved"}
        self._busy(None)
        self._rebuild_tree()
        msg = (f"{summary.get('saved', 0)} saved, {summary.get('failed', 0)} "
               f"failed, {summary.get('skipped', 0)} skipped")
        self.status_var.set(("Stopped — " if summary.get("stopped") else "Done — ")
                            + msg + f".  Folder: {summary.get('dir', '')}")
        self._log("=" * 48)
        self._log("Downloads finished — " + msg + ".")
        self.last_job_dir = summary.get("dir")

    # --------------------------------------------------------------- rows
    def _values(self, it):
        if it["kind"] == "page":
            kind = "page"
        else:
            kind = (it["ext"] or it["category"]).upper()[:6]
        size = it.get("dims") or fe.human_size(it.get("size"))
        return (CHECK_ON if it["id"] in self.ticked else CHECK_OFF,
                it["score"], kind, size, it["title"], it["site"], it["status"])

    def _tag(self, it):
        status = it.get("status", "")
        if status == "saved":
            return "saved"
        if status.startswith(("failed", "could not", "server said")):
            return "bad"
        if it["score"] >= 70:
            return "hi"
        if it["score"] >= 40:
            return "mid"
        return "lo"

    def _shown(self, it):
        try:
            return it["score"] >= int(self.min_score_var.get())
        except ValueError:
            return True

    def _sort_key(self, it):
        col = self.sort_col
        if col == "score":
            return it["score"]
        if col == "size":
            return it.get("size") or 0
        if col == "tick":
            return 1 if it["id"] in self.ticked else 0
        if col == "type":
            return (it["ext"] or it["kind"]).lower()
        return str(it.get(col, "")).lower()

    def _insert_row(self, it):
        if not self._shown(it):
            return
        # While a search is running rows arrive in bursts; keep them ordered
        # by score as they land so the best are always on top.
        index = "end"
        if self.sort_col == "score" and self.sort_desc:
            kids = self.tree.get_children()
            lo, hi = 0, len(kids)
            while lo < hi:
                mid = (lo + hi) // 2
                if self.items[kids[mid]]["score"] >= it["score"]:
                    lo = mid + 1
                else:
                    hi = mid
            index = lo
        self.tree.insert("", index, iid=it["id"], values=self._values(it),
                         tags=(self._tag(it),))

    def _update_row(self, it):
        if it["id"] not in self.items:
            return
        if self.tree.exists(it["id"]):
            self.tree.item(it["id"], values=self._values(it), tags=(self._tag(it),))
            if self.tree.selection() == (it["id"],):
                self._show_detail()
        self._refresh_counts()

    def _rebuild_tree(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        rows = [self.items[i] for i in self.order if self._shown(self.items[i])]
        rows.sort(key=self._sort_key, reverse=self.sort_desc)
        for it in rows:
            self.tree.insert("", "end", iid=it["id"], values=self._values(it),
                             tags=(self._tag(it),))
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        self._refresh_counts()

    def _refresh_counts(self):
        shown = len(self.tree.get_children())
        total = len(self.items)
        ticked = len(self.ticked)
        text = f"{shown} shown"
        if shown != total:
            text += f" of {total}"
        text += f" · {ticked} ticked"
        self.count_var.set(text if total else "")
        self.tree.heading("tick", text=CHECK_ON if ticked else CHECK_OFF)
        can = self.mode is None and ticked > 0
        self.dl_btn.config(state="normal" if can else "disabled",
                           text=f"Download ticked ({ticked})" if ticked else "Download ticked")

    def _heading_clicked(self, col):
        if col == "tick":
            self._tick_all(not self.ticked)
            return
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, col in ("score", "size")
        self._rebuild_tree()

    # ----------------------------------------------------------- ticking
    @staticmethod
    def _downloadable(it):
        return it.get("status") != "saved"

    def _set_tick(self, iid, on):
        if on:
            self.ticked.add(iid)
        else:
            self.ticked.discard(iid)
        if self.tree.exists(iid):
            self.tree.set(iid, "tick", CHECK_ON if on else CHECK_OFF)

    def _tick_all(self, on):
        for iid in self.tree.get_children():
            self._set_tick(iid, on and self._downloadable(self.items[iid]))
        if not on:
            self.ticked.clear()
        self._refresh_counts()

    def _tick_selected(self, on):
        for iid in self.tree.selection():
            self._set_tick(iid, on)
        self._refresh_counts()

    def _tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#1":
            return None
        iid = self.tree.identify_row(event.y)
        if iid:
            self._set_tick(iid, iid not in self.ticked)
            self._refresh_counts()
            return "break"
        return None

    def _tree_space(self, _event):
        sel = self.tree.selection()
        if sel:
            on = not all(i in self.ticked for i in sel)
            for iid in sel:
                self._set_tick(iid, on)
            self._refresh_counts()
        return "break"

    def _tree_double(self, event):
        if (self.tree.identify_region(event.x, event.y) == "cell"
                and self.tree.identify_column(event.x) != "#1"):
            self._open_selected()

    def _tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return None
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()
        return "break"

    def _show_detail(self, _event=None):
        sel = self.tree.selection()
        if len(sel) != 1 or sel[0] not in self.items:
            self._set_detail(f"{len(sel)} rows selected." if len(sel) > 1 else "")
            return
        it = self.items[sel[0]]
        lines = [it["url"], "Why: " + it["reason"]]
        if it.get("dest"):
            lines.append("Saved as: " + it["dest"])
        elif it.get("source"):
            lines.append("Found on: " + it["source"])
        self._set_detail("\n".join(lines))

    # ----------------------------------------------------------- actions
    def _selected_items(self):
        return [self.items[i] for i in self.tree.selection() if i in self.items]

    def _open_selected(self, source=False):
        chosen = self._selected_items()
        if len(chosen) > 8 and not messagebox.askyesno(
                "Site Harvester", f"Open {len(chosen)} browser tabs?"):
            return
        for it in chosen:
            url = (it.get("source") or it["url"]) if source else it["url"]
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def _copy_selected(self):
        urls = [it["url"] for it in self._selected_items()]
        if urls:
            self.clipboard_clear()
            self.clipboard_append("\n".join(urls))

    def _send_to_harvest(self):
        """Hand pages over to the Harvest tab, which can crawl them properly
        (every file on them, the PDF, the offline mirror, embedded video).
        Uses the ticked rows, or the selected ones if nothing is ticked. A
        file's row sends the page it was found on."""
        chosen = [self.items[i] for i in self.order if i in self.ticked]
        if not chosen:
            chosen = self._selected_items()
        urls = []
        for it in chosen:
            url = it["url"] if it["kind"] == "page" else (it.get("source") or "")
            if url and url not in urls:
                urls.append(url)
        if not urls:
            messagebox.showinfo(
                "Site Harvester",
                "Tick or select some rows first. Web pages are sent as they "
                "are; a file sends the page it was found on (files that came "
                "straight from the search have no page to send).")
            return
        box = self.app.url_text
        existing = [l.strip() for l in box.get("1.0", "end").splitlines()
                    if l.strip() and l.strip().lower() not in ("https://", "http://")]
        merged = existing + [u for u in urls if u not in existing]
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(merged))
        try:
            self.app.tabs.select(0)
        except Exception:
            pass

    def _job_dir(self):
        base = self.app.out_var.get().strip()
        slug = self.sh.safe_dirname(fe.Spec(self.last_description).plain_text[:60])
        # safe_dirname() answers "site" for text with no plain letters in it,
        # and Windows refuses a folder called con, nul, aux...
        if slug == "site":
            slug = "search"
        if slug.split(".", 1)[0].lower() in self.sh.WINDOWS_RESERVED:
            slug = "_" + slug
        return os.path.join(base, "Find", slug)

    def _download(self):
        if self.mode is not None:
            return
        chosen = [self.items[i] for i in self.order
                  if i in self.ticked and self._downloadable(self.items[i])]
        if not chosen:
            return
        base = self.app.out_var.get().strip()
        if not base:
            messagebox.showwarning("Site Harvester", "Choose a folder to save into.")
            return
        job_dir = self._job_dir()
        try:
            os.makedirs(self.sh._fs(job_dir), exist_ok=True)
        except Exception as e:
            messagebox.showerror("Site Harvester", f"Can't create the folder:\n{e}")
            return
        self.stop_event = threading.Event()
        engine = fe.FindEngine(self.sh, self.settings, self.events, self.stop_event)
        self.worker = threading.Thread(
            target=engine.run_download,
            args=(chosen, job_dir, self.last_description), daemon=True)
        self._busy("download")
        self.status_var.set(f"Downloading {len(chosen)} item(s)…")
        self.worker.start()

    def _open_folder(self):
        path = getattr(self, "last_job_dir", None)
        if not path or not os.path.isdir(path):
            path = os.path.join(self.app.out_var.get().strip(), "Find")
        if not os.path.isdir(path):
            path = self.app.out_var.get().strip()
        if not os.path.isdir(path):
            messagebox.showinfo("Site Harvester", "That folder doesn't exist yet.")
            return
        try:
            if self.sh.IS_WINDOWS:
                os.startfile(path)                                  # noqa: S606
            elif self.sh.IS_MAC:
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:
            messagebox.showinfo("Site Harvester",
                                f"Could not open the folder ({e}).\n\n{path}")


# --------------------------------------------------------------------------- #
# Settings window
# --------------------------------------------------------------------------- #
PROVIDER_LABELS = {
    "auto": "Free search (no key needed)",
    "brave": "Brave Search API (needs a key)",
}
AI_LABELS = {
    "off": "Off - match on the words in the description",
    "anthropic": "Anthropic API (needs a key, small cost per search)",
    "ollama": "Ollama on this computer (free, needs Ollama running)",
}


class SettingsWindow(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.title("Find - settings")
        self.resizable(False, False)
        if tab.pal:
            self.configure(bg=tab.pal["bg"])
        s = tab.settings
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=16, pady=14)
        body.columnconfigure(1, weight=1)
        self.vars = {}
        row = [0]

        def heading(text):
            ttk.Label(body, text=text, style="Field.TLabel").grid(
                row=row[0], column=0, columnspan=2, sticky="w",
                pady=(10 if row[0] else 0, 4))
            row[0] += 1

        def note(text):
            ttk.Label(body, text=text, style="Dim.TLabel", wraplength=470,
                      justify="left").grid(row=row[0], column=0, columnspan=2,
                                           sticky="w", pady=(0, 4))
            row[0] += 1

        def entry(key, label, show=None, width=44):
            ttk.Label(body, text=label).grid(row=row[0], column=0, sticky="w",
                                             padx=(0, 10), pady=2)
            v = tk.StringVar(value=str(s.get(key, "")))
            self.vars[key] = v
            ttk.Entry(body, textvariable=v, width=width, show=show or "").grid(
                row=row[0], column=1, sticky="ew", pady=2)
            row[0] += 1

        def choice(key, label, labels):
            ttk.Label(body, text=label).grid(row=row[0], column=0, sticky="w",
                                             padx=(0, 10), pady=2)
            v = tk.StringVar(value=labels.get(s.get(key), list(labels.values())[0]))
            self.vars[key] = v
            cb = ttk.Combobox(body, textvariable=v, values=list(labels.values()),
                              state="readonly", width=46)
            cb.grid(row=row[0], column=1, sticky="ew", pady=2)
            tab._combos.append(cb)
            row[0] += 1

        heading("Where search results come from")
        ver = fe.ddgs_version()
        note("Free search tries several sources in turn (ddgs "
             + (f"{ver}" if ver else "- not installed, skipped")
             + ", Bing, Brave Search, the headless browser, DuckDuckGo) and "
             "uses the first that answers. They push back if hammered; a "
             "Brave key is steadier.")
        choice("provider", "Search source", PROVIDER_LABELS)
        entry("brave_key", "Brave API key", show="•")
        entry("region", "Region (e.g. uk-en, us-en)", width=12)

        heading("Understanding the description")
        note("With AI on, a model writes the searches and judges each result "
             "against your description, so loose wording works. Off is free "
             "and needs nothing.")
        choice("ai", "AI help", AI_LABELS)
        entry("anthropic_key", "Anthropic API key", show="•")
        entry("anthropic_model", "Anthropic model")
        entry("ollama_url", "Ollama address")
        entry("ollama_model", "Ollama model")

        heading("Politeness and limits")
        entry("delay", "Seconds between requests to one site", width=8)
        entry("workers", "Sites checked at the same time (1-12)", width=8)
        entry("max_file_mb", "Skip any file bigger than (MB)", width=8)
        entry("max_total_mb", "Stop a download run at (MB)", width=8)
        note("Keys are stored as plain text in site_harvester_ui.json beside "
             "the app (that file is never committed to git).")

        self.test_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.test_var, style="Dim.TLabel",
                  wraplength=470, justify="left").grid(
            row=row[0], column=0, columnspan=2, sticky="w", pady=(6, 0))
        row[0] += 1

        btns = ttk.Frame(body)
        btns.grid(row=row[0], column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))
        self.test_btn = ttk.Button(btns, text="Test search", command=self._test)
        self.test_btn.pack(side="left")

        self.transient(tab.winfo_toplevel())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._test_q = queue.Queue()

    def _collect(self):
        out = dict(self.tab.settings)
        for key, var in self.vars.items():
            value = var.get().strip()
            if key == "provider":
                value = next((k for k, v in PROVIDER_LABELS.items() if v == value), "auto")
            elif key == "ai":
                value = next((k for k, v in AI_LABELS.items() if v == value), "off")
            elif key in ("delay", "max_file_mb", "max_total_mb"):
                try:
                    value = max(0.0, float(value))
                except ValueError:
                    value = fe.DEFAULTS[key]
            elif key == "workers":
                try:
                    value = max(1, min(12, int(float(value))))
                except ValueError:
                    value = fe.DEFAULTS[key]
            elif key == "region":
                value = value.lower() if re.fullmatch(r"[A-Za-z]{2}-[A-Za-z]{2}", value) else "uk-en"
            out[key] = value
        return out

    def _save(self):
        self.tab.settings = self._collect()
        self.tab._remember()
        self.destroy()

    def _test(self):
        """One real search with what is in the boxes, so a bad key or a
        rate-limited source shows up here rather than half-way through."""
        settings = self._collect()
        self.test_btn.config(state="disabled")
        self.test_var.set("Testing…")

        def work():
            import queue as _q
            eng = fe.FindEngine(self.tab.sh, settings, _q.Queue(), threading.Event())
            try:
                hits, via = eng._search_once("volkswagen polo owner manual", 5)
                msg = (f"Search works - {len(hits)} result(s) came back from {via}."
                       if hits else "The search ran but returned nothing.")
            except Exception as e:
                msg = f"Search failed: {e}"
            finally:
                eng.close()
            tried = []
            while True:
                try:
                    kind, text = eng.events.get_nowait()
                except _q.Empty:
                    break
                if kind == "log":
                    tried.append(text.strip())
            if tried:
                msg += "  Tried first: " + " | ".join(tried)[:400]
            if settings.get("ai") != "off":
                ai = fe.AIClient(settings)
                if not ai.enabled:
                    msg += "  AI: no key entered."
                else:
                    try:
                        ai.complete("Reply with the single word OK.", "Say OK.", 10)
                        msg += f"  AI works ({ai.label})."
                    except Exception as e:
                        msg += f"  AI failed: {e}"
            self._test_q.put(msg)

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll_test)

    def _poll_test(self):
        if not self.winfo_exists():
            return
        try:
            msg = self._test_q.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_test)
            return
        self.test_var.set(msg)
        self.test_btn.config(state="normal")
