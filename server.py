"""Local web UI for the lunch calculator. Run: python server.py

Serves on 127.0.0.1 only -- nothing is exposed to the network.
All money arithmetic stays in lunchcore; the browser only renders what it is sent.
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import lunchcore as core

WEB_DIR = Path(__file__).parent / "web"
FIRST_PORT = 8420
TYPES = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
         ".svg": "image/svg+xml", ".ico": "image/x-icon"}


# --- view model ------------------------------------------------------------
# One place builds everything the page shows, so no number is computed twice.

def money(cents):
    return core.format_cents(cents)


def person_view(order):
    subtotal = core.subtotal_of(order)
    missing = core.unpriced_in(order)
    paid = order.get("paid_cents")
    view = {
        "name": order["name"],
        "items": [i["desc"] for i in order["items"]],
        # desc + price together, so the row editor can drive a price field
        "item_rows": [{"desc": i["desc"],
                       "price": money(i["price_cents"]) if i["price_cents"] is not None else ""}
                      for i in order["items"]],
        "subtotal": money(subtotal),
        "paid": money(paid) if paid is not None else None,
        "unpriced": missing,
        "change_given": order.get("change_given", False),
        "method": core.method_of(order),
    }
    if missing:
        view.update(owed=None, change=None, status="needs price")
        return view
    owed = core.owed_dollars(subtotal)
    view["owed"] = owed
    if paid is None:
        view.update(change=None, status="unpaid")
        return view
    change = paid - owed * 100
    view.update(change=money(change), change_cents=change,
                status="short" if change < 0 else "paid")
    return view


def load_resolved(day_date):
    """Load a day, filling in an unset restaurant method from the most recent
    day that chose one. Saving then locks that day's own value in."""
    day = core.load_day(day_date)
    if day.get("restaurant_method") not in ("cash", "card"):
        day["restaurant_method"] = core.inherited_restaurant_method(day_date)
    return day


def menu_suggestions(place):
    """Everything ever typed, this place's items first then everything else.
    Cross-place items are included so a suggestion is never simply missing."""
    menus = core.load_menus()
    ordered = [menus.get(place, [])] + [v for k, v in sorted(menus.items()) if k != place]
    seen, names = set(), []
    for group in ordered:
        for entry in group:
            key = entry["desc"].casefold()
            if key not in seen:
                seen.add(key)
                names.append(entry["desc"])
    return names


