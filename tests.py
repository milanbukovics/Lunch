"""The whole test suite. Run it with:  python tests.py

Everything runs against temporary directories and a throwaway SQLite database,
so your real orders in data/ are never opened. Nothing here needs the network.
"""

import http.cookiejar
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

TMP = Path(tempfile.mkdtemp(prefix="lunch-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ["ADMIN_PASSWORD"] = "pw"
os.environ["SECRET_KEY"] = "test-only"

import lunchcore as core

# Point the file backend at the temp dir BEFORE anything can touch the real one.
core.DATA_DIR = TMP / "files"
core.MENUS_FILE = core.DATA_DIR / "menus.json"

import store                                    # noqa: E402
import app as webapp                            # noqa: E402

FAILURES = []
_section = ""


def section(title):
    global _section
    _section = title
    print(f"\n{title}")


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(f"{_section} :: {label}")


def money(cents):
    return core.format_cents(cents)


# --- the money engine ------------------------------------------------------
# These are the rules the whole thing exists for. If any of them move, someone
# gets handed the wrong change.

def test_money():
    section("MONEY")
    check("tax rate is Hawaii food tax", str(core.TAX) == "1.04712", str(core.TAX))

    # The worked example this project started from.
    check("$16.50 -> owes $18", core.owed_dollars(1650) == 18,
          f"got {core.owed_dollars(1650)}")
    check("  ...so $20 leaves $2 change", 2000 - core.owed_dollars(1650) * 100 == 200)

    # Rounding is UP, always, because change is handed over in bills.
    check("exactly a dollar after tax stays put", core.owed_dollars(0) == 0)
    check("1 cent still rounds up to $1", core.owed_dollars(1) == 1)
    check("$19.50 -> $21 (20.42 rounds up)", core.owed_dollars(1950) == 21,
          f"got {core.owed_dollars(1950)}")

    # The restaurant is charged exact cents; only people are rounded up.
    check("$16.50 bill is 1728 cents", core.taxed_cents(1650) == 1728,
          f"got {core.taxed_cents(1650)}")

    check("parse '16.50'", core.parse_price("16.50") == 1650)
    check("parse '$16.5'", core.parse_price("$16.5") == 1650)
    check("parse rejects nonsense", _raises(core.parse_price, "abc"))
    check("parse rejects zero", _raises(core.parse_price, "0"))
    check("format 1650", money(1650) == "16.50", money(1650))
    check("format negative", money(-250) == "-2.50", money(-250))


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def _day(orders, **extra):
    day = core.new_day("2026-01-01")
    day["orders"] = orders
    day.update(extra)
    return day


def _order(name, price_cents, paid_cents=None, method="cash"):
    return {"name": name, "items": [{"desc": "Thing", "price_cents": price_cents}],
            "paid_cents": paid_cents, "method": method}


def test_totals():
    section("TOTALS")
    day = _day([_order("A", 1650, 2000), _order("B", 1000, 1000)])
    t = core.totals(day)
    check("items summed", t["items_cents"] == 2650, str(t["items_cents"]))
    check("collected", t["collected_cents"] == 3000)
    # A owes 18 (gave 20 -> 2 back), B owes 11 (gave 10 -> nothing back, short)
    check("change out is $2", t["change_out_cents"] == 200, money(t["change_out_cents"]))
    check("short payer gets no negative change", t["change_out_cents"] >= 0)

    section("CASH vs VENMO ARE KEPT APART")
    # The restaurant is paid in cash, so Venmo money cannot be spent there.
    day = _day([_order("Cash", 1650, 2000, "cash"),
                _order("Venmo", 1650, 2000, "venmo")])
    t = core.totals(day)
    check("cash in", t["cash_in_cents"] == 2000, money(t["cash_in_cents"]))
    check("venmo in", t["venmo_in_cents"] == 2000, money(t["venmo_in_cents"]))
    check("cash on hand excludes venmo", t["cash_on_hand_cents"] == 1800,
          money(t["cash_on_hand_cents"]))
    check("venmo refund does not drain the bills",
          t["venmo_held_cents"] == 1800, money(t["venmo_held_cents"]))

    section("PAYING THE RESTAURANT BY CARD")
    day = _day([_order("V", 10000, 11000, "venmo")], receipt_cents=10471,
               restaurant_method="card")
    t = core.totals(day)
    check("card mode has no cash shortfall", t["cash_short_cents"] == 0)
    check("card pocket spans both pots",
          t["pocket_cents"] == t["cash_on_hand_cents"] + t["venmo_held_cents"]
          - t["due_cents"], money(t["pocket_cents"]))

    day["restaurant_method"] = "cash"
    t = core.totals(day)
    check("cash mode DOES report the shortfall", t["cash_short_cents"] > 0,
          money(t["cash_short_cents"]))

    section("UNPRICED ITEMS")
    day = _day([_order("A", None, 2000)])
    t = core.totals(day)
    check("unpriced counted", t["unpriced"] == 1)
    check("no change guessed before pricing", t["change_out_cents"] == 0)


def test_audited_day():
    """The real Aug 20 figures, kept as a fixture so this never depends on the
    live data folder. These are the numbers that were reconciled by hand."""
    section("THE AUDITED DAY (2026-08-20)")
    # The real orders from that day, copied in as a fixture so the check never
    # depends on the live data folder. Receipt was $126.30 on the paper slip.
    real = [("Craig", 1750, 2000), ("Ron", 1950, 2200), ("Renee", 1750, 2000),
            ("Deborah", 1750, 2000), ("Patty", 1650, 1800),
            ("Erwin", 1950, 2000), ("Bruce", 1950, 2200)]
    orders = [_order(n, p, g) for n, p, g in real]
    day = _day(orders, receipt_cents=12630)
    t = core.totals(day)
    check("items total $127.50", t["items_cents"] == 12750, money(t["items_cents"]))
    check("collected $142", t["collected_cents"] == 14200, money(t["collected_cents"]))
    check("change out $5", t["change_out_cents"] == 500, money(t["change_out_cents"]))
    check("cash on hand $137", t["cash_left_cents"] == 13700, money(t["cash_left_cents"]))
    check("receipt drives the total, not the estimate", t["due_cents"] == 12630)
    check("$10.70 left over", t["cash_left_cents"] - t["due_cents"] == 1070,
          money(t["cash_left_cents"] - t["due_cents"]))


def test_grouping_and_learning():
    section("GROUPING AND REMEMBERED ITEMS")
    day = _day([])
    for name in ("A", "B", "C"):
        day["orders"].append({"name": name, "paid_cents": None,
                              "items": [{"desc": "Plate lunch", "price_cents": None}]})
    groups = core.group_items(day)
    check("three of the same item is one row", len(groups) == 1)
    check("counted as 3", groups[0]["count"] == 3)
    changed = core.set_price_for_desc(day, "plate lunch", 1650)
    check("price typed once updates everyone", changed == 3, str(changed))
    check("case-insensitive match", core.subtotal_of(day["orders"][0]) == 1650)

    menus = {}
    core.learn_item(menus, "Place", "Seafood", None)
    check("an item with no price is still remembered",
          menus["Place"][0]["desc"] == "Seafood")
    core.learn_item(menus, "Place", "seafood", 1200)
    check("a later price fills it in", menus["Place"][0]["price_cents"] == 1200)
    core.learn_item(menus, "Place", "Seafood", None)
    check("a blank never wipes a known price",
          menus["Place"][0]["price_cents"] == 1200)


# The real wordings from 28 Aug 2026. Six people ordered one dish and typed it
# four ways, so it showed as four lines of 2, 2, 1 and 1; the count never
# reached six and the receipt's "5 Rice Lamb" had nothing to contradict.
LAMB_WORDINGS = [
    "Doner Rice Plate w/Lamb & Beef for the meats",
    "doner rice plate with lamb",
    "Doner Rice Plate with lamb/beef",
    "Rice plate with Lamb",
]


def test_same_dish():
    section("SPOTTING ONE DISH WRITTEN SEVERAL WAYS")
    clusters = core.cluster_items(LAMB_WORDINGS)
    check("all four lamb wordings become one line", len(clusters) == 1,
          str([len(c) for c in clusters]))

    # The safety rule. Everything here was on the same receipt on the same day,
    # and merging any of these pairs would apply one price to two dishes.
    must_differ = [
        ("Doner Rice Plate with chicken - veggies and yogurt garlic sauce",
         "doner rice plate with lamb", "chicken is not lamb"),
        ("Doner Pita Plate - chicken + Yogurt Garlic sauce",
         "Doner Rice Plate with chicken - veggies and yogurt garlic sauce",
         "a pita plate is not a rice plate ($15.75 vs $15.00)"),
        ("Veggie wrap", "Doner Wrap with lamb and beef", "veggie is not lamb"),
        ("Chicken schwarma sandwich", "Lamb/Beef Doner Sandwich",
         "chicken sandwich is not a lamb one"),
        ("Salad - no meat", "Veggie wrap", "a salad is not a wrap"),
        ("Doner Wrap with lamb and beef", "doner rice plate with lamb",
         "a wrap is not a rice plate"),
    ]
    for left, right, why in must_differ:
        check(f"kept apart: {why}", not core.same_dish(left, right))

    check("a short wording still reaches its family",
          core.same_dish("Rice plate with Lamb", "doner rice plate with lamb"))
    check("identical text always matches",
          core.same_dish("Veggie wrap", "veggie WRAP"))
    check("an exact match is never offered as a near miss",
          core.similar_items("Veggie wrap", ["veggie WRAP"]) == [])
    check("no recognised protein or form means no guessing",
          not core.same_dish("Special of the day", "Special number two"))


def test_merge_keeps_money():
    section("MERGING WORDINGS NEVER MOVES MONEY")
    day = _day([])
    for i, desc in enumerate(LAMB_WORDINGS):
        day["orders"].append({"name": f"P{i}", "paid_cents": None, "method": "cash",
                              "items": [{"desc": desc, "price_cents": 1500}]})
    before = core.totals(day)
    check("starts as four separate lines", len(core.group_items(day)) == 4)

    keys = {d.casefold() for d in LAMB_WORDINGS}
    for order in day["orders"]:
        for item in order["items"]:
            if item["desc"].casefold() in keys:
                item["desc"] = LAMB_WORDINGS[0]

    groups = core.group_items(day)
    check("becomes one line", len(groups) == 1, str(len(groups)))
    check("carrying the full count", groups[0]["count"] == 4)
    after = core.totals(day)
    check("every total identical", all(before[k] == after[k] for k in before),
          str([k for k in before if before[k] != after[k]]))


def test_receipt_check():
    section("CHECKING THE ORDER AGAINST THE RECEIPT")
    day = _day([_order("A", 1500), _order("B", 1500), _order("C", 1500)])

    t = core.totals(day)
    check("nothing entered means no comparison, not a false alarm",
          t["charge_diff_cents"] is None and t["count_diff"] is None)
    check("counts what was keyed in", t["keyed_items"] == 3, str(t["keyed_items"]))

    # Doner Shack's shape: prices already include tax, 3% on the card.
    day["tax_included"] = True
    day["restaurant_method"] = "card"
    # The 28 Aug shape: one more item keyed than the restaurant ever billed.
    day["surcharge_pct"] = 3
    day["receipt_items"] = 2
    day["receipt_cents"] = core.charge_with_fee(3000, 3)      # they billed two
    t = core.totals(day)
    check("counts the item that was never billed", t["count_diff"] == 1,
          str(t["count_diff"]))
    check("prices it, in food terms", t["food_diff_cents"] == -1500,
          money(t["food_diff_cents"]))
    check("implied fee goes negative, so it is the ORDER not the fee",
          t["implied_pct"] < 0, str(t["implied_pct"]))

    # The state the old check could never reach on a real receipt.
    day["receipt_items"] = 3
    day["receipt_cents"] = core.charge_with_fee(4500, 3)
    t = core.totals(day)
    check("a clean order reads exactly zero",
          t["charge_diff_cents"] == 0 and t["count_diff"] == 0,
          f"{t['charge_diff_cents']} / {t['count_diff']}")

    # Right things, wrong money: the fee moved, not the order.
    day["receipt_cents"] = core.charge_with_fee(4500, 6)
    t = core.totals(day)
    check("count still right", t["count_diff"] == 0)
    check("but the money is flagged", t["charge_diff_cents"] != 0)
    check("and the fee actually charged is named", t["implied_pct"] == 6.0,
          str(t["implied_pct"]))

    check("the old estimate is untouched", t["bill_cents"] == core.taxed_cents(4500))

    section("MOST PLACES ADD TAX ON TOP, THEN THE CARD FEE")
    # 23 Sept: $211.90 of food, $221.87 paid in cash. Tax on top, no card fee,
    # and the till's per-line rounding lands a cent under the whole-order sum.
    # The first version of this check could never go green on a day like it.
    sept = _day([_order("A", 21190)], receipt_cents=22187, receipt_items=1)
    t = core.totals(sept)
    check("23 Sept matches, a cent inside the slack",
          t["charge_ok"] and t["charge_diff_cents"] == -1, str(t["charge_diff_cents"]))
    check("  ...and names the tax it added", t["tax_part_cents"] == 998,
          str(t["tax_part_cents"]))
    # 26 Aug from the saved days: $105.45 -> $110.42, on the card, no fee.
    aug = _day([_order("A", 10545)], receipt_cents=11042, restaurant_method="card")
    check("26 Aug matches exactly", core.totals(aug)["charge_diff_cents"] == 0)

    # A $8 side the restaurant never rang up must never hide in the slack.
    short = _day([_order("A", 20390), _order("B", 800)],
                 receipt_cents=core.taxed_cents(20390), receipt_items=1)
    t = core.totals(short)
    check("a missing $8 side is caught", not t["charge_ok"] and t["count_diff"] == 1,
          str(t["charge_diff_cents"]))

    # A card fee is a card fee: paying cash, the remembered 3% must not apply.
    cash = _day([_order("A", 10000)], surcharge_pct=3, restaurant_method="cash",
                receipt_cents=core.taxed_cents(10000))
    t = core.totals(cash)
    check("a cash day ignores the card fee", t["charge_ok"] and t["expected_pct"] is None,
          str(t["expected_charge_cents"]))

    # On a card, both ways tills charge the fee are accepted.
    items, tax = 10000, core.taxed_cents(10000) - 10000
    for charged, how in ((items + tax + core.fee_of(items, 3), "fee on the food"),
                         (core.charge_with_fee(items + tax, 3), "fee on food and tax")):
        card = _day([_order("A", items)], surcharge_pct=3, restaurant_method="card",
                    receipt_cents=charged)
        check(f"card fee accepted: {how}", core.totals(card)["charge_diff_cents"] == 0)

    # Doner Shack before anyone says its prices include tax: the check offers
    # the switch rather than trying both on its own.
    doner = _day([_order("A", 22625)], surcharge_pct=3, restaurant_method="card",
                 receipt_cents=23304)
    t = core.totals(doner)
    check("the wrong tax setting is not green", not t["charge_ok"])
    check("  ...but says the other setting would match", t["tax_switch"])
    doner["tax_included"] = True
    t = core.totals(doner)
    check("  ...and matches to the cent once set", t["charge_ok"] and t["charge_diff_cents"] == 0)

    section("THE ESTIMATE BEFORE THE RECEIPT")
    plain = _day([_order("A", 1650)])
    t = core.totals(plain)
    check("tax on top, no fee: exactly the old bill",
          t["estimate_cents"] == t["bill_cents"] == t["due_cents"])
    doner = _day([_order("A", 22625)], surcharge_pct=3, restaurant_method="card",
                 tax_included=True)
    t = core.totals(doner)
    check("tax in the prices, 3% card: what Doner Shack charges",
          t["due_cents"] == 23304, money(t["due_cents"]))
    check("the slack is a cent an item, never under 5c",
          core.totals(_day([_order("A", 100)] * 12))["slack_cents"] == 12
          and core.totals(plain)["slack_cents"] == 5)


def test_forward_comparison_is_exact():
    """A correct order must land on exactly zero at every rate.

    The temptation is to divide the charged total back out to recover a
    subtotal, but that carries up to a cent of rounding, and a check that
    reports "$0.01 over" on a right order is one that gets ignored -- which has
    already happened twice here. Comparing forwards, both sides round the same
    way and the answer is exact.
    """
    section("THE MONEY CHECK NEVER INVENTS A CENT")
    drift = []
    for pct in (0, 3, 3.5, 8.25, 10):
        for subtotal in range(1, 40000, 11):
            charged = core.charge_with_fee(subtotal, pct)
            day = _day([_order("A", subtotal)], receipt_cents=charged, surcharge_pct=pct,
                       restaurant_method="card", tax_included=True)
            if core.totals(day)["charge_diff_cents"] != 0:
                drift.append((pct, subtotal))
    check(f"exact across {5 * len(range(1, 40000, 11)):,} order/rate combinations",
          not drift, str(drift[:4]))
    # And with tax on top, for every way a till can put the two together.
    drift = []
    for pct in (0, 3, 3.5):
        for subtotal in range(1, 40000, 37):
            for charged in core.till_totals(subtotal, True, pct):
                day = _day([_order("A", subtotal)], receipt_cents=charged,
                           surcharge_pct=pct, restaurant_method="card")
                if core.totals(day)["charge_diff_cents"] != 0:
                    drift.append((pct, subtotal, charged))
    check("exact with tax on top too, both fee orders", not drift, str(drift[:4]))
    # The real receipt: 226.25 -> fee 6.79 -> 233.04.
    check("reproduces the Doner Shack receipt to the cent",
          core.charge_with_fee(22625, 3) == 23304,
          str(core.charge_with_fee(22625, 3)))


def test_surcharge_follows_the_restaurant():
    section("THE SURCHARGE IS REMEMBERED PER RESTAURANT")
    check("a typed figure wins", core.surcharge_of({"surcharge_pct": 3}) == 3)
    # Older days carry a receipt subtotal instead; derive from it so nothing
    # saved before this change loses its meaning.
    derived = core.surcharge_of({"receipt_subtotal_cents": 22625,
                                 "receipt_cents": 23304})
    check("an older day derives it from its stored subtotal",
          derived is not None and round(float(derived), 1) == 3.0, str(derived))
    check("and a day with neither has none", core.surcharge_of({}) is None)


def test_storage_parity():
    section("FILE AND DATABASE AGREE")
    day = _day([_order("A", 1650, 2000), _order("B", 1000, None, "venmo")])
    day["date"] = "2026-02-02"
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    core.save_day(day)
    store.save_day(day)
    from_file = core.totals(core.load_day("2026-02-02"))
    from_db = core.totals(store.load_day("2026-02-02"))
    check("totals identical across backends", from_file == from_db)

    old = {"date": "2020-01-01", "place": "", "orders": []}   # pre-everything
    store.save_day(old)
    loaded = store.load_day("2020-01-01")
    for field in ("receipt_cents", "restaurant_method", "locked", "organiser"):
        check(f"old row gains '{field}'", field in loaded)


# --- the web app -----------------------------------------------------------

class Server:
    def __init__(self):
        from werkzeug.serving import make_server
        self.srv = make_server("127.0.0.1", 8439, webapp.app, threaded=True)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        time.sleep(0.8)
        self.base = "http://127.0.0.1:8439"

    def stop(self):
        self.srv.shutdown()

    def anon(self):
        return urllib.request.build_opener()

    def user(self):
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path, op=None):
        try:
            with (op or self.anon()).open(self.base + path) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(self, path, op=None, **body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with (op or self.anon()).open(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}

    def upload(self, op, place, filename, blob, mime="image/jpeg"):
        """Minimal multipart/form-data — avoids a test-only dependency."""
        boundary = "----lunchtest"
        parts = []
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="place"\r\n\r\n{place}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="file"; filename="{filename}"\r\n'
                     f'Content-Type: {mime}\r\n\r\n'.encode())
        parts.append(blob)
        parts.append(f'\r\n--{boundary}--\r\n'.encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            self.base + "/api/menu-file", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with (op or self.anon()).open(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}

    def raw(self, path, op=None):
        """Bytes and headers, for checking what /menu/<id> actually serves."""
        try:
            with (op or self.anon()).open(self.base + path) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def login(self, op, name, password="pw"):
        data = urllib.parse.urlencode({"name": name, "password": password}).encode()
        try:
            with op.open(urllib.request.Request(self.base + "/login", data=data,
                                                method="POST")) as r:
                return r.status, r.url
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def form(self, path, op, **fields):
        """A plain HTML form post; returns (status, body text)."""
        data = urllib.parse.urlencode(fields).encode()
        try:
            with op.open(urllib.request.Request(self.base + path, data=data,
                                                method="POST")) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")


def test_web(srv, D):
    section("LOGGING IN CLAIMS TODAY")
    srv.post("/api/public/order", date=D, name="Ginger", item="Tripe Stew",
             method="cash")
    srv.post("/api/public/order", date=D, name="Deb", item="Laulau",
             method="venmo", venmo_user="@deb")
    _, raw = srv.get(f"/api/public/day?date={D}")
    check("orders alone don't claim the day",
          json.loads(raw)["organiser"] == "", repr(json.loads(raw)["organiser"]))

    admin = srv.user()
    srv.login(admin, "Milan")
    _, raw = srv.get(f"/api/public/day?date={D}")
    check("claimed by logging in, before any admin action",
          json.loads(raw)["organiser"] == "Milan",
          repr(json.loads(raw)["organiser"]))

    section("THE PUBLIC PAGE LEAKS NOTHING")
    srv.post("/api/price", op=admin, date=D, desc="Tripe Stew", price="8.40")
    srv.post("/api/payment", op=admin, date=D, name="Ginger", paid="20")

    _, raw = srv.get(f"/api/public/day?date={D}")
    payload = json.loads(raw)
    check("no money field anywhere in the payload",
          not re.search(r'"(price|price_cents|owed|paid|paid_cents|subtotal'
                        r'|change|total|totals)"', raw))
    keys = sorted({k for o in payload["orders"] for k in o})
    check("order keys are name/items/rows/method/venmo_user",
          keys == ["items", "method", "name", "rows", "venmo_user"], str(keys))
    check("no menu suggestions handed to strangers",
          "suggestions" not in payload, str(sorted(payload)))

    section("ADMIN IS SHUT WITHOUT THE PASSWORD")
    for path, body in [("/api/price", {"desc": "x", "price": "1"}),
                       ("/api/payment", {"name": "Ginger", "paid": "5"}),
                       ("/api/receipt", {"receipt": "10"}),
                       ("/api/delete-person", {"name": "Ginger"}),
                       ("/api/lock", {"locked": True})]:
        code, _ = srv.post(path, date=D, **body)
        check(f"anonymous {path} refused", code in (401, 403), f"HTTP {code}")
    code, _ = srv.get("/api/history")
    check("anonymous /api/history refused", code in (401, 403), f"HTTP {code}")

    section("LOGIN")
    op = srv.user()
    code, body = srv.login(op, "", "pw")
    check("name is required", code == 401 and "Enter your name" in body)
    code, body = srv.login(op, "Someone", "wrong")
    check("wrong password refused", code == 401 and "Wrong password" in body)
    code, url = srv.login(op, "Someone")
    check("correct name+password gets in", code == 200 and url.endswith("/admin"))

    section("WHO PICKED UP")
    _, payload = srv.post("/api/public/order", date=D, name="Ian",
                          item="Long Rice", method="cash")
    check("today is Milan's (he touched it first)",
          payload.get("organiser") == "Milan", str(payload)[:120])
    srv.post("/api/place", op=op, date=D, place="Elsewhere")   # Someone edits it
    code, payload = srv.post("/api/public/order", date=D, name="Ron",
                             item="Beef", method="cash")
    check("a later organiser does not take it over",
          payload.get("organiser") == "Milan", f"HTTP {code} {str(payload)[:120]}")

    fresh = core.shift_date(D, -30)
    _, raw = srv.get(f"/api/public/day?date={fresh}")
    check("an untouched day has no organiser",
          json.loads(raw)["organiser"] == "")

    _, raw = srv.get("/api/history", op=admin)
    rows = {r["date"]: r.get("organiser") for r in json.loads(raw)["days"]}
    check("history records who ran the day", rows.get(D) == "Milan", str(rows))

    section("THE ORDERING FORM READS PLAINLY")
    _, html = srv.get("/")
    check("no <datalist>", "<datalist" not in html)
    check("no list= on the item box",
          not re.search(r'id="pItem"[^>]*list=', html))
    check("asks the friendly question",
          "What would you like to have for lunch today?" in html)
    check("the blunt version is gone", "What do you want?" not in html)
    check("the 'no set menu' line is gone", "no set menu" not in html)
    check("no 'first name is fine' placeholder", "first name is fine" not in html)
    check("no 'anything you like' placeholder", "anything you like" not in html)
    check("brand appears once, not twice",
          len(re.findall(r">Lunch<", html)) == 1,
          f"{len(re.findall(r'>Lunch<', html))} occurrences")

    section("YOUR OWN ORDER IS OBVIOUS, AND SO IS CANCELLING IT")
    _, mine_js = srv.get("/static/order.js")
    block = mine_js[mine_js.index("function renderMine"):]
    block = block[:block.index("\nfunction ")]

    # The control was a bare "x" whose only explanation was a title tooltip --
    # and phones, where most people order, have no tooltips at all.
    check("the cancel control carries visible words",
          'el("button", "cancelBtn", "Cancel")' in block)
    check("  ...and is not explained by a tooltip alone", ".title =" not in block)
    check("  ...and names the item for a screen reader",
          'setAttribute("aria-label"' in block)

    # A mis-tap must not silently bin somebody's lunch: they would not find out
    # until the food arrived and theirs was missing.
    check("cancelling asks first, it does not remove on the spot",
          "confirmingIndex = index" in block
          and "/api/public/remove" not in block.split("confirmingIndex = index")[0])
    check("the question offers a way out", '"Keep it"' in block)
    check("only one row can be asking at a time", "confirmingIndex === index" in block)

    # The heading was the most recessive style on the page. Their own name is
    # the strongest signal that the block is theirs.
    check("the heading uses the name they typed", "Your order · ${me.name}" in block)
    check("item text still goes in through el(), never as markup",
          'el("div", "what", rowText(entry))' in block)

    _, mine_html = srv.get("/")
    check("the block is a card of its own", 'id="mineCard"' in mine_html)
    # Someone who has NOT ordered still has to be told why the form vanished.
    check("the closed note stays outside that card",
          mine_html.index('id="closedNote"') > mine_html.index('id="mineCard"')
          and "</div>" in mine_html[mine_html.index('id="mine"'):
                                    mine_html.index('id="closedNote"')])

    section("CANCELLING ACTUALLY REMOVES THE RIGHT ONE")
    # Now that this is a button people will press, the endpoint behind it needs
    # a real test and not just a source pin.
    cday = core.shift_date(D, -21)
    srv.post("/api/public/order", date=cday, name="Ron", item="Veggie wrap",
             method="cash")
    srv.post("/api/public/order", date=cday, name="Ron", item="Rice plate with lamb",
             method="cash", confirm="add", item_ok=True)
    _, raw = srv.get(f"/api/public/day?date={cday}")
    items = json.loads(raw)["orders"][0]["items"]
    check("two items on the order", len(items) == 2, str(items))

    code, payload = srv.post("/api/public/remove", date=cday, name="Ron", index=0)
    left = payload["orders"][0]["items"]
    check("removing the first leaves the second", code == 200 and left == items[1:],
          f"HTTP {code} {left}")

    code, _ = srv.post("/api/public/remove", date=cday, name="Ron", index=9)
    check("an index off the end is refused", code >= 400, f"HTTP {code}")

    code, _ = srv.post("/api/public/remove", date=cday, name="Nobody", index=0)
    check("you cannot remove a name that isn't there", code >= 400, f"HTTP {code}")

    section("THE PAGE REMEMBERS NOBODY BETWEEN VISITS")
    _, js = srv.get("/static/order.js")
    # A link the whole office opens, on shared phones and laptops: a saved name
    # would greet the next person as the last one.
    check("nothing is stored in the browser", "localStorage" not in js)
    for gone in ("myName", "myVenmo", "rememberName", "orderedToday"):
        check(f"'{gone}' is gone", gone not in js)
    check("your order follows the typed name", "typedName()" in js)
    check("repeat items still skip the prompt in one sitting",
          "orderedHere" in js)
    _, html = srv.get("/")
    check("name box has no prefilled value",
          not re.search(r'id="pName"[^>]*value=', html))
    check("venmo box has no prefilled value",
          not re.search(r'id="pVenmo"[^>]*value=', html))

    section("THE VENMO NOTE NAMES THE ORGANISER")
    _, js = srv.get("/static/order.js")
    check("says a returning payer can leave it blank",
          "you can leave this blank" in js)
    check("names who will request the money",
          "${who} will request the amount" in js)
    check("falls back when nobody is recorded yet",
          '"the organiser"' in js and "you'll get a request" in js)
    # The organiser types this name at login; it renders for every coworker.
    check("note is built from textContent, never innerHTML",
          "innerHTML" not in js)

    section("CLOSING ORDERS")
    code, state = srv.post("/api/lock", op=admin, date=D, locked=True)
    check("admin can close", code == 200 and state["locked"])
    code, err = srv.post("/api/public/order", date=D, name="Late", item="X",
                         method="cash")
    check("late order refused", code == 409, err.get("error", ""))
    code, _ = srv.post("/api/lock", op=admin, date=D, locked=False)
    code, _ = srv.post("/api/public/order", date=D, name="Late", item="X",
                       method="cash")
    check("reopening lets orders through again", code == 200)

    section("VENMO REQUEST LINKS")
    # Deb has paid nothing yet -- which is exactly when you want to charge her.
    _, state = srv.post("/api/price", op=admin, date=D, desc="Laulau",
                        price="30.00")
    deb = next(p for p in state["people"] if p["name"] == "Deb")
    check("Deb owes $32 (30.00 taxed, rounded up)", str(deb["owed"]) == "32",
          f"owed {deb['owed']}")
    check("charge link is well formed",
          "venmo.com/deb?txn=charge" in (deb.get("venmo_link") or ""),
          (deb.get("venmo_link") or "(none)")[:70])
    check("charges the rounded whole dollar",
          f"amount={deb['owed']}" in (deb.get("venmo_link") or ""))


def test_same_name(srv, D):
    """Two coworkers can share a first name. Merging them onto one rounded
    total bills them as a single person and undercharges the pair."""
    section("TWO PEOPLE, ONE FIRST NAME")
    day = core.shift_date(D, -7)

    code, first = srv.post("/api/public/order", date=day, name="Ron",
                           item="Bento A", method="cash")
    check("a new name goes straight in", code == 200, f"HTTP {code}")

    code, err = srv.post("/api/public/order", date=day, name="Ron",
                         item="Saimin", method="cash")
    check("a repeat name is refused, not merged", code == 409, f"HTTP {code}")
    check("  ...and says why", err.get("error") == "name_taken", str(err))
    check("  ...and reports what that Ron already has",
          err.get("items") == ["Bento A"], str(err.get("items")))

    _, raw = srv.get(f"/api/public/day?date={day}")
    orders = json.loads(raw)["orders"]
    check("the refused attempt changed nothing",
          len(orders) == 1 and orders[0]["items"] == ["Bento A"], str(orders))

    section("SAME RON ADDS A SECOND ITEM")
    code, state = srv.post("/api/public/order", date=day, name="Ron",
                           item="Saimin", method="cash", confirm="add")
    check("confirming merges it", code == 200)
    ron = [o for o in state["orders"] if o["name"] == "Ron"]
    check("one row, two items",
          len(ron) == 1 and ron[0]["items"] == ["Bento A", "Saimin"],
          str(ron))

    section("A DIFFERENT RON GETS HIS OWN ROW AND HIS OWN BILL")
    code, state = srv.post("/api/public/order", date=day, name="Ron B",
                           item="Tripe Stew", method="cash")
    check("distinct name accepted", code == 200)
    check("two separate people now", len(state["orders"]) == 2,
          str([o["name"] for o in state["orders"]]))

    admin = srv.user()
    srv.login(admin, "Milan")
    srv.post("/api/price", op=admin, date=day, desc="Bento A", price="10.00")
    srv.post("/api/price", op=admin, date=day, desc="Saimin", price="10.00")
    _, adm = srv.post("/api/price", op=admin, date=day, desc="Tripe Stew",
                      price="10.00")
    owed = {p["name"]: str(p["owed"]) for p in adm["people"]}
    # Ron has $20 of food -> 20.94 -> $21.  Ron B has $10 -> 10.47 -> $11.
    check("Ron owes $21 for two items", owed.get("Ron") == "21", str(owed))
    check("Ron B is billed separately at $11", owed.get("Ron B") == "11", str(owed))

    # Merging rounds up once rather than once each, so the merged figure is
    # never higher and is sometimes a dollar lower. It happens to tie at these
    # amounts; two equal $10 orders show the gap plainly.
    check("merged is never more than separate",
          core.owed_dollars(3000) <= core.owed_dollars(2000) + core.owed_dollars(1000),
          f"merged ${core.owed_dollars(3000)} vs separate "
          f"${core.owed_dollars(2000) + core.owed_dollars(1000)}")
    check("two $10 orders: $22 apart but $21 merged",
          core.owed_dollars(1000) * 2 == 22 and core.owed_dollars(2000) == 21,
          f"${core.owed_dollars(1000) * 2} apart, ${core.owed_dollars(2000)} merged")

    section("THE ORGANISER'S OWN TYPING STILL MERGES")
    code, adm = srv.post("/api/order", op=admin, date=day, name="Ron",
                         item="Extra rice")
    ron = [p for p in adm["people"] if p["name"] == "Ron"]
    check("admin adding to an existing name merges as before",
          code == 200 and len(ron) == 1 and len(ron[0]["items"]) == 3,
          f"HTTP {code}")


def test_wording_prompt(srv, D):
    """The ordering page over HTTP: does it steer people onto one wording?"""
    section("THE ORDERING PAGE STEERS ONTO ONE WORDING")
    day = core.shift_date(D, -12)
    op = srv.user()
    srv.login(op, "Milan")
    srv.post("/api/place", op=op, date=day, place="Doner Shack")

    code, payload = srv.post("/api/public/order", date=day, name="Seth",
                             item="doner rice plate with lamb", method="cash")
    check("first order goes straight through", code == 200, f"HTTP {code}")
    check("and appears on the strip",
          [e["desc"] for e in payload["ordered_today"]] == ["doner rice plate with lamb"])

    code, payload = srv.post("/api/public/order", date=day, name="Ron",
                             item="Rice plate with Lamb", method="cash")
    check("a reworded same dish is questioned", code == 409, f"HTTP {code}")
    check("  ...offering the wording others used",
          payload.get("match") == "doner rice plate with lamb", str(payload)[:120])

    code, _ = srv.post("/api/public/order", date=day, name="Ron",
                       item="Rice plate with Lamb", method="cash", item_ok=True)
    check("item_ok gets past it", code == 200, f"HTTP {code}")

    code, _ = srv.post("/api/public/order", date=day, name="Pat",
                       item="Doner Rice Plate with chicken", method="cash")
    check("a CHICKEN plate is never offered a LAMB match", code == 200, f"HTTP {code}")

    code, _ = srv.post("/api/public/order", date=day, name="Deb",
                       item="doner rice plate with lamb", method="cash")
    check("wording that already matches is never questioned", code == 200, f"HTTP {code}")

    # The two questions are independent: answering the name one must not
    # silently answer this one.
    code, _ = srv.post("/api/public/order", date=day, name="Seth",
                       item="Doner rice plate w/ lamb", method="cash", confirm="add")
    check("confirm='add' alone does not skip the wording check", code == 409,
          f"HTTP {code}")

    section("MERGING OVER HTTP")
    _, raw = srv.get(f"/api/day?date={day}", op=op)
    state = json.loads(raw)
    offers = state["merge_suggestions"]
    check("a merge is offered", len(offers) == 1, str([o["into"] for o in offers]))
    check("  ...and never includes the chicken plate",
          all("chicken" not in v["desc"].lower() for v in offers[0]["variants"]))

    # Price everything first, so "money untouched" is an assertion about real
    # dollars rather than a comparison of two zeroes.
    for group in state["groups"]:
        srv.post("/api/price", op=op, date=day, desc=group["desc"], price="15")
    _, raw = srv.get(f"/api/day?date={day}", op=op)
    before = json.loads(raw)["totals"]["items"]
    check("  ...with real money on the day", before != "0.00", before)

    code, payload = srv.post("/api/merge-items", op=op, date=day, into=offers[0]["into"],
                             **{"from": [v["desc"] for v in offers[0]["variants"]]})
    check("merge succeeds", code == 200, f"HTTP {code}")
    check("  ...money untouched", payload["totals"]["items"] == before,
          f"{before} -> {payload['totals']['items']}")
    check("  ...and the offer is gone", not payload["merge_suggestions"])

    code, _ = srv.post("/api/receipt", op=op, date=day, receipt_items="not-a-number")
    check("a junk item count is refused", code == 400, f"HTTP {code}")


def test_menu_link_safety():
    """The one input another person's browser is invited to click.

    A 'javascript:' href would run script on a page the whole office opens, and
    'data:' is a page of somebody else's choosing wearing our name. The scheme
    is an allowlist and everything else is rejected outright -- never cleaned
    up and let through.
    """
    section("A PASTED MENU LINK CANNOT CARRY SCRIPT")
    refuse = [
        "javascript:alert(1)", "JavaScript:alert(1)", "  javascript:alert(1)  ",
        "jAvAsCrIpT:alert(1)", "\tjavascript:alert(1)", "java\nscript:alert(1)",
        "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)",
        "file:///C:/Windows/win.ini", "about:blank",
        "example.com", "//evil.example", "http://", "https://", "", "   ",
        "http://" + "a" * 2100,
    ]
    leaked = [u for u in refuse if webapp.safe_menu_url(u) is not None]
    check(f"all {len(refuse)} dangerous or malformed URLs refused", not leaked,
          str(leaked))

    allow = ["https://donershack.com/menu", "http://example.com",
             "https://a.co/x?y=1#z", "https://menu.example.co.uk/lunch.pdf"]
    blocked = [u for u in allow if webapp.safe_menu_url(u) is None]
    check("ordinary http and https links accepted", not blocked, str(blocked))

    check("labelled by host", webapp.link_label("https://a.co/x?y=1") == "a.co")
    check("  ...with www stripped",
          webapp.link_label("https://www.DonerShack.com/m") == "DonerShack.com")

    section("AND IS NEVER SERVED FROM OUR OWN ORIGIN")
    # Nothing a user typed should come back out of our own origin as a body.
    for js, tag in (("static/order.js", "public page"),
                    ("static/admin.js", "organiser page")):
        src = (HERE / js).read_text(encoding="utf-8")
        block = src[src.index('kind === "link"'):][:600]
        check(f"{tag} link opens in a new tab", 'target = "_blank"' in block)
        check(f"{tag} sets noopener AND noreferrer",
              'rel = "noopener noreferrer"' in block)
        check(f"{tag} builds the label with el(), so it stays text",
              'el("a", "menuLink", file.filename)' in block)


def test_markup_ids():
    """Every id the scripts look up must exist in the markup.

    A mistyped id is silent in the browser -- $("thing") is just null and the
    feature quietly does nothing -- so catch it here instead.
    """
    section("SCRIPTS AND MARKUP AGREE")
    for js, html in (("static/order.js", "templates/order.html"),
                     ("static/admin.js", "templates/admin.html")):
        wanted = set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)',
                                (HERE / js).read_text(encoding="utf-8")))
        present = set(re.findall(r'id="([A-Za-z0-9_]+)"',
                                 (HERE / html).read_text(encoding="utf-8")))
        missing = sorted(wanted - present)
        check(f"{js} looks up nothing that isn't there", not missing, str(missing))

    # lines.js loads before both page scripts, which each declare their own
    # top-level $ and el. A second top-level declaration of either is a
    # SyntaxError that stops the whole page, so it may add exactly one name.
    lines = (HERE / "static" / "lines.js").read_text(encoding="utf-8")
    top = re.findall(r"^(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)", lines, re.M)
    check("lines.js adds one name to the page, ItemLines", top == ["ItemLines"], str(top))
    for page in ("order", "admin"):
        html = (HERE / "templates" / f"{page}.html").read_text(encoding="utf-8")
        check(f"{page}.html loads lines.js before its own script",
              0 < html.find("lines.js") < html.find(f"{page}.js"))


