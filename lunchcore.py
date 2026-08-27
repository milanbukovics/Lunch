"""Money math and file storage for the lunch calculator. No UI code here."""

import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

TAX = Decimal("1.04712")  # Hawaii food tax. We never tip.

DATA_DIR = Path(__file__).parent / "data"
MENUS_FILE = DATA_DIR / "menus.json"


# --- money -----------------------------------------------------------------
# Everything is integer cents. Money never touches a float: the per-person
# total gets rounded UP to a whole dollar, so a float's last-bit error at a
# dollar boundary would silently overcharge someone by a full dollar.

def parse_price(text):
    """'16.50' / '16.5' / '$16.50' -> 1650 cents."""
    cleaned = text.strip().lstrip("$").strip().replace(",", "")
    if not cleaned:
        raise ValueError("Enter a price")
    try:
        cents = int((Decimal(cleaned) * 100).to_integral_value(ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError(f"'{text.strip()}' is not a price")
    if cents <= 0:
        raise ValueError("Price must be more than zero")
    return cents


def format_cents(cents):
    """1650 -> '16.50', -250 -> '-2.50'."""
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def owed_dollars(subtotal_cents):
    """What one person owes: taxed, then rounded up so change is bills only."""
    return int((Decimal(subtotal_cents) * TAX / 100).to_integral_value(ROUND_CEILING))


def taxed_cents(subtotal_cents):
    """What the restaurant actually charges -- exact cents, no dollar rounding."""
    return int((Decimal(subtotal_cents) * TAX).to_integral_value(ROUND_HALF_UP))


def subtotal_of(order):
    """Sum of the items we know a price for. Unpriced items count as nothing yet."""
    return sum(i["price_cents"] for i in order["items"] if i["price_cents"] is not None)


def unpriced_in(order):
    return sum(1 for i in order["items"] if i["price_cents"] is None)


def method_of(order):
    """'cash' or 'venmo'. Anything unset or unrecognised counts as cash, so
    older day files and hand-edits stay correct."""
    return "venmo" if order.get("method") == "venmo" else "cash"


def restaurant_method_of(day):
    """How the restaurant itself gets paid: 'cash' or 'card'. Unset means cash,
    which is what every file written before card support assumed."""
    return "card" if day.get("restaurant_method") == "card" else "cash"


def inherited_restaurant_method(day_date):
    """The method from the most recent day on or before this one that recorded
    a choice. Setting it once carries forward without rewriting older days."""
    for saved in list_saved_dates():          # newest first
        if saved <= day_date:
            stored = load_day(saved).get("restaurant_method")
            if stored in ("cash", "card"):
                return stored
    return "cash"


def group_items(day):
    """One entry per distinct item across everyone, for the call list and the
    receipt-pricing screen. Prices are typed once per item, not once per person."""
    groups = {}
    for order in day["orders"]:
        for item in order["items"]:
            key = item["desc"].casefold()
            group = groups.setdefault(key, {"desc": item["desc"], "names": [],
                                            "prices": set()})
            group["names"].append(order["name"])
            group["prices"].add(item["price_cents"])

    grouped = []
    for group in groups.values():
        prices = group.pop("prices")
        group["count"] = len(group["names"])
        # "mixed" means someone has an individual override -- don't silently pick one
        group["mixed"] = len(prices) > 1
        group["price_cents"] = prices.pop() if len(prices) == 1 else None
        grouped.append(group)
    return sorted(grouped, key=lambda g: g["desc"].casefold())


def set_price_for_desc(day, desc, price_cents):
    """Type a receipt price once; apply it to everyone who ordered that item."""
    key = desc.casefold()
    changed = 0
    for order in day["orders"]:
        for item in order["items"]:
            if item["desc"].casefold() == key:
                item["price_cents"] = price_cents
                changed += 1
    return changed


def totals(day):
    """Every number the footer shows, recomputed from the orders."""
    items_cents = sum(subtotal_of(o) for o in day["orders"])
    unpriced = sum(unpriced_in(o) for o in day["orders"])
    collected = sum(o["paid_cents"] for o in day["orders"] if o.get("paid_cents") is not None)
    change_out = 0
    unpaid = 0
    # Split by method: the restaurant is paid in cash, so Venmo money cannot be
    # spent there and refunding a Venmo payer must not drain the bills.
    cash_in = venmo_in = cash_change = venmo_change = 0
    for order in day["orders"]:
        paid = order.get("paid_cents")
        if paid is None:
            unpaid += 1
            continue
        venmo = method_of(order) == "venmo"
        if venmo:
            venmo_in += paid
        else:
            cash_in += paid
        if not unpriced_in(order):  # their change isn't knowable until priced
            change = max(0, paid - owed_dollars(subtotal_of(order)) * 100)
            change_out += change
            if venmo:
                venmo_change += change
            else:
                cash_change += change

    bill = taxed_cents(items_cents)
    cash_left = collected - change_out
    cash_on_hand = cash_in - cash_change
    venmo_held = venmo_in - venmo_change
    due = day.get("receipt_cents")
    if due is None:
        due = bill                      # before the receipt exists, go on the estimate
    restaurant_paid = day.get("restaurant_paid_cents")

    # Paying by card needs no bills, so there is no cash constraint to breach and
    # the "am I square?" figure spans both pots instead of just the cash one.
    by_card = restaurant_method_of(day) == "card"
    return {
        "people": len(day["orders"]),
        "unpaid": unpaid,
        "unpriced": unpriced,
        "items_cents": items_cents,
        "bill_cents": bill,
        "collected_cents": collected,
        "change_out_cents": change_out,
        "cash_left_cents": cash_left,
        "surplus_cents": cash_left - bill,
        # --- by method ---
        "cash_in_cents": cash_in,
        "venmo_in_cents": venmo_in,
        "cash_change_cents": cash_change,
        "venmo_change_cents": venmo_change,
        "cash_on_hand_cents": cash_on_hand,
        "venmo_held_cents": venmo_held,
        "due_cents": due,
        "has_receipt": day.get("receipt_cents") is not None,
        # --- how the restaurant gets paid ---
        "restaurant_method": "card" if by_card else "cash",
        "cash_short_cents": 0 if by_card else max(0, due - cash_on_hand),
        "card_charged_cents": due if by_card else None,
        "restaurant_paid_cents": restaurant_paid,
        "restaurant_change_cents": (None if by_card or restaurant_paid is None
                                    else restaurant_paid - due),
        "pocket_cents": (cash_on_hand + venmo_held - due) if by_card
                        else (cash_on_hand - due),
        "net_surplus_cents": cash_in + venmo_in - change_out - due,
    }


# --- storage ---------------------------------------------------------------
# Only source data (name, items, paid) is written. Subtotal/owed/change are
# always recomputed, so they can never drift out of sync with the items.

def today_str():
    return date.today().isoformat()


def new_day(day_date=None):
    return {"date": day_date or today_str(), "place": "", "orders": [],
            "receipt_cents": None, "restaurant_paid_cents": None,
            "restaurant_method": None, "locked": False, "organiser": ""}


def day_path(day_date):
    return DATA_DIR / f"lunch_{day_date}.json"


def shift_date(day_date, days):
    """'2026-08-01', -1 -> '2026-07-31'. Handles month and year rollover."""
    return (date.fromisoformat(day_date) + timedelta(days=days)).isoformat()


def parse_date(text):
    """Validate a typed YYYY-MM-DD."""
    cleaned = text.strip()
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError:
        raise ValueError(f"'{cleaned}' is not a date — use YYYY-MM-DD")


def load_day(day_date):
    """Missing file just means the day hasn't started yet."""
    path = day_path(day_date)
    if not path.exists():
        return new_day(day_date)
    day = json.loads(path.read_text(encoding="utf-8"))
    day.setdefault("date", day_date)
    day.setdefault("place", "")
    day.setdefault("orders", [])
    day.setdefault("receipt_cents", None)          # files written before Venmo
    day.setdefault("restaurant_paid_cents", None)  # tracking still load cleanly
    day.setdefault("restaurant_method", None)      # None = inherit / default cash
    day.setdefault("locked", False)                # True = public ordering closed
    day.setdefault("organiser", "")                # who picked up; "" = unrecorded
    return day


def save_day(day):
    DATA_DIR.mkdir(exist_ok=True)
    day_path(day["date"]).write_text(json.dumps(day, indent=2), encoding="utf-8")


def list_saved_dates():
    """Days that actually have a file, newest first. Ignores stray filenames."""
    if not DATA_DIR.exists():
        return []
    dates = []
    for path in DATA_DIR.glob("lunch_*.json"):
        try:
            dates.append(parse_date(path.stem[len("lunch_"):]))
        except ValueError:
            continue  # e.g. a lunch_backup.json someone dropped in
    return sorted(dates, reverse=True)


def load_menus():
    if not MENUS_FILE.exists():
        return {}
    return json.loads(MENUS_FILE.read_text(encoding="utf-8"))


def save_menus(menus):
    DATA_DIR.mkdir(exist_ok=True)
    MENUS_FILE.write_text(json.dumps(menus, indent=2), encoding="utf-8")


def learn_item(menus, place, desc, price_cents):
    """Remember an item for this place.

    The name is remembered even with no price yet -- orders are usually typed
    before the receipt exists, so waiting for a price would mean most items were
    never learned. Items typed before a place is set are kept under "" so they
    are still suggested later.
    """
    items = menus.setdefault(place or "", [])
    for item in items:
        if item["desc"].casefold() == desc.casefold():
            if price_cents is not None:  # never wipe a known price with a blank
                item["price_cents"] = price_cents
            return
    items.append({"desc": desc, "price_cents": price_cents})
    items.sort(key=lambda i: i["desc"].casefold())
