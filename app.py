"""Lunch ordering site.

Two audiences:
  /       coworkers add their own order -- names and items only, no money
  /admin  the organiser: prices, receipt, settlement. Password protected.

Money never crosses to the public side. That is enforced here, in what the
public endpoints serialise, not in the templates -- see `public_view`.
"""

import hmac
import os
import secrets
import threading
from functools import wraps

from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   session, url_for)

import lunchcore as core
import store

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
# Menu photos come off phones. Anything larger than this is a mistake.
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
MENU_FILE_LIMIT = 6          # per place

def _local_password():
    """The password for local use, kept OUT of this repo.

    Generated on first run and stored under data/, which is git-ignored in
    full. That way the published source contains no working password at all --
    not even a local one. Edit that file to choose your own.
    """
    path = core.DATA_DIR / "admin_password.txt"
    try:
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    except OSError:
        pass                                  # no file yet, or unreadable
    generated = secrets.token_urlsafe(9)
    core.DATA_DIR.mkdir(exist_ok=True)
    path.write_text(generated + "\n", encoding="utf-8")
    print(f"\n  Organiser password: {generated}")
    print(f"  Saved in {path}")
    print("  Edit that file to pick your own.\n")
    return generated


# The repo is public, so it must contain no usable password anywhere. Deployed,
# ADMIN_PASSWORD has to be set or the admin side stays shut; locally, the
# password lives in a git-ignored file rather than in this source.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ""
if not ADMIN_PASSWORD:
    if os.environ.get("DATABASE_URL"):
        ADMIN_PASSWORD = secrets.token_urlsafe(32)   # unknowable => nobody gets in
        print("ADMIN_PASSWORD is not set — admin access is disabled. "
              "Set it in the host's environment settings.")
    else:
        ADMIN_PASSWORD = _local_password()


@app.errorhandler(Exception)
def unhandled(err):
    """API callers get JSON, never a stack-trace page. A corrupt day file or a
    dropped database connection shouldn't take the page down mid-lunch."""
    from werkzeug.exceptions import HTTPException
    if isinstance(err, HTTPException):
        return err
    app.logger.exception("unhandled")
    if request.path.startswith("/api/"):
        return jsonify({"error": f"{type(err).__name__}: {err}"}), 500
    return render_template("login.html", error="Something went wrong"), 500


# --- auth ------------------------------------------------------------------

def is_admin():
    return session.get("admin") is True