def test_no_drift():
    """The real day files must produce byte-identical numbers to before."""
    section("REAL DAYS ARE UNAFFECTED")
    days = sorted((HERE / "data").glob("lunch_*.json"))
    if not days:
        check("no data/ to compare against (fine on a clean checkout)", True)
        return
    for path in days:
        day = json.loads(path.read_text(encoding="utf-8"))
        for key, default in (("receipt_subtotal_cents", None), ("receipt_items", None)):
            day.setdefault(key, default)
        t = core.totals(day)
        check(f"{path.stem[6:]} still reconciles",
              t["subtotal_diff_cents"] is None and t["count_diff"] is None
              and t["keyed_items"] == sum(len(o["items"]) for o in day["orders"]))


def test_drinks(srv, D):
    """A drink is one more item row, carrying what to get if they're out."""
    section("A DRINK, WITH WHAT TO GET INSTEAD")
    day = core.shift_date(D, -40)
    op = srv.user()
    srv.login(op, "Milan")
    srv.post("/api/place", op=op, date=day, place="Doner Shack")

    code, pub = srv.post("/api/public/order", date=day, name="Ron",
                         item="Rice plate with lamb", method="cash",
                         drink="Coke", drink_fallback="Diet Coke")
    check("food and drink land in one request", code == 200, f"HTTP {code}")
    me = pub["orders"][0]
    check("two item rows", len(me["items"]) == 2, str(me["items"]))
    check("the drink row carries its backup",
          me["rows"][1] == {"desc": "Coke", "drink": True, "fallback": "Diet Coke"},
          str(me["rows"]))
    check("the food row is plain",
          me["rows"][0] == {"desc": "Rice plate with lamb", "drink": False})
    check("the tap-to-match strip lists food only",
          [e["desc"] for e in pub["ordered_today"]] == ["Rice plate with lamb"],
          str(pub["ordered_today"]))
    check("still no money anywhere in the public payload",
          not any(w in json.dumps(pub).lower() for w in ("price", "paid", "owed", "cents")))

    srv.post("/api/public/order", date=day, name="Deb", item="Veggie wrap",
             method="cash", drink="Coke", drink_fallback="Sprite")
    srv.post("/api/public/order", date=day, name="Ian", item="Doner wrap with lamb",
             method="cash", drink="Coke")
    srv.post("/api/public/order", date=day, name="Gina", item="Salad - no meat",
             method="cash", drink="", drink_fallback="Sprite")
    _, raw = srv.get(f"/api/day?date={day}", op=op)
    state = json.loads(raw)
    coke = next(g for g in state["groups"] if g["desc"] == "Coke")
    check("three Cokes group as one line", coke["count"] == 3, str(coke["count"]))
    check("  ...flagged as a drink", coke["drink"] is True)
    check("  ...listing the distinct backups",
          [(f["desc"], f["count"]) for f in coke["fallbacks"]]
          == [("Diet Coke", 1), ("Sprite", 1)], str(coke["fallbacks"]))
    gina = next(p for p in state["people"] if p["name"] == "Gina")
    check("a backup with no drink is ignored", len(gina["items"]) == 1)
    check("drinks count as items on the receipt check",
          state["totals"]["keyed_items"] == 7, str(state["totals"]["keyed_items"]))

    before = state["totals"]["items_cents"] if "items_cents" in state["totals"]         else int(round(float(state["totals"]["items"]) * 100))
    srv.post("/api/price", op=op, date=day, desc="Coke", price="2.50")
    _, raw = srv.get(f"/api/day?date={day}", op=op)
    state = json.loads(raw)
    after = int(round(float(state["totals"]["items"]) * 100))
    # A delta, not an absolute: food may already carry a price remembered from
    # an earlier test at this place, which is the remembered-price feature
    # doing its job.
    check("pricing the group prices every Coke", after - before == 750,
          f"{before} -> {after}")
    ron = next(p for p in state["people"] if p["name"] == "Ron")
    check("the organiser sees the backup per person",
          ron["item_rows"][1]["fallback"] == "Diet Coke", str(ron["item_rows"]))

    # Neither available: the organiser removes it, and it leaves the numbers.
    code, state = srv.post("/api/remove-item", op=op, date=day, name="Ron", index=1)
    gone = after - int(round(float(state["totals"]["items"]) * 100))
    check("removing a drink drops it from the count and the total",
          state["totals"]["keyed_items"] == 6 and gone == 250,
          f"{state['totals']['keyed_items']} items, ${gone/100:.2f} removed")

    section("THE DRINK IS EXPLAINED ON THE FORM")
    _, html = srv.get("/")
    check("the rule is stated where the box is",
          "out of both, you get no drink" in html)
    check("both boxes exist", 'id="pDrink"' in html and 'id="pDrinkAlt"' in html)


