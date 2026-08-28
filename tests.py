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
    check("order keys are name/items/method/venmo_user",
          keys == ["items", "method", "name", "venmo_user"], str(keys))
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

    section("THE PRICE BOX STILL CLEARS THE $")
    check("padding rule is specific enough to survive the shorthand",
          "input.editPrice" in css)


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
        test_storage_parity()
        srv = Server()
        try:
            D = core.today_str()
            test_web(srv, D)
            test_same_name(srv, D)
            test_menu_files(srv, D)
            test_concurrency(srv, D)
            test_login_claims_day(srv, D)   # last: it claims today
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