def admin_required(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not is_admin():
            if request.method == "POST" or request.path.startswith("/api/"):
                return jsonify({"error": "Admin only"}), 403
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return guarded


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    name = session.get("admin_name", "")
    if request.method == "POST":
        supplied = request.form.get("password", "")
        name = (request.form.get("name") or "").strip()
        # The name is a label, not a second credential -- the password is still
        # the only thing that grants access.
        if not name:
            error = "Enter your name"
        elif hmac.compare_digest(supplied, ADMIN_PASSWORD):
            session["admin"] = True
            session["admin_name"] = name
            session.permanent = True
            # Claim today straight away, so coworkers ordering first thing see a
            # real name rather than "the organiser". Set-if-empty, so whoever
            # logs in first keeps the day. Safe to take the write lock here --
            # login is not inside another transaction.
            with store.edit_day(core.today_str()) as day:
                if not day.get("organiser"):
                    day["organiser"] = name
            return redirect(request.args.get("next") or url_for("admin"))
        else:
            error = "Wrong password"
    return (render_template("login.html", error=error, name=name),
            200 if not error else 401)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("admin", None)
    session.pop("admin_name", None)
    return redirect(url_for("index"))


# --- view models -----------------------------------------------------------

def money(cents):
    return core.format_cents(cents)


def public_view(day):
    """Everything the public page is allowed to know. Deliberately contains no
    price, subtotal, owed, paid or total -- forwarding the link leaks nothing."""
    return {
        "date": day["date"],
        "place": day["place"],
        "locked": bool(day.get("locked")),
        "organiser": day.get("organiser", ""),
        "menu": menu_view(day["place"]),
        "orders": [{"name": o["name"],
                    "items": [i["desc"] for i in o["items"]],
                    "method": core.method_of(o),
                    "venmo_user": o.get("venmo_user", "")}
                   for o in day["orders"]],
        # No menu suggestions here on purpose. The dropdown read as a menu you
        # had to pick from, and it handed every anonymous visitor a list of
        # every dish ever ordered anywhere. The admin box keeps its own.
    }


def menu_suggestions(place):
    """Everything ever typed, this place's items first then everything else."""
    menus = store.load_menus()
    ordered = [menus.get(place, [])] + [v for k, v in sorted(menus.items()) if k != place]
    seen, names = set(), []
    for group in ordered:
        for entry in group:
            key = entry["desc"].casefold()
            if key not in seen:
                seen.add(key)
                names.append(entry["desc"])
    return names


def venmo_link(person, place, owed):
    if not person.get("venmo_user"):
        return ""
    handle = person["venmo_user"].lstrip("@")
    note = f"Lunch {place}".strip()
    return (f"https://venmo.com/{handle}?txn=charge&amount={owed}"
            f"&note={note.replace(' ', '%20')}")


def person_view(order, place):
    subtotal = core.subtotal_of(order)
    missing = core.unpriced_in(order)
    paid = order.get("paid_cents")
    view = {
        "name": order["name"],
        "items": [i["desc"] for i in order["items"]],
        "item_rows": [{"desc": i["desc"],
                       "price": money(i["price_cents"]) if i["price_cents"] is not None else ""}
                      for i in order["items"]],
        "subtotal": money(subtotal),
        "paid": money(paid) if paid is not None else None,
        "unpriced": missing,
        "change_given": order.get("change_given", False),
        "method": core.method_of(order),
        "venmo_user": order.get("venmo_user", ""),
    }
    if missing:
        view.update(owed=None, change=None, status="needs price", venmo_link="")
        return view
    owed = core.owed_dollars(subtotal)
    view["owed"] = owed
    view["venmo_link"] = (venmo_link(order, place, owed)
                          if core.method_of(order) == "venmo" else "")
    if paid is None:
        view.update(change=None, status="unpaid")
        return view
    change = paid - owed * 100
    view.update(change=money(change), status="short" if change < 0 else "paid")
    return view


def admin_view(day):
    t = core.totals(day)
    groups = core.group_items(day)
    return {
        "date": day["date"],
        "place": day["place"],
        "locked": bool(day.get("locked")),
        "menu": menu_view(day["place"]),
        "suggestions": menu_suggestions(day["place"]),
        "people": [person_view(o, day["place"]) for o in day["orders"]],
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
            "restaurant_change": (money(t["restaurant_change_cents"])
                                  if t["restaurant_change_cents"] is not None else ""),
            "pocket": money(t["pocket_cents"]),
            "pocket_short": t["pocket_cents"] < 0,
            "pocket_abs": money(abs(t["pocket_cents"])),
            "net_surplus": money(abs(t["net_surplus_cents"])),
            "net_short": t["net_surplus_cents"] < 0,
            "any_venmo": t["venmo_in_cents"] > 0,
        },
        "priced_groups": sum(1 for g in groups if g["price_cents"] is not None),
        "total_groups": len(groups),
    }


_pending = threading.local()


def queue_learn(place, desc, price_cents):
    """Remember an item AFTER the day transaction closes.

    Admin handlers run inside store.edit_day()'s write transaction; writing to
    the menu from in there would take the write lock a second time on the same
    thread and deadlock. So learning is queued and flushed once the day commits.
    """
    items = getattr(_pending, "learn", None)
    if items is None:
        items = _pending.learn = []
    items.append((place, desc, price_cents))


def flush_learn():
    items = getattr(_pending, "learn", [])
    _pending.learn = []
    for place, desc, price_cents in items:
        store.learn_item(place, desc, price_cents)