def _person(state, name):
    return next(p for p in state["people"] if p["name"] == name)


def _state(srv, op, day):
    return json.loads(srv.get(f"/api/day?date={day}", op=op)[1])


def test_lines(srv, D):
    """Several things per order, each with its own optional backup -- on the
    ordering page and on the organiser's own form."""
    section("ONE THING PER LINE, EACH WITH ITS OWN BACKUP")
    day = core.shift_date(D, -50)
    op = srv.user()
    srv.login(op, "Milan")
    srv.post("/api/place", op=op, date=day, place="Line Diner")

    code, pub = srv.post(
        "/api/public/order", date=day, name="Ron", method="cash",
        items=[{"desc": "Rice plate with lamb", "fallback": "Rice plate with chicken"},
               {"desc": "Side salad", "fallback": ""},
               {"desc": "Fries", "fallback": "fries"},
               {"desc": "  ", "fallback": "a backup for nothing"}],
        drink="Coke", drink_fallback="Diet Coke")
    check("three lines and a drink land in one request", code == 200, f"HTTP {code}")
    rows = pub["orders"][0]["rows"]
    check("  ...as four item rows, in order, the empty line skipped",
          [r["desc"] for r in rows] == ["Rice plate with lamb", "Side salad", "Fries", "Coke"],
          str(rows))
    check("food carries its own backup", rows[0].get("fallback") == "Rice plate with chicken")
    check("a line with no backup has none", "fallback" not in rows[1])
    check("a backup that repeats its item is dropped", "fallback" not in rows[2])
    check("the drink keeps its own",
          rows[3] == {"desc": "Coke", "drink": True, "fallback": "Diet Coke"}, str(rows[3]))
    check("still no money in the public payload",
          not any(w in json.dumps(pub).lower() for w in ("price", "paid", "owed", "cents")))

    code, _ = srv.post("/api/public/order", date=day, name="Deb", method="cash",
                       items=[], drink="Iced tea")
    check("a drink on its own is a fine order", code == 200, f"HTTP {code}")
    code, err = srv.post("/api/public/order", date=day, name="Ian", method="cash",
                         items=[{"desc": ""}])
    check("nothing at all is refused", code == 400 and "like" in err.get("error", ""), str(err))
    code, _ = srv.post("/api/public/order", date=day, name="Ian", method="cash",
                       items=[{"desc": f"Thing {n}"} for n in range(11)])
    check("eleven lines are refused", code == 400, f"HTTP {code}")
    code, _ = srv.post("/api/public/order", date=day, name="Ian", method="cash",
                       items=[{"desc": "x" * 151}])
    check("a 151-character line is refused", code == 400, f"HTTP {code}")

    section("THE SAME-DISH QUESTION IS ASKED LINE BY LINE")
    code, err = srv.post("/api/public/order", date=day, name="Pat", method="cash",
                         items=[{"desc": "Side salad"}, {"desc": "rice plate w/ lamb"}])
    check("the reworded line is questioned", code == 409 and err.get("error") == "similar_item",
          f"HTTP {code}")
    check("  ...naming which line it means",
          err.get("line") == 1 and err.get("desc") == "rice plate w/ lamb", str(err))
    code, err = srv.post("/api/public/order", date=day, name="Pat", method="cash",
                         items=[{"desc": "rice plate w/ lamb"},
                                {"desc": "Rice plate lamb, extra rice"}],
                         items_ok=["rice plate w/ lamb"])
    check("an answer covers only its own line", code == 409 and err.get("line") == 1, str(err))
    code, _ = srv.post("/api/public/order", date=day, name="Pat", method="cash",
                       items=[{"desc": "rice plate w/ lamb"},
                              {"desc": "Rice plate lamb, extra rice"}],
                       items_ok=["rice plate w/ lamb", "Rice plate lamb, extra rice"])
    check("answering both lets it through", code == 200, f"HTTP {code}")

    section("THE ORGANISER'S FORM TAKES LINES AND A DRINK TOO")
    code, adm = srv.post("/api/order", op=op, date=day, name="Kai", paid="20",
                         items=[{"desc": "Veggie wrap", "fallback": "Salad - no meat"},
                                {"desc": "Fries"}],
                         drink="Water")
    kai = _person(adm, "Kai")
    check("two lines and a drink for Kai",
          code == 200 and [r["desc"] for r in kai["item_rows"]] == ["Veggie wrap", "Fries", "Water"],
          f"HTTP {code} {kai['item_rows'] if code == 200 else ''}")
    check("  ...with the backup kept and the drink marked",
          kai["item_rows"][0]["fallback"] == "Salad - no meat" and kai["item_rows"][2]["drink"])
    check("  ...and the cash recorded", kai["paid"] == "20.00")
    lamb = next(g for g in adm["groups"] if g["desc"] == "Rice plate with lamb")
    check("a food group lists its backups for the call",
          [(f["desc"], f["count"]) for f in lamb["fallbacks"]] == [("Rice plate with chicken", 1)],
          str(lamb["fallbacks"]))
    check("  ...without being taken for a drink", lamb["drink"] is False)

    section("EDITING KEEPS DRINKS, BACKUPS AND PRICES")
    for desc, price in (("Rice plate with lamb", "15.50"), ("Coke", "2.50"),
                        ("Veggie wrap", "14")):
        srv.post("/api/price", op=op, date=day, desc=desc, price=price)
    ron = _person(_state(srv, op, day), "Ron")
    rows = [{"desc": r["desc"], "fallback": r["fallback"],
             "kind": "drink" if r["drink"] else None} for r in ron["item_rows"]]
    rows[1]["desc"] = "Veggie wrap"          # reworded from "Side salad"
    code, adm = srv.post("/api/edit-person", op=op, date=day, name="Ron", new_name="Ron",
                         items=rows)
    ron = _person(adm, "Ron")["item_rows"]
    check("the edit goes through with no prices sent", code == 200, f"HTTP {code}")
    check("the drink is still a drink, backup and all",
          ron[3]["drink"] and ron[3]["fallback"] == "Diet Coke", str(ron[3]))
    check("the food backup survived", ron[0]["fallback"] == "Rice plate with chicken")
    check("unchanged lines keep their price",
          ron[0]["price"] == "15.50" and ron[3]["price"] == "2.50", str(ron))
    check("a reworded line takes today's price for its new wording",
          ron[1]["price"] == "14.00", str(ron[1]))

    section("EVERY BOX SAYS WHAT IT IS FOR")
    # Bare numbers and a terse "If they're out" left people guessing; the user
    # asked for it in words: "Item 1 -- what you want to order", and "if they
    # are out, what would you like to get?"
    lines_js = (HERE / "static" / "lines.js").read_text(encoding="utf-8")
    _, page = srv.get("/")
    question = "If they're out, what would you like to get?"
    check("each backup asks the question outright", question in lines_js)
    check("  ...and so does the drink's", question in page)
    check("items are headed 'Item 1 — what you want to order'",
          "`Item ${index + 1}`" in lines_js and "what you want to order" in lines_js)
    check("the organiser's own form stays compact",
          "compact: true" in (HERE / "static" / "admin.js").read_text(encoding="utf-8"))