def day_view(day):
    t = core.totals(day)
    groups = core.group_items(day)
    return {
        "date": day["date"],
        "place": day["place"],
        "suggestions": menu_suggestions(day["place"]),
        "people": [person_view(o) for o in day["orders"]],
        "groups": [{"desc": g["desc"], "count": g["count"], "names": g["names"],
                    "mixed": g["mixed"],
                    "price": money(g["price_cents"]) if g["price_cents"] is not None else ""}
                   for g in groups],
        "receipt": money(day["receipt_cents"]) if day.get("receipt_cents") is not None else "",
        "restaurant_paid": (money(day["restaurant_paid_cents"])
                            if day.get("restaurant_paid_cents") is not None else ""),
        "restaurant_method": core.restaurant_method_of(day),
        "totals": {
            "people": t["people"], "unpaid": t["unpaid"], "unpriced": t["unpriced"],
            "items": money(t["items_cents"]),
            "bill": money(t["bill_cents"]), "bill_cents": t["bill_cents"],
            "collected": money(t["collected_cents"]),
            "change_out": money(t["change_out_cents"]),
            "cash_left": money(t["cash_left_cents"]),
            "surplus": money(abs(t["surplus_cents"])),
            "short": t["surplus_cents"] < 0,
            # --- cash vs venmo ---
            "cash_in": money(t["cash_in_cents"]),
            "venmo_in": money(t["venmo_in_cents"]),
            "cash_change": money(t["cash_change_cents"]),
            "venmo_change": money(t["venmo_change_cents"]),
            "cash_on_hand": money(t["cash_on_hand_cents"]),
            "venmo_held": money(t["venmo_held_cents"]),
            "due": money(t["due_cents"]),
            "has_receipt": t["has_receipt"],
            "cash_short": money(t["cash_short_cents"]),
            "cash_short_cents": t["cash_short_cents"],
            "by_card": t["restaurant_method"] == "card",
            "card_charged": (money(t["card_charged_cents"])
                             if t["card_charged_cents"] is not None else ""),
            "pocket_short": t["pocket_cents"] < 0,
            "pocket_abs": money(abs(t["pocket_cents"])),
            "restaurant_change": (money(t["restaurant_change_cents"])
                                  if t["restaurant_change_cents"] is not None else ""),
            "pocket": money(t["pocket_cents"]),
            "net_surplus": money(abs(t["net_surplus_cents"])),
            "net_short": t["net_surplus_cents"] < 0,
            "any_venmo": t["venmo_in_cents"] > 0,
        },
        "priced_groups": sum(1 for g in groups if g["price_cents"] is not None),
        "total_groups": len(groups),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "Lunch"

    def log_message(self, *args):
        pass  # the console is for the user, not a request log

    # --- plumbing ----------------------------------------------------------

    def _send(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(json.dumps(payload).encode("utf-8"), "application/json", status)

    def _fail(self, message, status=400):
        self._json({"error": message}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _day(self, params):
        date = (params.get("date") or [core.today_str()])[0]
        return load_resolved(core.parse_date(date))

    # --- GET ---------------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        try:
            if url.path == "/api/day":
                return self._json(day_view(self._day(params)))
            if url.path == "/api/days":
                return self._json({"days": core.list_saved_dates()})
            if url.path == "/api/menu":
                return self._json({"items": menu_suggestions((params.get("place") or [""])[0])})
            if url.path.startswith("/api/"):
                return self._fail("unknown endpoint", 404)
            return self._static(url.path)
        except ValueError as err:
            self._fail(str(err))
        except Exception as err:  # never take the server down mid-lunch
            self._fail(f"{type(err).__name__}: {err}", 500)

    def _static(self, path):
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if not target.is_file() or WEB_DIR.resolve() not in target.parents:
            return self._fail("not found", 404)  # keeps ../lunchcore.py out of reach
        self._send(target.read_bytes(), TYPES.get(target.suffix, "text/plain"))

    # --- POST --------------------------------------------------------------

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            handler = ACTIONS.get(url.path)
            if handler is None:
                return self._fail("unknown endpoint", 404)

            body = self._body()
            day = load_resolved(core.parse_date(body.get("date") or core.today_str()))
            handler(day, body)
            core.save_day(day)
            self._json(day_view(day))
        except ValueError as err:
            self._fail(str(err))
        except Exception as err:
            self._fail(f"{type(err).__name__}: {err}", 500)


# --- actions ---------------------------------------------------------------

def find_order(day, name):
    key = (name or "").strip().casefold()
    for order in day["orders"]:
        if order["name"].casefold() == key:
            return order
    return None


def require_order(day, body):
    order = find_order(day, body.get("name"))
    if order is None:
        raise ValueError(f"no order for {body.get('name')!r}")
    return order


def act_order(day, body):
    name = (body.get("name") or "").strip()
    desc = (body.get("item") or "").strip()
    if not name:
        raise ValueError("Enter a name")
    if not desc:
        raise ValueError("Enter an item")

    price = None
    if (body.get("price") or "").strip():
        price = core.parse_price(body["price"])
    else:
        # fall back to what this place charged last time, so step 3 arrives pre-filled
        for item in core.load_menus().get(day["place"], []):
            if item["desc"].casefold() == desc.casefold():
                price = item["price_cents"]
                break

    order = find_order(day, name)
    if order is None:
        order = {"name": name, "items": [], "paid_cents": None}
        day["orders"].append(order)
    order["items"].append({"desc": desc, "price_cents": price})

    if (body.get("paid") or "").strip():
        order["paid_cents"] = core.parse_price(body["paid"])
    if body.get("method"):
        order["method"] = parse_method(body["method"])

    menus = core.load_menus()
    core.learn_item(menus, day["place"], desc, price)
    core.save_menus(menus)


def act_price(day, body):
    desc = (body.get("desc") or "").strip()
    if not desc:
        raise ValueError("Which item?")
    raw = (body.get("price") or "").strip()
    price = core.parse_price(raw) if raw else None
    core.set_price_for_desc(day, desc, price)
    menus = core.load_menus()
    core.learn_item(menus, day["place"], desc, price)
    core.save_menus(menus)


def parse_method(value):
    if value not in ("cash", "venmo"):
        raise ValueError(f"unknown payment method {value!r}")
    return value


def act_payment(day, body):
    order = require_order(day, body)
    raw = (body.get("paid") or "").strip()
    order["paid_cents"] = core.parse_price(raw) if raw else None
    if body.get("method"):
        order["method"] = parse_method(body["method"])


def act_method(day, body):
    require_order(day, body)["method"] = parse_method(body.get("method"))


def act_receipt(day, body):
    """What the bill came to, how it was paid, and any cash handed over."""
    if body.get("method"):
        value = body["method"]
        if value not in ("cash", "card"):
            raise ValueError(f"unknown restaurant payment method {value!r}")
        day["restaurant_method"] = value
    for field, key in (("receipt", "receipt_cents"),
                       ("restaurant_paid", "restaurant_paid_cents")):
        if field in body:
            raw = (body.get(field) or "").strip()
            day[key] = core.parse_price(raw) if raw else None


def act_edit_person(day, body):
    """Rename someone and replace their whole item list in one go, so a partial
    edit can never be written."""
    order = require_order(day, body)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise ValueError("Enter a name")
    clash = find_order(day, new_name)
    if clash is not None and clash is not order:
        raise ValueError(f"{new_name} is already on the list")

    rows = body.get("items")
    if not isinstance(rows, list):
        raise ValueError("items must be a list")

    # Parse everything before touching the order, so a bad row aborts cleanly.
    parsed = []
    for row in rows:
        desc = (row.get("desc") or "").strip()
        if not desc:
            raise ValueError("Every item needs a name")
        raw = (row.get("price") or "").strip()
        parsed.append({"desc": desc, "price_cents": core.parse_price(raw) if raw else None})

    order["name"] = new_name
    order["items"] = parsed

    menus = core.load_menus()
    for item in parsed:
        core.learn_item(menus, day["place"], item["desc"], item["price_cents"])
    core.save_menus(menus)


def act_change_given(day, body):
    require_order(day, body)["change_given"] = bool(body.get("given"))


def act_place(day, body):
    day["place"] = (body.get("place") or "").strip()


def act_remove_item(day, body):
    order = require_order(day, body)
    index = int(body.get("index", -1))
    if not 0 <= index < len(order["items"]):
        raise ValueError("no such item")
    order["items"].pop(index)


def act_delete_person(day, body):
    day["orders"].remove(require_order(day, body))


ACTIONS = {
    "/api/order": act_order,
    "/api/price": act_price,
    "/api/payment": act_payment,
    "/api/method": act_method,
    "/api/receipt": act_receipt,
    "/api/change-given": act_change_given,
    "/api/place": act_place,
    "/api/remove-item": act_remove_item,
    "/api/edit-person": act_edit_person,
    "/api/delete-person": act_delete_person,
}


def build_server(port=FIRST_PORT, tries=20):
    """First free port from FIRST_PORT up, so a stale instance can't block startup."""
    for candidate in range(port, port + tries):
        try:
            return ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
        except OSError:
            continue
    raise SystemExit(f"No free port in {port}-{port + tries}")


def main():
    server = build_server()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"\n  Lunch Calculator running at {url}")
    print("  Close this window (or click Quit in the page) to stop.\n")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("  Stopped.")


if __name__ == "__main__":
    main()