def resolved(day_date):
    """Fill in an unset restaurant method from the most recent day that chose one."""
    day = store.load_day(day_date)
    if day.get("restaurant_method") not in ("cash", "card"):
        day["restaurant_method"] = store.inherited_restaurant_method(day_date)
    return day


def find_order(day, name):
    key = (name or "").strip().casefold()
    for order in day["orders"]:
        if order["name"].casefold() == key:
            return order
    return None


def wanted_date():
    raw = request.args.get("date") or (request.get_json(silent=True) or {}).get("date")
    return core.parse_date(raw) if raw else core.today_str()


# --- pages -----------------------------------------------------------------

@app.route("/")
def index():
    return render_template("order.html")


@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html")


@app.route("/history")
@admin_required
def history():
    return render_template("history.html")


# --- menu files ------------------------------------------------------------

def sniff_type(blob):
    """The real type, read from the bytes themselves.

    Never trust the browser's Content-Type or the file extension here: these
    bytes get served back to other people from our own origin, so an HTML file
    called menu.jpg would otherwise run as a page on this site.
    """
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:5] == b"%PDF-":
        return "application/pdf"
    return None


def menu_view(place):
    """What the pages need to show a menu: never the bytes."""
    if not place:
        return []
    return [{"id": f["id"], "filename": f["filename"],
             "kind": "pdf" if f["mime"] == "application/pdf" else "image"}
            for f in store.list_menu_files(place)]


@app.get("/menu/<file_id>")
def menu_file(file_id):
    """Public: coworkers have to be able to read the menu."""
    meta, blob = store.load_menu_file(file_id)
    if meta is None:
        return jsonify({"error": "No such file"}), 404
    return Response(blob, mimetype=meta["mime"], headers={
        # The stored mime came from sniff_type, not from the uploader.
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
        # Ids are unique per upload, so a cached copy can never be stale.
        "Cache-Control": "public, max-age=86400",
    })


@app.post("/api/menu-file")
@admin_required
def upload_menu_file():
    place = (request.form.get("place") or "").strip()
    if not place:
        return jsonify({"error": "Set the Place first — menus are saved "
                                 "per restaurant."}), 400

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Pick a file"}), 400

    blob = upload.read()
    if not blob:
        return jsonify({"error": "That file is empty"}), 400

    mime = sniff_type(blob)
    if mime is None:
        return jsonify({"error": "That isn't a photo or a PDF"}), 400

    if len(store.list_menu_files(place)) >= MENU_FILE_LIMIT:
        return jsonify({"error": f"{place} already has {MENU_FILE_LIMIT} menu "
                                 "files — remove one first."}), 400

    name = os.path.basename(upload.filename)[:120]
    store.save_menu_file(place, name, mime, blob)
    return jsonify({"menu": menu_view(place)})


@app.post("/api/menu-file/delete")
@admin_required
def remove_menu_file():
    body = request.get_json(silent=True) or {}
    file_id = (body.get("id") or "").strip()
    place = (body.get("place") or "").strip()
    if not store.delete_menu_file(file_id):
        return jsonify({"error": "No such file"}), 404
    return jsonify({"menu": menu_view(place)})


# --- public API ------------------------------------------------------------

@app.get("/api/public/day")
def api_public_day():
    try:
        return jsonify(public_view(store.load_day(wanted_date())))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400


