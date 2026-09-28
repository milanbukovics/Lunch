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


def paid_total(order):
    """Everything this person has handed over, or None if nothing yet.

    `paid_cents` is what they first gave. `topups` are later payments made
    the OTHER way -- $15 cash at lunch, the last $2 on Venmo that evening --
    kept apart because cash and Venmo land in different pots.
    """
    later = order.get("topups") or []
    if order.get("paid_cents") is None and not later:
        return None
    return (order.get("paid_cents") or 0) + sum(t["cents"] for t in later)


def outstanding_of(order):
    """What they still owe, in cents -- 0 when square or ahead, None while an
    item has no price, because until then nobody knows."""
    if unpriced_in(order):
        return None
    return max(0, owed_dollars(subtotal_of(order)) * 100 - (paid_total(order) or 0))


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


# --- matching the same dish written two ways --------------------------------
# On 28 Aug six people ordered a rice plate with lamb and wrote it four
# different ways, so group_items() -- which keys on the exact text -- made four
# lines of 2, 2, 1 and 1. The number six appeared nowhere, and the receipt's
# "5 Rice Lamb" had nothing to contradict. These two functions spot the near
# misses. They only ever *suggest*: the ordering page asks the person and the
# pricing screen asks the organiser, so a wrong guess costs one tap.

# Words that carry no meaning for telling two dishes apart. "no" is absent on
# purpose: "salad - no meat" should stay unlike "salad with meat".
FILLER = frozenset("with w and the for a an of my please extra side plus".split())

# Words that DO tell two dishes apart, in two groups. Naming a different
# protein blocks a match, and so does naming a different form -- Karen's pita
# plate and Patricia's rice plate are 67% the same words but different dishes
# at different prices ($15.75 and $15.00), and merging them would have applied
# one price to both. "plate" is deliberately not a form: it appears in nearly
# every dish here and would let anything match anything.
PROTEIN = frozenset(
    "chicken lamb beef veggie veggies vegetarian falafel shrimp pork tofu".split())
FORM = frozenset("rice pita wrap sandwich salad bowl gyro".split())
MARK_GROUPS = (PROTEIN, FORM)

# How alike the remaining wording has to be. Tuned on the real 28 Aug orders:
# high enough that a pita plate and a rice plate stay apart on their own words,
# low enough that "Rice plate with Lamb" reaches "doner rice plate with lamb".
SIMILAR_ENOUGH = 0.6

_SEPARATORS = str.maketrans("/&+-,()", "       ")


def item_tokens(desc):
    """'Doner Rice Plate w/Lamb & Beef' -> {doner, rice, plate, lamb, beef}."""
    words = desc.casefold().translate(_SEPARATORS).split()
    return {w for w in words if w not in FILLER}


def _conflicts(left, right):
    """True when the two name different proteins, or different forms.

    Only compares a group when BOTH sides mention it. One order saying "lamb"
    and another saying nothing about protein is not a disagreement -- it is
    just a shorter description.
    """
    for group in MARK_GROUPS:
        here, there = left & group, right & group
        if here and there and not (here & there):
            return True
    return False


def same_dish(left, right):
    """Do these two descriptions look like the same thing?

    The conflict check comes first and is the safety rule: chicken never
    matches lamb, and a wrap never matches a rice plate, however alike the
    rest of the words are. Only then does overall wording decide.
    """
    if left.casefold() == right.casefold():
        return True
    here, there = item_tokens(left), item_tokens(right)
    if not here or not there or _conflicts(here, there):
        return False
    return len(here & there) / len(here | there) >= SIMILAR_ENOUGH


def similar_items(desc, existing):
    """Descriptions in `existing` that look like `desc`, closest first.

    An exact match is not a near miss, so it never comes back: there is
    nothing to ask about when the wording already agrees.
    """
    key = desc.casefold()
    mine = item_tokens(desc)
    hits = []
    for other in existing:
        if other.casefold() == key or not same_dish(desc, other):
            continue
        theirs = item_tokens(other)
        both = mine | theirs
        hits.append((len(mine & theirs) / len(both) if both else 0, other))
    hits.sort(key=lambda pair: (-pair[0], pair[1].casefold()))
    return [other for _, other in hits]


