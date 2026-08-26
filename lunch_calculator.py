"""Daily lunch order calculator. Run: python lunch_calculator.py"""

import calendar
import tkinter as tk
from datetime import date
from tkinter import ttk, messagebox

import lunchcore as core

BG = "#eef1f5"
SURFACE = "#ffffff"
BORDER = "#d7dee8"
TEXT = "#1f2933"
MUTED = "#6b7684"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
SELECT = "#dbeafe"
GREEN = "#15803d"
RED = "#b91c1c"
ORANGE = "#b45309"
ROW_PAID = "#e9f7ee"
ROW_SHORT = "#fdecec"
ROW_NEEDS = "#fff5e6"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SECTION = ("Segoe UI", 8, "bold")
FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_BIG = ("Segoe UI", 11, "bold")
DASH = "—"


def card(parent):
    """A white panel with a hairline border."""
    return tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER,
                    highlightthickness=1, bd=0)


def section(parent, text):
    tk.Label(parent, text=text.upper(), bg=SURFACE, fg=MUTED,
             font=FONT_SECTION).pack(anchor="w", pady=(0, 6))


def install_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=SURFACE, foreground=TEXT, font=FONT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Bold.TLabel", background=SURFACE, foreground=TEXT, font=FONT_BOLD)
    style.configure("Big.TLabel", background=SURFACE, foreground=TEXT, font=FONT_BIG)

    style.configure("TButton", background=SURFACE, foreground=TEXT, font=FONT,
                    borderwidth=1, bordercolor=BORDER, focusthickness=0,
                    padding=(11, 6), relief="flat")
    style.map("TButton",
              background=[("pressed", "#e7ecf3"), ("active", "#f4f7fa")],
              bordercolor=[("active", ACCENT)])

    style.configure("Accent.TButton", background=ACCENT, foreground="white",
                    font=FONT_BOLD, borderwidth=0, padding=(16, 6))
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
              foreground=[("disabled", "#cbd5e1")])

    for widget in ("TEntry", "TCombobox"):
        style.configure(widget, fieldbackground=SURFACE, background=SURFACE,
                        foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, arrowcolor=MUTED, padding=5,
                        insertcolor=TEXT)
        style.map(widget, bordercolor=[("focus", ACCENT)],
                  lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.font", FONT)
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, rowheight=30, font=FONT, borderwidth=0)
    style.configure("Treeview.Heading", background="#f2f5f9", foreground=MUTED,
                    font=FONT_SECTION, relief="flat", padding=(8, 8),
                    borderwidth=0)
    style.map("Treeview.Heading", background=[("active", "#e7ecf3")])
    style.map("Treeview", background=[("selected", SELECT)],
              foreground=[("selected", TEXT)])
    style.configure("Vertical.TScrollbar", background="#e3e8ef", troughcolor=SURFACE,
                    bordercolor=SURFACE, arrowcolor=MUTED, borderwidth=0)