@app.post("/api/public/order")
def api_public_order():
    """Anyone may add or change THEIR OWN order, until the day is closed."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    desc = (body.get("item") or "").strip()
    method = body.get("method") or "cash"
    venmo_user = (body.get("venmo_user") or "").strip()

    if not name:
        return jsonify({"error": "Enter your name"}), 400
    if not desc:
        return jsonify({"error": "Enter what you want"}), 400
    if method not in ("cash", "venmo"):
        return jsonify({"error": "Pick cash or Venmo"}), 400

    try:
        day_date = wanted_date()
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    # Read menus before opening the write transaction, so no second connection
    # is needed while the day row is locked.
    menus = store.load_menus()
    place = store.load_day(day_date)["place"]

    with store.edit_day(day_date) as day:
        if day.get("locked"):
            return jsonify({"error": "Orders are closed for today"}), 409

        order = find_order(day, name)
        if order is not None and body.get("confirm") != "add":
            # Two coworkers can share a first name. Merging them would put both
            # on one rounded total -- billed as one person, and the pair pays
            # about a dollar less than they owe. Ask instead of guessing.
            return jsonify({"error": "name_taken", "name": order["name"],
                            "items": [i["desc"] for i in order["items"]]}), 409
        if order is None:
            order = {"name": name, "items": [], "paid_cents": None}
            day["orders"].append(order)
        order["items"].append({"desc": desc,
                               "price_cents": _price_from(menus, day["place"], desc)})
        order["method"] = method
        if method == "venmo" and venmo_user:
            order["venmo_user"] = venmo_user

    store.learn_item(place, desc, None)
    return jsonify(public_view(store.load_day(day_date)))


def _price_from(menus, place, desc):
    for item in menus.get(place, []):
        if item["desc"].casefold() == desc.casefold():
            return item["price_cents"]
    return None


def _remembered_price(day, desc):
    return _price_from(store.load_menus(), day["place"], desc)


@app.post("/api/public/remove")
def api_public_remove():
    """Remove one of your own items. Index is within your own order only, so
    there is no way to reach someone else's row."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    index = body.get("index")

    try:
        day_date = wanted_date()
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    with store.edit_day(day_date) as day:
        if day.get("locked"):
            return jsonify({"error": "Orders are closed for today"}), 409
        order = find_order(day, name)
        if order is None:
            return jsonify({"error": "No order under that name"}), 404
        if not isinstance(index, int) or not 0 <= index < len(order["items"]):
            return jsonify({"error": "No such item"}), 400
        order["items"].pop(index)
        if not order["items"] and order.get("paid_cents") is None:
            day["orders"].remove(order)

    return jsonify(public_view(store.load_day(day_date)))


# --- admin API -------------------------------------------------------------

@app.get("/api/day")
@admin_required
def api_day():
    try:
        return jsonify(admin_view(resolved(wanted_date())))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400


@app.get("/api/days")
@admin_required
def api_days():
    return jsonify({"days": store.list_saved_dates()})


@app.get("/api/history")
@admin_required
def api_history():
    rows, by_person, by_place = [], {}, {}
    for day_date in store.list_saved_dates():
        day = resolved(day_date)
        if not day["orders"]:
            continue          # logging in creates the day; an empty one says nothing
        t = core.totals(day)
        rows.append({"date": day_date, "place": day["place"],
                     "organiser": day.get("organiser", ""),
                     "people": t["people"], "items": money(t["items_cents"]),
                     "bill": money(t["bill_cents"]),
                     "surplus": money(t["net_surplus_cents"])})
        by_place[day["place"] or "(none)"] = (by_place.get(day["place"] or "(none)", 0)
                                              + t["items_cents"])
        for order in day["orders"]:
            by_person[order["name"]] = by_person.get(order["name"], 0) + core.subtotal_of(order)
    return jsonify({
        "days": rows,
        "people": sorted(({"name": n, "total": money(c)} for n, c in by_person.items()),
                         key=lambda r: r["name"].casefold()),
        "places": sorted(({"place": p, "total": money(c)} for p, c in by_place.items()),
                         key=lambda r: r["place"].casefold()),
    })


def _admin_mutation(handler, note_ok=None):
    body = request.get_json(silent=True) or {}
    try:
        day_date = wanted_date()
        inherited = store.inherited_restaurant_method(day_date)
        with store.edit_day(day_date) as day:
            if day.get("restaurant_method") not in ("cash", "card"):
                day["restaurant_method"] = inherited
            # Whoever first works a day owns it. Set-if-empty, so opening an old
            # day later cannot rewrite who actually ran it. Rides the open
            # transaction -- taking another write lock here would deadlock.
            if not day.get("organiser"):
                day["organiser"] = session.get("admin_name", "")
            handler(day, body)
        flush_learn()                      # menu writes, now the day has committed
        return jsonify(admin_view(resolved(day_date)))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400