def cluster_items(descs):
    """Group descriptions that look like one dish. Longest wording first.

    Single linkage: a description joins a cluster if it matches ANY member,
    not all of them. That matters -- "Rice plate with Lamb" is too short to
    reach "Doner Rice Plate w/Lamb & Beef for the meats" directly, but both
    reach "doner rice plate with lamb", so all three belong together.
    """
    clusters = []
    for desc in sorted(set(descs), key=lambda d: (-len(d), d.casefold())):
        for cluster in clusters:
            if any(same_dish(desc, member) for member in cluster):
                cluster.append(desc)
                break
        else:
            clusters.append([desc])
    return clusters


def surcharge_of(day):
    """The card fee this day expects, as a percent. Typed, or read off an older
    day that still carries a receipt subtotal."""
    stored = day.get("surcharge_pct")
    if stored is not None:
        return Decimal(str(stored))
    subtotal = day.get("receipt_subtotal_cents")
    if subtotal and day.get("receipt_cents") is not None:
        return (Decimal(day["receipt_cents"] - subtotal) / Decimal(subtotal) * 100)
    return None


def fee_of(subtotal_cents, pct):
    """A percentage fee on an amount, rounded to the cent on its own."""
    return int((Decimal(subtotal_cents) * Decimal(pct) / 100).to_integral_value(ROUND_HALF_UP))


def charge_with_fee(subtotal_cents, pct):
    """What a till would ring up: round the fee, then add it.

    Doner Shack's receipt goes 226.25 -> fee 6.79 -> 233.04, so the fee is
    rounded to the cent on its own and then added. Doing it as one
    multiplication would round in a different place and be a cent out.
    """
    return subtotal_cents + fee_of(subtotal_cents, pct)


def tax_included_of(day):
    """True when this restaurant's menu prices already include the 4.712% GET,
    so the till adds none on top. Doner Shack works like that; most places add
    it, so unset means on top."""
    return day.get("tax_included") is True


def till_totals(items_cents, tax_on_top, fee_pct):
    """What a till could ring up for these items, lowest first.

    Food, then 4.712% GET unless the menu prices already include it, then the
    card fee. Tills work the fee out two ways -- on the food alone, or on the
    food plus its tax -- and nothing on a receipt says which, so both come back
    and the check accepts either. With no tax added they are the same number,
    which is how Doner Shack's 226.25 -> 233.04 still comes out to the cent.
    """
    tax = taxed_cents(items_cents) - items_cents if tax_on_top else 0
    if not fee_pct:
        return [items_cents + tax]
    return sorted({items_cents + tax + fee_of(items_cents, fee_pct),
                   charge_with_fee(items_cents + tax, fee_pct)})


# Tax is rounded line by line at the till, so once it is involved a correct
# receipt can land a cent or two off a figure worked out on the whole order
# (23 Sept: $211.90 of food, $221.87 charged, $221.88 on paper). A cent per
# item covers that with room to spare and is still far below anything on a
# menu, so a missing plate or drink can never hide inside it.
MIN_SLACK_CENTS = 5


def inherited_surcharge_pct(day_date, place):
    """The fee last recorded for THIS restaurant, on or before this day.

    Matched on place, not just the most recent day: one place charges 3% for a
    card and the next charges nothing, so carrying yesterday's figure into an
    unrelated restaurant would invent a mismatch every time.
    """
    if not place:
        return None
    wanted = place.strip().casefold()
    for saved in list_saved_dates():          # newest first
        if saved <= day_date:
            day = load_day(saved)
            if (day.get("place") or "").strip().casefold() != wanted:
                continue
            pct = surcharge_of(day)
            if pct is not None:
                return float(round(pct, 3))
    return None