class LunchApp:
    def __init__(self, root):
        self.root = root
        self.day = self._safe_load(core.today_str())
        self.menus = self._safe_menus()

        root.title("Lunch Calculator")
        root.geometry("1000x720")
        root.minsize(880, 560)
        root.configure(bg=BG)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)  # only the table grows

        install_theme(root)
        self._build_header()
        self._build_toolbar()
        self._build_entry()
        self._build_table()
        self._build_actions()
        self._build_footer()

        self.refresh()
        self.name_entry.focus_set()

    # --- construction ------------------------------------------------------

    def _build_header(self):
        bar = tk.Frame(self.root, bg=ACCENT)
        bar.grid(row=0, column=0, sticky="ew")
        inner = tk.Frame(bar, bg=ACCENT, padx=18, pady=12)
        inner.pack(fill="x")
        tk.Label(inner, text="Lunch Calculator", bg=ACCENT, fg="white",
                 font=FONT_TITLE).pack(side="left")
        self.date_label = tk.Label(inner, bg=ACCENT, fg="#c7dbfd", font=FONT_BOLD)
        self.date_label.pack(side="right")

    def _build_toolbar(self):
        panel = card(self.root)
        panel.grid(row=1, column=0, sticky="ew", padx=14, pady=(12, 0))
        inner = tk.Frame(panel, bg=SURFACE, padx=14, pady=11)
        inner.pack(fill="x")

        ttk.Label(inner, text="Place", style="Muted.TLabel").pack(side="left")
        self.place_box = ttk.Combobox(inner, width=26, font=FONT)
        self.place_box.pack(side="left", padx=(8, 8))
        for event in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            self.place_box.bind(event, self.on_place_change)
        ttk.Button(inner, text="Manage menu", command=self.open_menu_manager).pack(side="left")

        # packed right-to-left, so this reads backwards: [Today] [calendar] ▶ [box] ◀ "Load day"
        ttk.Button(inner, text="Today", width=6,
                   command=lambda: self.open_day(core.today_str())).pack(side="right")
        ttk.Button(inner, text="📅", width=3, command=self.open_calendar).pack(
            side="right", padx=(6, 6))
        ttk.Button(inner, text="▶", width=2, command=lambda: self.go_day(1)).pack(side="right")
        self.day_box = ttk.Combobox(inner, width=12, font=FONT, justify="center")
        self.day_box.pack(side="right", padx=2)
        self.day_box.bind("<<ComboboxSelected>>", self.on_day_typed)
        self.day_box.bind("<Return>", self.on_day_typed)
        ttk.Button(inner, text="◀", width=2, command=lambda: self.go_day(-1)).pack(side="right")
        ttk.Label(inner, text="Load day", style="Muted.TLabel").pack(side="right", padx=(0, 8))

    def _build_entry(self):
        panel = card(self.root)
        panel.grid(row=2, column=0, sticky="ew", padx=14, pady=(10, 0))
        inner = tk.Frame(panel, bg=SURFACE, padx=14, pady=12)
        inner.pack(fill="x")
        section(inner, "Add an order")

        fields = tk.Frame(inner, bg=SURFACE)
        fields.pack(fill="x")
        for column, label in enumerate(["Name", "Item", "Price (optional)"]):
            ttk.Label(fields, text=label, style="Muted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 8, 0))

        self.name_entry = ttk.Entry(fields, width=18, font=FONT)
        self.name_entry.grid(row=1, column=0, sticky="w")
        self.item_box = ttk.Combobox(fields, width=30, font=FONT)
        self.item_box.grid(row=1, column=1, padx=(8, 0))
        self.item_box.bind("<<ComboboxSelected>>", self.on_item_pick)
        self.price_entry = ttk.Entry(fields, width=12, font=FONT)
        self.price_entry.grid(row=1, column=2, padx=(8, 0))
        ttk.Button(fields, text="Add", style="Accent.TButton",
                   command=self.add_order).grid(row=1, column=3, padx=(10, 0))
        fields.columnconfigure(4, weight=1)

        for widget in (self.name_entry, self.item_box, self.price_entry):
            widget.bind("<Return>", lambda _event: self.add_order())

        self.status = ttk.Label(inner, text="Leave the price blank if you don't know it yet.",
                                style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(9, 0))

    def _build_table(self):
        panel = card(self.root)
        panel.grid(row=3, column=0, sticky="nsew", padx=14, pady=(10, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)

        columns = ("name", "items", "subtotal", "owed", "paid", "change", "status")
        self.tree = ttk.Treeview(panel, columns=columns, show="headings", selectmode="browse")
        for key, text, width, anchor in [
                ("name", "Name", 130, "w"), ("items", "Items", 300, "w"),
                ("subtotal", "Subtotal", 95, "e"), ("owed", "Owed", 75, "e"),
                ("paid", "Paid", 85, "e"), ("change", "Change", 90, "e"),
                ("status", "Status", 115, "w")]:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "items"))
        self.tree.tag_configure("paid", background=ROW_PAID)
        self.tree.tag_configure("short", background=ROW_SHORT)
        self.tree.tag_configure("needs", background=ROW_NEEDS)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-Button-1>", lambda _event: self.edit_items())

        bar = ttk.Scrollbar(panel, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns", pady=1, padx=(0, 1))
        self.tree.configure(yscrollcommand=bar.set)

    def _build_actions(self):
        panel = card(self.root)
        panel.grid(row=4, column=0, sticky="ew", padx=14, pady=(10, 0))
        inner = tk.Frame(panel, bg=SURFACE, padx=14, pady=12)
        inner.pack(fill="x")
        section(inner, "Collect money")

        line = tk.Frame(inner, bg=SURFACE)
        line.pack(fill="x")
        self.pay_label = ttk.Label(line, text="", style="Bold.TLabel", width=46)
        self.pay_label.pack(side="left")
        ttk.Label(line, text="Received  $", style="Muted.TLabel").pack(side="left")
        self.pay_entry = ttk.Entry(line, width=10, font=FONT)
        self.pay_entry.pack(side="left", padx=(2, 8))
        self.pay_entry.bind("<Return>", lambda _event: self.record_payment())
        ttk.Button(line, text="Record", style="Accent.TButton",
                   command=self.record_payment).pack(side="left")

        ttk.Button(line, text="Call summary", command=self.open_call_summary).pack(side="right")
        ttk.Button(line, text="Delete person", command=self.delete_person).pack(side="right", padx=6)
        ttk.Button(line, text="Edit items", command=self.edit_items).pack(side="right")

    def _build_footer(self):
        panel = card(self.root)
        panel.grid(row=5, column=0, sticky="ew", padx=14, pady=12)
        inner = tk.Frame(panel, bg=SURFACE, padx=14, pady=12)
        inner.pack(fill="x")
        self.footer1 = ttk.Label(inner, style="Muted.TLabel")
        self.footer1.pack(anchor="w")
        self.footer2 = ttk.Label(inner, style="Big.TLabel")
        self.footer2.pack(anchor="w", pady=(6, 0))
        self.footer3 = ttk.Label(inner, style="Card.TLabel", foreground=ORANGE)
        self.footer3.pack(anchor="w")

    # --- loading -----------------------------------------------------------

    def _safe_load(self, day_date):
        try:
            return core.load_day(day_date)
        except (ValueError, OSError) as err:
            messagebox.showerror("Could not read day file",
                                 f"{core.day_path(day_date)}\n\n{err}\n\nStarting an empty day. "
                                 "The file was left untouched.")
            return core.new_day(day_date)

    def _safe_menus(self):
        try:
            return core.load_menus()
        except (ValueError, OSError) as err:
            messagebox.showerror("Could not read menus",
                                 f"{core.MENUS_FILE}\n\n{err}\n\nStarting with no saved menus.")
            return {}

    def save(self):
        core.save_day(self.day)

    # --- helpers -----------------------------------------------------------

    def find_order(self, name):
        key = name.strip().casefold()
        for order in self.day["orders"]:
            if order["name"].casefold() == key:
                return order
        return None

    def selected_order(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self.day["orders"][int(selection[0])]

    def say(self, message, tone="info"):
        self.status.configure(text=message,
                              foreground={"error": RED, "good": GREEN}.get(tone, MUTED))

    # --- refresh -----------------------------------------------------------

    def refresh(self):
        keep = self.selected_order()
        self.date_label.configure(text=self.day["date"])
        self.root.title(f"Lunch Calculator — {self.day['date']}")

        self.place_box.configure(values=sorted(self.menus))
        if self.place_box.get() != self.day["place"]:
            self.place_box.set(self.day["place"])
        self.item_box.configure(values=[i["desc"] for i in self.menus.get(self.day["place"], [])])

        dates = core.list_saved_dates()
        if self.day["date"] not in dates:
            dates = sorted(dates + [self.day["date"]], reverse=True)
        self.day_box.configure(values=dates)
        self.day_box.set(self.day["date"])

        self.tree.delete(*self.tree.get_children())
        for index, order in enumerate(self.day["orders"]):
            self.tree.insert("", "end", iid=str(index), **self._row(order))
            if keep is order:
                self.tree.selection_set(str(index))

        self.refresh_footer()
        self.on_select()

    def _row(self, order):
        subtotal = core.subtotal_of(order)
        missing = core.unpriced_in(order)
        paid = order.get("paid_cents")
        items = ", ".join(i["desc"] + ("" if i["price_cents"] is not None else " (?)")
                          for i in order["items"]) or DASH

        if missing:
            plural = "s" if missing > 1 else ""
            return {"tags": ("needs",), "values": (
                order["name"], items,
                f"{core.format_cents(subtotal)} + ?" if subtotal else "?", "?",
                core.format_cents(paid) if paid is not None else DASH, "?",
                f"needs {missing} price{plural}")}

        owed = core.owed_dollars(subtotal)
        if paid is None:
            return {"tags": (), "values": (order["name"], items, core.format_cents(subtotal),
                                           owed, DASH, DASH, "unpaid")}
        change = paid - owed * 100
        status = f"SHORT {core.format_cents(-change)}" if change < 0 else "PAID"
        return {"tags": ("short" if change < 0 else "paid",), "values": (
            order["name"], items, core.format_cents(subtotal), owed,
            core.format_cents(paid), core.format_cents(change), status)}

    def refresh_footer(self):
        t = core.totals(self.day)
        self.footer1.configure(text=(
            f"People {t['people']}      Unpaid {t['unpaid']}      "
            f"Items ${core.format_cents(t['items_cents'])}      "
            f"Restaurant bill ${core.format_cents(t['bill_cents'])}"))

        surplus = t["surplus_cents"]
        verdict = (f"⚠  SHORT ${core.format_cents(-surplus)}" if surplus < 0
                   else f"✓  Surplus ${core.format_cents(surplus)}")
        self.footer2.configure(
            text=(f"Collected ${core.format_cents(t['collected_cents'])}      "
                  f"Change to hand back ${core.format_cents(t['change_out_cents'])}      "
                  f"Cash after change ${core.format_cents(t['cash_left_cents'])}      {verdict}"),
            foreground=RED if surplus < 0 else GREEN)

        plural = "s" if t["unpriced"] > 1 else ""
        self.footer3.configure(text=(
            f"⚠  {t['unpriced']} item{plural} still unpriced — these totals are incomplete"
            if t["unpriced"] else ""))

    # --- events ------------------------------------------------------------

    def on_place_change(self, _event=None):
        place = self.place_box.get().strip()
        if place != self.day["place"]:
            self.day["place"] = place
            self.save()
            self.refresh()

    def open_day(self, day_date):
        """The one way any day gets opened, so every route behaves identically."""
        if day_date != self.day["date"]:
            self.day = self._safe_load(day_date)
        self.refresh()
        if self.day["orders"]:
            self.say("")
        else:
            self.say(f"No orders saved for {day_date} yet")

    def go_day(self, delta):
        self.open_day(core.shift_date(self.day["date"], delta))

    def on_day_typed(self, _event=None):
        try:
            self.open_day(core.parse_date(self.day_box.get()))
        except ValueError as err:
            self.day_box.set(self.day["date"])  # put the good value back
            self.say(str(err), "error")

    def open_calendar(self):
        CalendarPopup(self.root, self)

    def on_item_pick(self, _event=None):
        desc = self.item_box.get()
        for item in self.menus.get(self.day["place"], []):
            if item["desc"] == desc:
                self.price_entry.delete(0, "end")
                if item["price_cents"] is not None:  # remembered names may have no price yet
                    self.price_entry.insert(0, core.format_cents(item["price_cents"]))
                self.price_entry.focus_set()
                self.price_entry.selection_range(0, "end")
                return

    def on_select(self, _event=None):
        order = self.selected_order()
        if order is None:
            self.pay_label.configure(text="Select a row to record payment", foreground=MUTED)
            return
        if core.unpriced_in(order):
            self.pay_label.configure(
                text=f"{order['name']} — price unknown, can still take money", foreground=ORANGE)
            return
        owed = core.owed_dollars(core.subtotal_of(order))
        paid = order.get("paid_cents")
        if paid is None:
            self.pay_label.configure(text=f"{order['name']} owes ${owed}", foreground=TEXT)
        else:
            change = paid - owed * 100
            verb = "change" if change >= 0 else "still owes"
            self.pay_label.configure(
                text=f"{order['name']}: owed ${owed}, paid ${core.format_cents(paid)}, "
                     f"{verb} ${core.format_cents(abs(change))}",
                foreground=RED if change < 0 else TEXT)

    # --- actions -----------------------------------------------------------

    def add_order(self):
        name = self.name_entry.get().strip()
        desc = self.item_box.get().strip()
        if not name:
            return self.say("Enter a name", "error")
        if not desc:
            return self.say("Enter an item", "error")

        raw = self.price_entry.get().strip()
        if raw:
            try:
                price = core.parse_price(raw)
            except ValueError as err:
                return self.say(str(err), "error")
        else:
            price = None  # fill it in later, once the restaurant tells you

        order = self.find_order(name)
        if order is None:
            order = {"name": name, "items": [], "paid_cents": None}
            self.day["orders"].append(order)
            note = f"Added {desc} for {name}"
        else:
            note = f"Added {desc} to {order['name']}'s order"
        order["items"].append({"desc": desc, "price_cents": price})
        if price is None:
            note += " — price still needed"

        core.learn_item(self.menus, self.day["place"], desc, price)
        core.save_menus(self.menus)
        self.save()

        self.name_entry.delete(0, "end")
        self.item_box.set("")
        self.price_entry.delete(0, "end")
        self.refresh()
        self.say(note, "good" if price is not None else "info")
        self.name_entry.focus_set()

    def record_payment(self):
        order = self.selected_order()
        if order is None:
            return self.say("Select a row first", "error")
        try:
            paid = core.parse_price(self.pay_entry.get())
        except ValueError as err:
            return self.say(str(err), "error")

        order["paid_cents"] = paid
        self.save()
        self.pay_entry.delete(0, "end")
        self.refresh()

        if core.unpriced_in(order):
            return self.say(f"Recorded ${core.format_cents(paid)} from {order['name']} — "
                            "change once the price is filled in")
        change = paid - core.owed_dollars(core.subtotal_of(order)) * 100
        if change < 0:
            self.say(f"{order['name']} is SHORT ${core.format_cents(-change)}", "error")
        else:
            self.say(f"Give {order['name']} ${core.format_cents(change)} change", "good")

    def edit_items(self):
        order = self.selected_order()
        if order is None:
            return self.say("Select a row first", "error")
        if not order["items"]:
            return self.say(f"{order['name']} has no items", "error")
        ItemEditor(self.root, self, order)

    def after_item_edit(self, note):
        self.save()
        self.refresh()
        self.say(note, "good")

    def delete_person(self):
        order = self.selected_order()
        if order is None:
            return self.say("Select a row first", "error")
        if order.get("paid_cents") is not None and not messagebox.askyesno(
                "Delete person",
                f"{order['name']} already paid ${core.format_cents(order['paid_cents'])}.\n\n"
                "Delete them anyway?"):
            return
        self.day["orders"].remove(order)
        self.save()
        self.refresh()
        self.say(f"Deleted {order['name']}", "good")

    def call_summary_text(self):
        counts = {}
        for order in self.day["orders"]:
            for item in order["items"]:
                key = item["desc"].casefold()
                desc, count = counts.get(key, (item["desc"], 0))
                counts[key] = (desc, count + 1)

        t = core.totals(self.day)
        lines = [f"{self.day['place'] or 'Lunch'} — {self.day['date']}", ""]
        lines += [f"{count}x {desc}" for desc, count in sorted(counts.values())] or ["(no items yet)"]
        lines += ["", f"Subtotal:  ${core.format_cents(t['items_cents'])}",
                  f"With tax:  ${core.format_cents(t['bill_cents'])}"]
        if t["unpriced"]:
            lines.append(f"({t['unpriced']} item(s) unpriced — total is incomplete)")
        return "\n".join(lines)

    def open_call_summary(self):
        text = self.call_summary_text()
        window = tk.Toplevel(self.root, bg=SURFACE)
        window.title("Call Summary")
        window.geometry("400x460")
        window.transient(self.root)

        box = tk.Text(window, wrap="word", padx=16, pady=14, font=("Consolas", 11),
                      bg=SURFACE, fg=TEXT, relief="flat", highlightthickness=0)
        box.pack(fill="both", expand=True)
        box.insert("1.0", text)
        box.configure(state="disabled")

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            copied.configure(text="Copied to clipboard")

        bar = tk.Frame(window, bg=SURFACE, padx=14, pady=12)
        bar.pack(fill="x")
        ttk.Button(bar, text="Copy", style="Accent.TButton", command=copy).pack(side="left")
        copied = ttk.Label(bar, text="", style="Card.TLabel", foreground=GREEN)
        copied.pack(side="left", padx=10)
        ttk.Button(bar, text="Close", command=window.destroy).pack(side="right")

    def open_menu_manager(self):
        if not self.day["place"]:
            return self.say("Set a place first — menus are saved per place", "error")
        MenuManager(self.root, self)


class CalendarPopup(tk.Toplevel):
    """Month grid for jumping to any day. Days with saved orders are marked."""

    WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

    def __init__(self, parent, app):
        super().__init__(parent, bg=SURFACE)
        self.app = app
        self.title("Pick a day")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())

        opened = date.fromisoformat(app.day["date"])
        self.year, self.month = opened.year, opened.month
        self.saved = set(core.list_saved_dates())

        wrap = tk.Frame(self, bg=SURFACE, padx=14, pady=12)
        wrap.pack()

        head = tk.Frame(wrap, bg=SURFACE)
        head.pack(fill="x", pady=(0, 8))
        ttk.Button(head, text="◀", width=2,
                   command=lambda: self.step_month(-1)).pack(side="left")
        ttk.Button(head, text="▶", width=2,
                   command=lambda: self.step_month(1)).pack(side="right")
        self.heading = ttk.Label(head, style="Bold.TLabel", anchor="center")
        self.heading.pack(side="left", expand=True, fill="x")

        self.grid_frame = tk.Frame(wrap, bg=SURFACE)
        self.grid_frame.pack()
        ttk.Label(wrap, text="●  has orders", style="Muted.TLabel").pack(
            anchor="w", pady=(10, 0))
        self.draw()

    def step_month(self, delta):
        shifted = self.month + delta - 1
        self.year += shifted // 12
        self.month = shifted % 12 + 1
        self.draw()

    def draw(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.heading.configure(text=f"{calendar.month_name[self.month]} {self.year}")

        for column, name in enumerate(self.WEEKDAYS):
            tk.Label(self.grid_frame, text=name, bg=SURFACE, fg=MUTED,
                     font=FONT_SECTION).grid(row=0, column=column, pady=(0, 3))

        today = core.today_str()
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self.year, self.month)
        for row, week in enumerate(weeks, start=1):
            for column, day_number in enumerate(week):
                if day_number == 0:
                    continue  # padding from the previous/next month
                iso = f"{self.year:04d}-{self.month:02d}-{day_number:02d}"
                self._cell(day_number, iso, today).grid(row=row, column=column, padx=1, pady=1)

    def _cell(self, day_number, iso, today):
        has_orders = iso in self.saved
        is_open = iso == self.app.day["date"]
        button = tk.Button(self.grid_frame, width=3, relief="flat", bd=0,
                           font=("Segoe UI", 9), cursor="hand2",
                           text=f"{day_number}\n{'●' if has_orders else ' '}",
                           command=lambda: self.choose(iso))
        if is_open:
            button.configure(bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
                             activeforeground="white")
        else:
            button.configure(bg=SURFACE, activebackground=SELECT,
                             fg=ACCENT if has_orders else TEXT,
                             font=("Segoe UI", 9, "bold" if has_orders else "normal"))
        if iso == today and not is_open:
            button.configure(highlightbackground=ACCENT, highlightthickness=1)
        return button

    def choose(self, iso):
        self.destroy()  # release the grab before the main window refreshes
        self.app.open_day(iso)


class ItemEditor(tk.Toplevel):
    """Fix a price the restaurant charged differently, or drop an item."""

    def __init__(self, parent, app, order):
        super().__init__(parent, bg=SURFACE)
        self.app, self.order = app, order
        self.title(f"Items — {order['name']}")
        self.geometry("430x330")
        self.transient(parent)
        self.grab_set()

        wrap = tk.Frame(self, bg=SURFACE, padx=14, pady=14)
        wrap.pack(fill="both", expand=True)
        section(wrap, f"{order['name']}'s items")

        self.tree = ttk.Treeview(wrap, columns=("desc", "price"), show="headings",
                                 selectmode="browse", height=6)
        self.tree.heading("desc", text="Item")
        self.tree.heading("price", text="Price")
        self.tree.column("desc", width=250)
        self.tree.column("price", width=90, anchor="e")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        form = tk.Frame(wrap, bg=SURFACE)
        form.pack(fill="x", pady=(12, 0))
        self.desc_entry = ttk.Entry(form, width=24, font=FONT)
        self.desc_entry.pack(side="left")
        self.price_entry = ttk.Entry(form, width=9, font=FONT)
        self.price_entry.pack(side="left", padx=7)
        self.price_entry.bind("<Return>", lambda _event: self.save_item())
        ttk.Button(form, text="Save", style="Accent.TButton",
                   command=self.save_item).pack(side="left")
        ttk.Button(form, text="Remove", command=self.remove_item).pack(side="left", padx=7)

        self.status = ttk.Label(wrap, text="Blank price means still unknown.",
                                style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(10, 0))
        ttk.Button(wrap, text="Done", command=self.destroy).pack(anchor="e", pady=(10, 0))
        self.reload()

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.order["items"]):
            price = item["price_cents"]
            self.tree.insert("", "end", iid=str(index), values=(
                item["desc"], core.format_cents(price) if price is not None else "?"))
        if self.order["items"]:
            self.tree.selection_set("0")

    def on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.order["items"][int(selection[0])]
        self.desc_entry.delete(0, "end")
        self.desc_entry.insert(0, item["desc"])
        self.price_entry.delete(0, "end")
        if item["price_cents"] is not None:
            self.price_entry.insert(0, core.format_cents(item["price_cents"]))

    def _selected_index(self):
        selection = self.tree.selection()
        if not selection:
            self.status.configure(text="Select an item first", foreground=RED)
            return None
        return int(selection[0])

    def save_item(self):
        index = self._selected_index()
        if index is None:
            return
        desc = self.desc_entry.get().strip()
        if not desc:
            self.status.configure(text="Enter an item name", foreground=RED)
            return
        raw = self.price_entry.get().strip()
        price = None
        if raw:
            try:
                price = core.parse_price(raw)
            except ValueError as err:
                self.status.configure(text=str(err), foreground=RED)
                return

        item = self.order["items"][index]
        item["desc"], item["price_cents"] = desc, price
        core.learn_item(self.app.menus, self.app.day["place"], desc, price)
        core.save_menus(self.app.menus)
        self.status.configure(text=f"Saved {desc}", foreground=GREEN)
        self.reload()
        self.app.after_item_edit(f"Updated {desc} for {self.order['name']}")

    def remove_item(self):
        index = self._selected_index()
        if index is None:
            return
        removed = self.order["items"].pop(index)
        self.status.configure(text=f"Removed {removed['desc']}", foreground=GREEN)
        self.reload()
        self.app.after_item_edit(f"Removed {removed['desc']} from {self.order['name']}")