def test_settle(srv, D):
    """Someone who still owes pays up, in cash or on Venmo."""
    section("MARKING SOMEONE PAID")
    day = core.shift_date(D, -51)
    op = srv.user()
    srv.login(op, "Milan")
    # Everyone's plate is $15.50, so everyone owes $17 (16.23 rounded up).
    for name, method, paid in (("Deb", "venmo", ""), ("Ron", "cash", "15"),
                               ("Kai", "cash", "15"), ("Ann", "venmo", "10"),
                               ("Gina", "cash", "")):
        srv.post("/api/order", op=op, date=day, name=name, item=f"{name}'s plate",
                 method=method, paid=paid)
    for name in ("Deb", "Ron", "Kai", "Ann"):
        srv.post("/api/price", op=op, date=day, desc=f"{name}'s plate", price="15.50")
    srv.post("/api/venmo-user", op=op, date=day, name="Ann", venmo_user="@ann")

    code, _ = srv.post("/api/settle", date=day, name="Deb", method="venmo")
    check("anonymous cannot mark anyone paid", code == 403, f"HTTP {code}")

    state = _state(srv, op, day)
    deb, ann = _person(state, "Deb"), _person(state, "Ann")
    check("an unpaid person owes it all", deb["status"] == "unpaid" and deb["outstanding"] == "17.00")
    check("a short Venmo payer is asked for just the rest",
          "amount=7&" in ann["venmo_link"], ann["venmo_link"])
    # Deb $17, Ron $2, Kai $2, Ann $7. Gina is unpriced, so not yet counted.
    check("all four are counted as still owing", state["totals"]["owing"] == 4
          and state["totals"]["outstanding"] == "28.00",
          f'{state["totals"]["owing"]} / {state["totals"]["outstanding"]}')

    code, state = srv.post("/api/settle", op=op, date=day, name="Deb", method="venmo")
    deb = _person(state, "Deb")
    check("unpaid -> paid in full, on Venmo",
          code == 200 and deb["paid"] == "17.00" and deb["method"] == "venmo"
          and deb["status"] == "paid", str(deb.get("paid")))
    code, state = srv.post("/api/settle", op=op, date=day, name="Ron", method="cash")
    ron = _person(state, "Ron")
    check("short in cash, the rest in cash -> one payment of $17",
          ron["paid"] == "17.00" and not ron["topups"] and ron["change"] == "0.00", str(ron))
    code, state = srv.post("/api/settle", op=op, date=day, name="Kai", method="venmo")
    kai = _person(state, "Kai")
    check("short in cash, the rest on Venmo -> kept as its own payment",
          kai["paid"] == "15.00" and kai["topups"] == [{"amount": "2.00", "method": "venmo"}]
          and kai["status"] == "paid", str(kai))
    t = state["totals"]
    # Cash: Ron $17 + Kai $15. Venmo: Deb $17 + Ann $10 + Kai's later $2.
    check("each dollar lands in the pot it came in",
          t["cash_in"] == "32.00" and t["venmo_in"] == "29.00", f'{t["cash_in"]} / {t["venmo_in"]}')

    code, err = srv.post("/api/settle", op=op, date=day, name="Kai", method="cash")
    check("a double tap records nothing twice", code == 400 and "owe" in err.get("error", ""),
          str(err))
    code, err = srv.post("/api/settle", op=op, date=day, name="Gina", method="cash")
    check("an unpriced order can't be settled", code == 400 and "Price" in err.get("error", ""),
          str(err))

    code, state = srv.post("/api/remove-topup", op=op, date=day, name="Kai", index=0)
    kai = _person(state, "Kai")
    check("a later payment can be undone",
          code == 200 and kai["status"] == "short" and kai["outstanding"] == "2.00", str(kai))
    check("  ...and leaves the Venmo pot", state["totals"]["venmo_in"] == "27.00",
          state["totals"]["venmo_in"])