def group_items(day):
    """One entry per distinct item across everyone, for the call list and the
    receipt-pricing screen. Prices are typed once per item, not once per person."""
    groups = {}
    for order in day["orders"]:
        for item in order["items"]:
            key = item["desc"].casefold()
            group = groups.setdefault(key, {"desc": item["desc"], "names": [],
                                            "prices": set(), "fallbacks": {},
                                            "drink": False})
            group["names"].append(order["name"])
            group["prices"].add(item["price_cents"])
            if item.get("kind") == "drink":
                group["drink"] = True
            # Food or drink can name what to get if they're out. Two people
            # wanting a Coke with different backups still group as one
            # "2x Coke" -- the key stays the plain desc, because
            # set_price_for_desc() matches on exactly that string -- and the
            # call list lists the backups underneath.
            alt = (item.get("fallback") or "").strip()
            if alt:
                group["fallbacks"][alt] = group["fallbacks"].get(alt, 0) + 1

    grouped = []
    for group in groups.values():
        prices = group.pop("prices")
        group["count"] = len(group["names"])
        # "mixed" means someone has an individual override -- don't silently pick one
        group["mixed"] = len(prices) > 1
        group["price_cents"] = prices.pop() if len(prices) == 1 else None
        group["fallbacks"] = [{"desc": d, "count": n}
                              for d, n in sorted(group["fallbacks"].items(),
                                                 key=lambda p: (-p[1], p[0].casefold()))]
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
    collected = change_out = unpaid = 0
    # Still to come in, from people whose items are all priced. Display only:
    # it moves no money, it just says how much of a shortfall is temporary.
    outstanding = owing = 0
    # Split by method: the restaurant is paid in cash, so Venmo money cannot be
    # spent there and refunding a Venmo payer must not drain the bills.
    cash_in = venmo_in = cash_change = venmo_change = 0
    for order in day["orders"]:
        still = outstanding_of(order)
        if still:
            outstanding += still
            owing += 1
        paid = paid_total(order)
        if paid is None:
            unpaid += 1
            continue
        collected += paid
        venmo = method_of(order) == "venmo"
        first = order.get("paid_cents") or 0
        if venmo:
            venmo_in += first
        else:
            cash_in += first
        # A later payment made the other way lands in its own pot.
        for later in order.get("topups") or []:
            if later.get("method") == "venmo":
                venmo_in += later["cents"]
            else:
                cash_in += later["cents"]
        if not unpriced_in(order):  # their change isn't knowable until priced
            change = max(0, paid - owed_dollars(subtotal_of(order)) * 100)
            change_out += change
            if venmo:
                venmo_change += change
            else:
                cash_change += change

    bill = taxed_cents(items_cents)

    # Checking the order against the receipt. First a plain count of things:
    # if the receipt lists a different NUMBER of things, something was never
    # rung up -- on 28 Aug that was a plate nobody made. The subtotal figures
    # below only exist on older days, which recorded the receipt's subtotal.
    keyed_items = sum(len(o["items"]) for o in day["orders"])
    receipt_subtotal = day.get("receipt_subtotal_cents")
    receipt_items = day.get("receipt_items")
    subtotal_diff = None if receipt_subtotal is None else items_cents - receipt_subtotal
    count_diff = None if receipt_items is None else keyed_items - receipt_items
    # What the restaurant really added on top, so it stops being a guess.
    surcharge_pct = None
    if receipt_subtotal and day.get("receipt_cents") is not None:
        surcharge_pct = round(
            float(Decimal(day["receipt_cents"] - receipt_subtotal)
                  / Decimal(receipt_subtotal) * 100), 1)

    # The money check, run FORWARDS: what the till should have rung up for
    # these orders, against what it did. Food, then 4.712% GET unless this
    # place's prices already include it, then the card fee -- only when the
    # card paid, since cash pays no card fee. The first version assumed Doner
    # Shack's model everywhere (tax already in the prices), so at every place
    # that adds tax on top a correct receipt read "the fee looks like 4.7%"
    # and could never go green.
    by_card = restaurant_method_of(day) == "card"
    tax_on_top = not tax_included_of(day)
    fee_pct = surcharge_of(day) if by_card else None
    candidates = till_totals(items_cents, tax_on_top, fee_pct) if items_cents else []
    estimate = candidates[-1] if candidates else 0
    tax_part = taxed_cents(items_cents) - items_cents if tax_on_top else 0
    slack = max(MIN_SLACK_CENTS, keyed_items)
    charged = day.get("receipt_cents")
    expected_charge = charge_diff = food_diff = implied_pct = None
    charge_ok = tax_switch = False
    if candidates:
        expected_charge = (estimate if charged is None
                           else min(candidates, key=lambda c: (abs(charged - c), c)))
    if candidates and charged is not None:
        charge_diff = charged - expected_charge
        charge_ok = abs(charge_diff) <= slack
        if not charge_ok:
            # Would the other tax setting explain it? Offered, never assumed:
            # silently trying both could hide a missing item that happens to
            # cost about 4.7% of the order.
            other = till_totals(items_cents, not tax_on_top, fee_pct)
            tax_switch = any(abs(charged - c) <= slack for c in other)
        # Shown in food terms: the raw gap includes the tax and fee charged on
        # the discrepancy, and only the food figure maps onto a menu price.
        scale = (TAX if tax_on_top else Decimal(1)) * (1 + Decimal(fee_pct or 0) / 100)
        food_diff = int((Decimal(charge_diff) / scale).to_integral_value(ROUND_HALF_UP))
        # What the card fee would have to have been for these orders to be
        # right. Negative means they charged less than the food and its tax,
        # so the orders are what is wrong, not the fee.
        base = items_cents + tax_part
        implied_pct = round(float(Decimal(charged - base) / Decimal(base) * 100), 1)

    cash_left = collected - change_out
    cash_on_hand = cash_in - cash_change
    venmo_held = venmo_in - venmo_change
    due = day.get("receipt_cents")
    if due is None:
        # Before the receipt exists, go on the till estimate. With tax on top
        # and no card fee -- every day saved before this -- that is `bill`.
        due = estimate
    restaurant_paid = day.get("restaurant_paid_cents")

    # Paying by card needs no bills, so there is no cash constraint to breach and
    # the "am I square?" figure spans both pots instead of just the cash one.
    return {
        "people": len(day["orders"]),
        "unpaid": unpaid,
        "unpriced": unpriced,
        "items_cents": items_cents,
        "bill_cents": bill,
        # --- does the order match the receipt? None until the receipt is typed
        "keyed_items": keyed_items,
        "subtotal_diff_cents": subtotal_diff,
        "count_diff": count_diff,
        "surcharge_pct": surcharge_pct,
        "receipt_subtotal_cents": receipt_subtotal,
        "receipt_items": receipt_items,
        # The card fee this day expects: None when paying cash, or when unknown.
        "expected_pct": None if fee_pct is None else float(round(fee_pct, 3)),
        "tax_on_top": tax_on_top,
        "tax_part_cents": tax_part,
        "fee_part_cents": (None if expected_charge is None
                           else expected_charge - items_cents - tax_part),
        "expected_charge_cents": expected_charge,
        "estimate_cents": estimate,
        "charge_diff_cents": charge_diff,
        "charge_ok": charge_ok,
        "slack_cents": slack,
        "tax_switch": tax_switch,
        "food_diff_cents": food_diff,
        "implied_pct": implied_pct,
        "outstanding_cents": outstanding,
        "owing": owing,
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
            "restaurant_method": None, "locked": False, "organiser": "",
            "receipt_subtotal_cents": None, "receipt_items": None,
            "surcharge_pct": None, "tax_included": None}


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
    day.setdefault("receipt_subtotal_cents", None)  # older days only; not collected now
    day.setdefault("receipt_items", None)           # how many lines it billed
    day.setdefault("surcharge_pct", None)           # the place's card fee, if known
    day.setdefault("tax_included", None)            # None = inherit / tax on top
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