class MenuManager(tk.Toplevel):
    """Fix typos and prices in a place's learned menu."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=SURFACE)
        self.app = app
        self.place = app.day["place"]
        self.title(f"Menu — {self.place}")
        self.geometry("450x400")
        self.transient(parent)

        wrap = tk.Frame(self, bg=SURFACE, padx=14, pady=14)
        wrap.pack(fill="both", expand=True)
        section(wrap, f"saved menu — {self.place}")

        self.tree = ttk.Treeview(wrap, columns=("desc", "price"), show="headings",
                                 selectmode="browse")
        self.tree.heading("desc", text="Item")
        self.tree.heading("price", text="Price")
        self.tree.column("desc", width=270)
        self.tree.column("price", width=90, anchor="e")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        form = tk.Frame(wrap, bg=SURFACE)
        form.pack(fill="x", pady=(12, 0))
        self.desc_entry = ttk.Entry(form, width=24, font=FONT)
        self.desc_entry.pack(side="left")
        self.price_entry = ttk.Entry(form, width=9, font=FONT)
        self.price_entry.pack(side="left", padx=7)
        ttk.Button(form, text="Save", style="Accent.TButton",
                   command=self.save_item).pack(side="left")
        ttk.Button(form, text="Remove", command=self.remove_item).pack(side="left", padx=7)

        self.status = ttk.Label(wrap, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(10, 0))
        ttk.Button(wrap, text="Close", command=self.destroy).pack(anchor="e", pady=(10, 0))
        self.reload()

    def items(self):
        return self.app.menus.setdefault(self.place, [])

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.items()):
            price = item["price_cents"]
            self.tree.insert("", "end", iid=str(index), values=(
                item["desc"], core.format_cents(price) if price is not None else "?"))
        self.app.refresh()

    def on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.items()[int(selection[0])]
        self.desc_entry.delete(0, "end")
        self.desc_entry.insert(0, item["desc"])
        self.price_entry.delete(0, "end")
        if item["price_cents"] is not None:
            self.price_entry.insert(0, core.format_cents(item["price_cents"]))

    def save_item(self):
        selection = self.tree.selection()
        if not selection:
            self.status.configure(text="Select an item first", foreground=RED)
            return
        desc = self.desc_entry.get().strip()
        if not desc:
            self.status.configure(text="Enter an item name", foreground=RED)
            return
        try:
            price = core.parse_price(self.price_entry.get())
        except ValueError as err:
            self.status.configure(text=str(err), foreground=RED)
            return
        item = self.items()[int(selection[0])]
        item["desc"], item["price_cents"] = desc, price
        self.items().sort(key=lambda i: i["desc"].casefold())
        core.save_menus(self.app.menus)
        self.status.configure(text=f"Saved {desc}", foreground=GREEN)
        self.reload()

    def remove_item(self):
        selection = self.tree.selection()
        if not selection:
            self.status.configure(text="Select an item first", foreground=RED)
            return
        removed = self.items().pop(int(selection[0]))
        core.save_menus(self.app.menus)
        self.status.configure(text=f"Removed {removed['desc']}", foreground=GREEN)
        self.reload()


if __name__ == "__main__":
    root = tk.Tk()
    LunchApp(root)
    root.mainloop()