def test_deploy_safety():
    """28 Sept: a redeploy pulled in SQLAlchemy 2.1, whose default Postgres
    driver is not installed, and every page that read an order failed."""
    section("A REDEPLOY CANNOT SWAP THE DATABASE DRIVER")
    host = "u:p@db.example/lunch?sslmode=require"
    check("postgresql:// names psycopg2 outright",
          store.engine_url(f"postgresql://{host}") == f"postgresql+psycopg2://{host}")
    check("Render's old postgres:// too",
          store.engine_url(f"postgres://{host}") == f"postgresql+psycopg2://{host}")
    check("a driver someone chose is left alone",
          store.engine_url(f"postgresql+psycopg://{host}") == f"postgresql+psycopg://{host}")
    check("sqlite is untouched", store.engine_url("sqlite:///x.db") == "sqlite:///x.db")

    reqs = [line.strip() for line in
            (HERE / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]
    open_ended = [r for r in reqs if "<" not in r and "==" not in r]
    check("every requirement has a ceiling", reqs and not open_ended, str(open_ended))


def test_bar():
    section("THE BAR LEADS WITH ONE ANSWER")
    # The 23 Sept shape that read as a contradiction: $235 in cash with $28 of
    # it handed back, $20 on Venmo, a $221.87 receipt paid in cash.
    day = _day([_order("A", 19700, 23500, "cash"), _order("B", 1900, 2000, "venmo")],
               receipt_cents=22187)
    t = core.totals(day)
    check("$207 cash once the $28 change is back",
          t["cash_on_hand_cents"] == 20700 and t["cash_change_cents"] == 2800)
    check("$14.87 short at the till", t["cash_short_cents"] == 1487)
    check("and $5.13 ahead overall", t["net_surplus_cents"] == 513)
    check("the headline is exactly the sum printed under it",
          t["net_surplus_cents"]
          == t["cash_on_hand_cents"] + t["venmo_held_cents"] - t["due_cents"])
    view = webapp.admin_view(day)["totals"]
    check("the page is told 'ahead'", view["net_surplus"] == "5.13" and not view["net_short"])

    day["orders"].append(_order("C", 1650))          # owes $18 and has paid nothing
    view = webapp.admin_view(day)["totals"]
    check("money still to come is counted apart",
          view["owing"] == 1 and view["outstanding"] == "18.00")
    check("  ...and says where it leaves you once paid",
          view["after_collect"] == "23.13" and not view["after_short"])


def test_staying_logged_in(srv, D):
    section("A HICCUP IS NEVER DRESSED UP AS A LOGIN")
    webapp.app.logger.disabled = True        # the handler logs; this one is on purpose
    try:
        with webapp.app.test_request_context("/admin"):
            body, code = webapp.unhandled(RuntimeError("database waking up"))
    finally:
        webapp.app.logger.disabled = False
    check("an error on a page is a 500", code == 500)
    check("  ...that offers to try again", "Try again" in body)
    check("  ...and is not the login form", 'name="password"' not in body)

    real, calls = store.get_setting, []

    def flaky(key):
        calls.append(key)
        if len(calls) == 1:
            raise RuntimeError("server closed the connection unexpectedly")
        return real(key)

    store.get_setting = flaky
    try:
        stamp = webapp.password_stamp()
    finally:
        store.get_setting = real
    check("a failed read of the login stamp is retried once",
          len(calls) == 2 and stamp == (real("password_changed_at") or ""))

    section("A SESSION KEY THAT OUTLIVES A RESTART")
    first = store.stable_secret("test_session_secret")
    check("made once, then kept", first and store.stable_secret("test_session_secret") == first)
    from flask import Flask
    bare = Flask("bare")
    bare.secret_key = None
    webapp._Sessions().get_signing_serializer(bare)
    check("with no SECRET_KEY set, sessions use the stored key",
          bare.secret_key == store.stable_secret("session_secret"))

    section("THE NAME IS REMEMBERED, AND A LOGIN ONLY GOES HOME")
    op = srv.user()
    srv.login(op, "Seth")
    srv.form("/logout", op)
    _, page = srv.get("/login", op=op)
    check("logged out, the form still has the name in", 'value="Seth"' in page)

    class _Stay(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None                      # report the redirect, don't follow it

    data = urllib.parse.urlencode({"name": "Seth", "password": "pw"}).encode()
    for target in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
        stay = urllib.request.build_opener(
            _Stay, urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request = urllib.request.Request(
            srv.base + "/login?next=" + urllib.parse.quote(target, safe=""),
            data=data, method="POST")
        try:
            stay.open(request)
            where = "(no redirect)"
        except urllib.error.HTTPError as err:
            where = err.headers.get("Location", "")
        check(f"next={target} stays on this site", where.endswith("/admin"), where)


def test_password(srv, D):
    """Change the password from the site. LAST: it changes the shared server's
    password, and cleans up through the recovery path at the end."""
    section("THE PASSWORD CAN BE CHANGED FROM THE SITE")
    first = srv.user()
    code, _ = srv.login(first, "Milan")             # env password, "pw"
    check("the environment password works while none is set", code == 200)
    probe = lambda op: srv.post("/api/lock", op=op, date=D, locked=False)[0]
    check("  ...and the session is live", probe(first) == 200)

    code, _ = srv.form("/password", srv.anon(), current="pw", new="a", again="a")
    check("anonymous cannot change it", code == 403, f"HTTP {code}")

    # A second device, logged in BEFORE the change.
    second = srv.user()
    srv.login(second, "Seth")
    check("a second device is in too", probe(second) == 200)

    code, body = srv.form("/password", first, current="wrong",
                          new="lunchtime2026", again="lunchtime2026")
    check("wrong current password refused", code == 400 and "current password" in body)
    code, _ = srv.form("/password", first, current="pw", new="short", again="short")
    check("seven characters refused", code == 400, f"HTTP {code}")
    code, body = srv.form("/password", first, current="pw", new="lunchtime2026",
                          again="different2026")
    check("mismatched new passwords refused", code == 400 and "match" in body)
    code, body = srv.form("/password", first, current="pw", new="lunchtime2026",
                          again="lunchtime2026")
    check("a proper change is accepted", code == 200 and "Password changed" in body)

    stored = store.get_setting("admin_password_hash") or ""
    check("stored as a hash, never the password itself",
          stored.startswith("scrypt:") and "lunchtime2026" not in stored, stored[:24])
    check("the device that changed it stays in", probe(first) == 200)
    check("EVERY OTHER device is logged out", probe(second) == 403, f"HTTP {probe(second)}")

    code, _ = srv.login(srv.user(), "Milan", "pw")
    check("the old password is refused", code == 401, f"HTTP {code}")
    fresh = srv.user()
    code, _ = srv.login(fresh, "Milan", "lunchtime2026")
    check("the new password works", code == 200 and probe(fresh) == 200)

    _, page = srv.get("/password", op=fresh)
    check("the page never shows a password",
          "lunchtime2026" not in page and '"pw"' not in page)
    _, admin = srv.get("/admin", op=fresh)
    check("the organiser page links to it", 'href="/password"' in admin)

    section("AND RECOVERED FROM THE HOST IF FORGOTTEN")
    os.environ["ADMIN_PASSWORD_RESET"] = "rescue-me"
    try:
        code, _ = srv.login(srv.user(), "Milan", "rescue-me")
        check("the reset value logs in while it is set", code == 200, f"HTTP {code}")
        check("  ...and wipes the stored hash",
              store.get_setting("admin_password_hash") is None)
        code, _ = srv.login(srv.user(), "Milan", "pw")
        check("  ...so the environment password works again", code == 200)
    finally:
        os.environ.pop("ADMIN_PASSWORD_RESET", None)
    code, _ = srv.login(srv.user(), "Milan", "rescue-me")
    check("the reset value is refused once the variable is gone", code == 401)


def test_login_claims_day(srv, D):
    """Milan already claimed today by logging in during test_web."""
    section("A LATER ORGANISER CANNOT TAKE THE DAY OVER")
    seth = srv.user()
    srv.login(seth, "Seth")
    _, raw = srv.get(f"/api/public/day?date={D}")
    check("still Milan's day after Seth logs in",
          json.loads(raw)["organiser"] == "Milan",
          repr(json.loads(raw)["organiser"]))

    section("EMPTY DAYS STAY OUT OF THE HISTORY")
    _, raw = srv.get("/api/history", op=seth)
    rows = json.loads(raw)["days"]
    check("every listed day has orders in it",
          all(r["people"] > 0 for r in rows),
          str([(r["date"], r["people"]) for r in rows]))


# A real 1x1 PNG and the smallest thing a PDF reader will accept.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def test_menu_files(srv, D):
    section("ONLY THE ORGANISER CAN UPLOAD A MENU")
    place = "Monarch Seafood"
    code, _ = srv.upload(None, place, "menu.png", PNG, "image/png")
    check("anonymous upload refused", code in (401, 403), f"HTTP {code}")
    code, _ = srv.post("/api/menu-file/delete", id="whatever", place=place)
    check("anonymous delete refused", code in (401, 403), f"HTTP {code}")

    admin = srv.user()
    srv.login(admin, "Milan")

    section("ONLY REAL PHOTOS AND PDFs GET IN")
    code, err = srv.upload(admin, place, "menu.jpg", b"this is just text",
                           "image/jpeg")
    check("a text file renamed .jpg is rejected", code == 400, str(err))
    # The dangerous one: HTML served from our own origin would run as a page.
    code, err = srv.upload(admin, place, "menu.jpg",
                           b"<html><script>alert(1)</script></html>", "image/jpeg")
    check("HTML disguised as an image is rejected", code == 400, str(err))
    code, err = srv.upload(admin, "", "menu.png", PNG, "image/png")
    check("upload with no Place is refused", code == 400, str(err))

    section("A PHOTO AND A PDF ROUND-TRIP")
    code, data = srv.upload(admin, place, "front.png", PNG, "image/png")
    check("photo accepted", code == 200, str(data)[:80])
    code, data = srv.upload(admin, place, "specials.pdf", PDF, "application/pdf")
    check("pdf accepted", code == 200, str(data)[:80])
    kinds = sorted(f["kind"] for f in data["menu"])
    check("one image and one pdf listed", kinds == ["image", "pdf"], str(kinds))

    image = next(f for f in data["menu"] if f["kind"] == "image")
    code, blob, headers = srv.raw(f"/menu/{image['id']}")
    check("served to anyone, no login", code == 200)
    check("exact bytes come back", blob == PNG, f"{len(blob)} bytes")
    check("served as the sniffed type, not the claimed one",
          headers.get("Content-Type") == "image/png",
          headers.get("Content-Type"))
    check("nosniff header set", headers.get("X-Content-Type-Options") == "nosniff")

    section("MENUS FOLLOW THE RESTAURANT, NOT THE DAY")
    later = core.shift_date(D, 40)
    srv.post("/api/place", op=admin, date=later, place=place)
    _, raw = srv.get(f"/api/public/day?date={later}")
    check("a future day at the same place shows it",
          len(json.loads(raw)["menu"]) == 2, raw[:120])

    elsewhere = core.shift_date(D, 41)
    srv.post("/api/place", op=admin, date=elsewhere, place="Somewhere Else")
    _, raw = srv.get(f"/api/public/day?date={elsewhere}")
    check("a different restaurant shows nothing",
          json.loads(raw)["menu"] == [], raw[:120])

    section("SIX FILES IS THE LIMIT")
    for _ in range(4):
        srv.upload(admin, place, "more.png", PNG, "image/png")
    code, err = srv.upload(admin, place, "seventh.png", PNG, "image/png")
    check("the seventh is refused", code == 400, str(err))

    section("REMOVING ONE")
    code, data = srv.post("/api/menu-file/delete", op=admin,
                          id=image["id"], place=place)
    check("delete accepted", code == 200)
    check("gone from the listing",
          all(f["id"] != image["id"] for f in data["menu"]))
    code, _, _ = srv.raw(f"/menu/{image['id']}")
    check("and gone from /menu/<id>", code == 404, f"HTTP {code}")

    section("THE UPLOAD IS FINDABLE, AND ORGANISER-ONLY")
    _, page = srv.get("/admin", op=admin)
    check("reads as a drop target", "Drop a file here" in page)
    check("says what it is for", "Add a menu photo or PDF" in page)
    # A disabled control gives no reason for being disabled; the click handler
    # points at the Place field instead.
    check("the file input is never disabled in the markup",
          "disabled" not in page, "found a disabled attribute")
    _, adminjs = srv.get("/static/admin.js", op=admin)
    check("a blank Place explains itself", "needsPlace" in adminjs)
    check("drag and drop wired up", "dataTransfer" in adminjs)

    section("THE WHOLE IMAGE IS VISIBLE")
    _, css = srv.get("/static/style.css")
    # Can't assert pixels from here, but these three are what stop the crop,
    # and each is a one-word edit away from silently coming back.
    check("stage can shrink below the image's intrinsic height",
          "min-height: 0" in css)
    check("lightbox tracks the visible viewport on phones", "100dvh" in css)
    check("thumbnails letterbox rather than trim",
          "object-fit: contain" in css and
          "object-fit: cover" not in css.split(".lightbox")[0])

    section("THE EDIT PANEL HAS NO MYSTERY PRICE BOX")
    # It was a per-item price box with "later" as its placeholder, and nobody
    # could tell what it was for. Prices are typed once, in step 3.
    check("no price box in the edit panel",
          "editPrice" not in adminjs and 'placeholder = "later"' not in adminjs)
    check("the $ boxes that remain keep their padding rule", ".money.money input" in css)

    section("NOTHING EXPLAINS ITSELF IN A BOX TOO NARROW TO READ IT")
    # .money inputs are a fixed 150px. A long placeholder is silently clipped
    # mid-word -- "the receipt's own item total" showed as "the receipt's owr",
    # so the field meant to explain the check explained nothing. Anything that
    # needs more than a short example belongs in a hint line instead.
    admin = (HERE / "templates" / "admin.html").read_text(encoding="utf-8")
    money_boxes = re.findall(r'<div class="money">.*?</div>', admin, re.S)
    check("money boxes are found at all", len(money_boxes) >= 4, str(len(money_boxes)))
    too_long = []
    for box in money_boxes:
        for placeholder in re.findall(r'placeholder="([^"]*)"', box):
            if len(placeholder) > 12:
                too_long.append(placeholder)
    check("no money placeholder can be clipped", not too_long, str(too_long))
    check("the receipt fields are explained in a hint instead",
          "the card fee this place adds" in admin)
    check("hints get a line of their own in the flex row",
          ".receiptCheck .hint" in css)

    section("GREEN MEANS THE MONEY WAS CHECKED")
    # A matching item count alone used to paint green, having compared nothing
    # about money -- the same lie as the old permanently-red check, inverted.
    js = (HERE / "static" / "admin.js").read_text(encoding="utf-8")
    check("green is gated on the money comparison having actually run",
          re.search(r'has\(t\.charge_diff_cents\)\)\s*\{\s*'
                    r'verdict\.className = "verdict g"', js) is not None)
    check("there is an amber 'not checked yet' state", 'verdict a' in js)
    check("  ...and it is styled", ".verdict.a" in css)
    check("the tax-estimate fallback no longer claims a tick",
          "✓ matches my total" not in js)
    # Red is what says "a plate is missing". A fee that crept up is real money
    # but is not a mis-rung order, so it must not spend the same colour.
    check("a wrong count is red", re.search(r'countWrong\)\s*\{\s*'
                                            r'verdict\.className = "verdict r"', js))
    check("a wrong fee alone is amber, not red",
          re.search(r'moneyWrong\)\s*\{\s*verdict\.className = "verdict a"', js))

    section("THE APP HAS A NAME")
    for page, title in (("order", "Wasa Lunch Order"),
                        ("admin", "Wasa Lunch Order"),
                        ("history", "Wasa Lunch — History"),
                        ("login", "Wasa Lunch — Organiser")):
        html = (HERE / "templates" / f"{page}.html").read_text(encoding="utf-8")
        check(f"{page}.html tab reads '{title}'", f"<title>{title}</title>" in html)
    # The header brand sits beside the place name; repeating the full name
    # there is what made it read "Lunch Lunch" before.
    check("the header brand is left alone",
          '<div class="brand">Lunch</div>' in admin)


def test_encoding():
    """Text that was UTF-8, got read as Windows-1252, and saved back that way.

    An em dash becomes 'a-euro-quote' and reaches the browser looking like
    line noise. Nothing crashes, so it only surfaces when somebody reads the
    page -- which is exactly why it is worth a test.
    """
    section("FILES ARE CLEAN UTF-8")
    suffixes = {".py", ".html", ".js", ".css", ".md", ".bat", ".yaml", ".yml"}
    skip = {".git", "__pycache__", "data", "web"}
    bad_text, bad_bom = [], []

    for path in sorted(HERE.rglob("*")):
        if not path.is_file() or any(p in skip for p in path.parts):
            continue
        if path.suffix not in suffixes:
            continue
        raw = path.read_bytes()
        if raw[:3] == b"\xef\xbb\xbf":
            bad_bom.append(path.name)
        text = raw.decode("utf-8", "replace")
        # Escapes, not literal glyphs, so this file does not flag
        # itself: U+00E2 then U+20AC is a UTF-8 lead byte seen
        # through cp1252.
        markers = ("\u00e2\u20ac", "\u00c3\u00a9", "\u00c2\u00a0")
        if any(m in text for m in markers):
            bad_text.append(path.name)

    check("no mojibake anywhere", not bad_text, str(bad_text))
    # A BOM before <!doctype> can drop old browsers out of standards mode, and
    # in a .bat file it breaks the first command outright.
    check("no byte-order marks", not bad_bom, str(bad_bom))


def test_menu_files_on_disk():
    """The same behaviour with no database, where files land under data/."""
    section("MENU FILES WITHOUT A DATABASE")
    saved = os.environ.pop("DATABASE_URL")
    store.reset_for_tests()
    try:
        file_id = store.save_menu_file("Diner", "m.png", "image/png", PNG)
        listed = store.list_menu_files("Diner")
        check("listed for its place", len(listed) == 1 and listed[0]["id"] == file_id)
        check("listing carries no bytes", "data" not in listed[0], str(listed[0]))
        meta, blob = store.load_menu_file(file_id)
        check("bytes round-trip", blob == PNG and meta["mime"] == "image/png")
        check("other places unaffected", store.list_menu_files("Elsewhere") == [])
        check("delete works", store.delete_menu_file(file_id))
        check("and it's gone", store.load_menu_file(file_id) == (None, None))
        key = store.stable_secret("session_secret")
        check("a session key is kept without a database too",
              key and store.stable_secret("session_secret") == key)
    finally:
        os.environ["DATABASE_URL"] = saved
        store.reset_for_tests()


def test_concurrency(srv, D):
    """The reason the database exists: two people ordering in the same second."""
    section("TWENTY PEOPLE ORDER AT ONCE")
    day_date = core.shift_date(D, -3)
    results = []

    def order(n):
        code, _ = srv.post("/api/public/order", date=day_date,
                           name=f"Person{n:02d}", item="Plate", method="cash")
        results.append(code)

    threads = [threading.Thread(target=order, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    _, raw = srv.get(f"/api/public/day?date={day_date}")
    landed = len(json.loads(raw)["orders"])
    check("all 20 accepted", results.count(200) == 20, f"{results.count(200)}/20")
    check("all 20 actually stored", landed == 20, f"{landed}/20 in the database")


def main():
    print("Running against a temporary database — data/ is never opened.")
    try:
        test_money()
        test_totals()
        test_audited_day()
        test_grouping_and_learning()
        test_same_dish()
        test_merge_keeps_money()
        test_receipt_check()
        test_forward_comparison_is_exact()
        test_surcharge_follows_the_restaurant()
        test_menu_link_safety()
        test_markup_ids()
        test_deploy_safety()
        test_bar()
        test_no_drift()
        test_storage_parity()
        test_encoding()
        srv = Server()
        try:
            D = core.today_str()
            test_web(srv, D)
            test_same_name(srv, D)
            test_menu_files(srv, D)
            test_wording_prompt(srv, D)
            test_drinks(srv, D)
            test_lines(srv, D)
            test_settle(srv, D)
            test_concurrency(srv, D)
            test_login_claims_day(srv, D)   # claims today
            test_staying_logged_in(srv, D)
            test_password(srv, D)           # LAST: changes the shared password
        finally:
            srv.stop()
        test_menu_files_on_disk()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