def require_order(day, body):
    order = find_order(day, body.get("name"))
    if order is None:
        raise ValueError(f"no order for {body.get('name')!r}")
    return order


def parse_method(value):
    if value not in ("cash", "venmo"):
        raise ValueError(f"unknown payment method {value!r}")
    return value


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
        price = _remembered_price(day, desc)

    order = find_order(day, name)
    if order is None:
        order = {"name": name, "items": [], "paid_cents": None}
        day["orders"].append(order)
    order["items"].append({"desc": desc, "price_cents": price})
    if (body.get("paid") or "").strip():
        order["paid_cents"] = core.parse_price(body["paid"])
    if body.get("method"):
        order["method"] = parse_method(body["method"])

    queue_learn(day["place"], desc, price)


def act_price(day, body):
    desc = (body.get("desc") or "").strip()
    if not desc:
        raise ValueError("Which item?")
    raw = (body.get("price") or "").strip()
    price = core.parse_price(raw) if raw else None
    core.set_price_for_desc(day, desc, price)
    queue_learn(day["place"], desc, price)


def act_payment(day, body):
    order = require_order(day, body)
    raw = (body.get("paid") or "").strip()
    order["paid_cents"] = core.parse_price(raw) if raw else None
    if body.get("method"):
        order["method"] = parse_method(body["method"])


def act_method(day, body):
    require_order(day, body)["method"] = parse_method(body.get("method"))


def act_venmo_user(day, body):
    require_order(day, body)["venmo_user"] = (body.get("venmo_user") or "").strip()


def act_receipt(day, body):
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


def act_change_given(day, body):
    require_order(day, body)["change_given"] = bool(body.get("given"))


def act_place(day, body):
    day["place"] = (body.get("place") or "").strip()


def act_lock(day, body):
    day["locked"] = bool(body.get("locked"))


def act_remove_item(day, body):
    order = require_order(day, body)
    index = int(body.get("index", -1))
    if not 0 <= index < len(order["items"]):
        raise ValueError("no such item")
    order["items"].pop(index)


def act_edit_person(day, body):
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

    parsed = []
    for row in rows:
        desc = (row.get("desc") or "").strip()
        if not desc:
            raise ValueError("Every item needs a name")
        raw = (row.get("price") or "").strip()
        parsed.append({"desc": desc, "price_cents": core.parse_price(raw) if raw else None})

    order["name"] = new_name
    order["items"] = parsed
    if "venmo_user" in body:
        order["venmo_user"] = (body.get("venmo_user") or "").strip()

    for item in parsed:
        queue_learn(day["place"], item["desc"], item["price_cents"])


def act_delete_person(day, body):
    day["orders"].remove(require_order(day, body))


ADMIN_ACTIONS = {
    "order": act_order, "price": act_price, "payment": act_payment,
    "method": act_method, "venmo-user": act_venmo_user, "receipt": act_receipt,
    "change-given": act_change_given, "place": act_place, "lock": act_lock,
    "remove-item": act_remove_item, "edit-person": act_edit_person,
    "delete-person": act_delete_person,
}


@app.post("/api/<action>")
@admin_required
def api_admin(action):
    handler = ADMIN_ACTIONS.get(action)
    if handler is None:
        return jsonify({"error": "unknown endpoint"}), 404
    return _admin_mutation(handler)


if __name__ == "__main__":
    # Loopback unless HOST says otherwise, so the local app stays off the network
    # by default. sandbox.py opts in to 0.0.0.0 for testing from a phone.
    app.run(host=os.environ.get("HOST") or "127.0.0.1",
            port=int(os.environ.get("PORT", 8420)), debug=False)
