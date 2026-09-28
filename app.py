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
import time
from collections import Counter
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, Response, jsonify, make_response, redirect, render_template,
                   request, session, url_for)
from flask.sessions import SecureCookieSessionInterface

import lunchcore as core
import store


class _Sessions(SecureCookieSessionInterface):
    """Sessions signed with SECRET_KEY -- or, if the host never set one, with
    a key made once and kept in the store.

    A fresh random key at every start logs every organiser out whenever the
    process restarts, and the free host restarts after every quiet spell; it
    would also split the two workers, each signing with its own. Looked up on
    first use rather than at import, so importing the app touches no storage.
    """

    def get_signing_serializer(self, app):
        if not app.secret_key:
            app.secret_key = store.stable_secret("session_secret")
        return super().get_signing_serializer(app)


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or None
app.session_interface = _Sessions()
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
    # Never the login form. This used to show it, so a database still waking
    # up on a quiet morning looked exactly like having been logged out.
    return render_template("error.html"), 500


# --- auth ------------------------------------------------------------------
# The password can be changed from the site. Once one has been set it lives in
# the settings store as a scrypt hash, and the environment/local password stops
# working. Until then, nothing here changes how login behaves.

PASSWORD_HASH_KEY = "admin_password_hash"
PASSWORD_STAMP_KEY = "password_changed_at"
MIN_PASSWORD_LENGTH = 8


def password_stamp():
    """When the password last changed, or "" if never. Carried in each session
    so that changing the password logs every OTHER device out -- a signed
    cookie would otherwise stay valid for thirty days, including on whichever
    device the change was meant to shut out."""
    try:
        return store.get_setting(PASSWORD_STAMP_KEY) or ""
    except Exception:
        # Every organiser page reads this first, so it is what meets a
        # database still waking after a quiet night. One retry keeps that from
        # turning the day's first page into an error.
        time.sleep(0.5)
        return store.get_setting(PASSWORD_STAMP_KEY) or ""


def password_ok(supplied):
    """Does this password open the organiser side right now?

    Three doors, tried in order:
      1. ADMIN_PASSWORD_RESET in the environment -- the recovery path. Set it
         on the host, log in with that value, and the stored hash is wiped so
         a new password can be set. Remove the variable afterwards.
      2. A password set from the site (stored as a hash).
      3. The environment / local-file password, only while none has been set.
    """
    from werkzeug.security import check_password_hash
    reset = os.environ.get("ADMIN_PASSWORD_RESET") or ""
    if reset and hmac.compare_digest(supplied, reset):
        store.set_setting(PASSWORD_HASH_KEY, None)
        store.set_setting(PASSWORD_STAMP_KEY, _now_stamp())
        return True
    stored = store.get_setting(PASSWORD_HASH_KEY)
    if stored:
        return check_password_hash(stored, supplied)
    return hmac.compare_digest(supplied, ADMIN_PASSWORD)


def set_password(new):
    """Store a new password. Never the password itself: a one-way hash, so
    even someone with the database cannot read it back."""
    from werkzeug.security import generate_password_hash
    store.set_setting(PASSWORD_HASH_KEY, generate_password_hash(new))
    stamp = _now_stamp()
    store.set_setting(PASSWORD_STAMP_KEY, stamp)
    return stamp


def _now_stamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def is_admin():
    if session.get("admin") is not True:
        return False
    # A session from before the last password change is no longer good.
    return session.get("pw_stamp", "") == password_stamp()


def admin_required(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not is_admin():
            if request.method == "POST" or request.path.startswith("/api/"):
                return jsonify({"error": "Admin only"}), 403
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return guarded


# The login briefly remembered the organiser's name in this cookie. It no
# longer does -- the name box starts empty on every load -- and any copy that
# version left in a browser is deleted whenever the login page answers, so no
# name lingers on a shared machine.
OLD_NAME_COOKIE = "organiser_name"


def _next_page():
    """Where to go after logging in: a path on this site, never elsewhere --
    otherwise a crafted link could send a fresh login off to any address."""
    target = request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("admin")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    # Empty on every load, a refresh included. The only name ever shown is the
    # one typed a moment ago, after a wrong password, so a typo in the
    # password doesn't mean typing the name again too.
    name = ""
    if request.method == "POST":
        supplied = request.form.get("password", "")
        name = (request.form.get("name") or "").strip()
        # The name is a label, not a second credential -- the password is still
        # the only thing that grants access.
        if not name:
            error = "Enter your name"
        elif password_ok(supplied):
            session["admin"] = True
            session["admin_name"] = name
            session["pw_stamp"] = password_stamp()
            session.permanent = True
            # Claim today straight away, so coworkers ordering first thing see a
            # real name rather than "the organiser". Set-if-empty, so whoever
            # logs in first keeps the day. Safe to take the write lock here --
            # login is not inside another transaction.
            with store.edit_day(core.today_str()) as day:
                if not day.get("organiser"):
                    day["organiser"] = name
            response = redirect(_next_page())
            response.delete_cookie(OLD_NAME_COOKIE)
            return response
        else:
            error = "Wrong password"
    response = make_response(render_template("login.html", error=error, name=name),
                             200 if not error else 401)
    response.delete_cookie(OLD_NAME_COOKIE)
    return response


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("admin", None)
    session.pop("admin_name", None)
    session.pop("pw_stamp", None)
    return redirect(url_for("index"))


@app.route("/password", methods=["GET", "POST"])
@admin_required
def password():
    """Change the organiser password from the site.

    Current -> new -> new again. The current one proves you hold it, which is
    what stops someone who found a logged-in phone from quietly locking the
    real organiser out. The password itself is never shown anywhere.
    """
    error = done = None
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        again = request.form.get("again", "")
        if not password_ok(current):
            error = "That isn't the current password"
        elif len(new) < MIN_PASSWORD_LENGTH:
            error = f"Make the new one at least {MIN_PASSWORD_LENGTH} characters"
        elif new != again:
            error = "The two new passwords don't match"
        elif new == current:
            error = "That's the same as the current one"
        else:
            # Every other device is logged out by the new stamp; this one is
            # given it, so whoever made the change stays in.
            session["pw_stamp"] = set_password(new)
            done = "Password changed. Every other device has been logged out."
    return (render_template("password.html", error=error, done=done,
                            name=session.get("admin_name", "")),
            200 if not error else 400)


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
                    # Same rows with what to get if they're out of a drink.
                    # Still no price anywhere in here.
                    "rows": [item_public(i) for i in o["items"]],
                    "method": core.method_of(o),
                    "venmo_user": o.get("venmo_user", "")}
                   for o in day["orders"]],
        # What people have already ordered TODAY, commonest first, so the next
        # person can reuse the exact wording instead of inventing a fifth way
        # to say "rice plate with lamb". Still no menu_suggestions() here: the
        # old dropdown read as a menu you had to pick from and handed every
        # anonymous visitor every dish ever ordered anywhere. This is only this
        # day's orders -- already in "orders" above, so it exposes nothing new.
        "ordered_today": ordered_today(day),
    }


def merge_suggestions(day):
    """Clusters of two or more wordings that look like the same dish.

    The wording kept by default is the commonest one, tie-broken by length --
    it is the one most people already recognise on the call list.
    """
    counts = Counter(i["desc"] for o in day["orders"] for i in o["items"])
    out = []
    for cluster in core.cluster_items(counts):
        if len(cluster) < 2:
            continue
        variants = sorted(cluster, key=lambda d: (-counts[d], -len(d), d.casefold()))
        out.append({"into": variants[0],
                    "total": sum(counts[d] for d in variants),
                    "variants": [{"desc": d, "count": counts[d]} for d in variants]})
    return sorted(out, key=lambda c: -c["total"])


def item_public(item):
    """One item row as the public page sees it: words only, never a price."""
    row = {"desc": item["desc"], "drink": item.get("kind") == "drink"}
    if item.get("fallback"):
        row["fallback"] = item["fallback"]
    return row


def ordered_today(day):
    """Today's FOOD wordings with counts, commonest first, then alphabetical.

    Drinks are left out on purpose: tapping a chip fills the lunch box, and
    "Coke" in the lunch box is wrong.
    """
    counts = Counter(i["desc"] for o in day["orders"] for i in o["items"]
                     if i.get("kind") != "drink")
    return [{"desc": desc, "count": n}
            for desc, n in sorted(counts.items(), key=lambda p: (-p[1], p[0].casefold()))]


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


def dollars(cents):
    """For a Venmo amount: '32' for whole dollars, '1.50' otherwise."""
    return str(cents // 100) if cents % 100 == 0 else money(cents)


def person_view(order, place):
    subtotal = core.subtotal_of(order)
    missing = core.unpriced_in(order)
    paid = order.get("paid_cents")
    total = core.paid_total(order)
    view = {
        "name": order["name"],
        "items": [i["desc"] for i in order["items"]],
        "item_rows": [{"desc": i["desc"],
                       "price": money(i["price_cents"]) if i["price_cents"] is not None else "",
                       "drink": i.get("kind") == "drink",
                       "fallback": i.get("fallback", "")}
                      for i in order["items"]],
        "subtotal": money(subtotal),
        "paid": money(paid) if paid is not None else None,
        # Later payments made the other way -- the last $2 on Venmo after $15
        # in cash -- each shown on its own so it can be undone.
        "topups": [{"amount": money(t["cents"]), "method": t["method"]}
                   for t in order.get("topups") or []],
        "unpriced": missing,
        "change_given": order.get("change_given", False),
        "method": core.method_of(order),
        "venmo_user": order.get("venmo_user", ""),
    }
    if missing:
        view.update(owed=None, change=None, outstanding=None, status="needs price",
                    venmo_link="")
        return view
    owed = core.owed_dollars(subtotal)
    still = core.outstanding_of(order)
    view["owed"] = owed
    view["outstanding"] = money(still)
    # A charge link for what is still owed: all of it before they pay a thing,
    # just the rest after a short payment, and nothing once they are square.
    view["venmo_link"] = (venmo_link(order, place, dollars(still))
                          if core.method_of(order) == "venmo" and still else "")
    if total is None:
        view.update(change=None, status="unpaid")
        return view
    change = total - owed * 100
    view.update(change=money(change), status="short" if change < 0 else "paid")
    return view


def charge_basis(t):
    """How the till total is built, in words: '4.712% tax + 3% card fee'."""
    parts = ["4.712% tax" if t["tax_on_top"] else "tax already in the prices"]
    if t["expected_pct"]:
        parts.append(f"{t['expected_pct']:g}% card fee")
    return " + ".join(parts)


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
                    "mixed": g["mixed"], "drink": g["drink"], "fallbacks": g["fallbacks"],
                    "price": money(g["price_cents"]) if g["price_cents"] is not None else ""}
                   for g in groups],
        # Lines that look like one dish written several ways, for the merge
        # offer on the pricing screen. Suggestion only -- the organiser sees
        # every wording and confirms before anything is rewritten.
        "merge_suggestions": merge_suggestions(day),
        "receipt": money(day["receipt_cents"]) if day.get("receipt_cents") is not None else "",
        "receipt_subtotal": (money(day["receipt_subtotal_cents"])
                             if day.get("receipt_subtotal_cents") is not None else ""),
        "receipt_items": ("" if day.get("receipt_items") is None
                          else str(day["receipt_items"])),
        # Trailing zeros trimmed: 3.0 shows as "3", which is what they typed.
        "surcharge_pct": ("" if day.get("surcharge_pct") is None
                          else f"{float(day['surcharge_pct']):g}"),
        "tax_included": core.tax_included_of(day),
        "restaurant_paid": (money(day["restaurant_paid_cents"])
                            if day.get("restaurant_paid_cents") is not None else ""),
        "restaurant_method": core.restaurant_method_of(day),
        "totals": {
            "people": t["people"], "unpaid": t["unpaid"], "unpriced": t["unpriced"],
            "items": money(t["items_cents"]),
            "bill": money(t["bill_cents"]), "bill_cents": t["bill_cents"],
            "keyed_items": t["keyed_items"],
            "receipt_items_count": t["receipt_items"],
            "subtotal_diff_cents": t["subtotal_diff_cents"],
            "subtotal_diff": (None if t["subtotal_diff_cents"] is None
                              else money(abs(t["subtotal_diff_cents"]))),
            "count_diff": t["count_diff"],
            "surcharge_pct": t["surcharge_pct"],
            "expected_pct": t["expected_pct"],
            "implied_pct": t["implied_pct"],
            "charge_diff_cents": t["charge_diff_cents"],
            "charge_diff": (None if t["charge_diff_cents"] is None
                            else money(abs(t["charge_diff_cents"]))),
            "charge_ok": t["charge_ok"],
            "tax_switch": t["tax_switch"],
            "tax_on_top": t["tax_on_top"],
            "tax_part": money(t["tax_part_cents"]),
            "fee_part": (None if not t["fee_part_cents"] else money(t["fee_part_cents"])),
            "charge_basis": charge_basis(t),
            "estimate": money(t["estimate_cents"]),
            "food_diff": (None if t["food_diff_cents"] is None
                          else money(abs(t["food_diff_cents"]))),
            "food_diff_cents": t["food_diff_cents"],
            "expected_charge": (None if t["expected_charge_cents"] is None
                                else money(t["expected_charge_cents"])),
            # Owed to you by people whose items are all priced -- the part of
            # any shortfall that fixes itself once they pay.
            "owing": t["owing"],
            "outstanding": money(t["outstanding_cents"]),
            "after_collect": money(abs(t["net_surplus_cents"] + t["outstanding_cents"])),
            "after_short": t["net_surplus_cents"] + t["outstanding_cents"] < 0,
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
            "net_zero": t["net_surplus_cents"] == 0,
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
    """Fill in an unset restaurant method from the most recent day that chose one.

    The surcharge is filled in the same way, but from the last day at the SAME
    restaurant -- one place charges 3% on a card and the next charges nothing
    -- and so is whether its prices already include tax. None of it is written
    back here: this is the read path, so they are a suggestion until the
    organiser saves the receipt panel. That also keeps the lookup out of the
    write transaction, where walking other days to find it would take a second
    lock and deadlock.
    """
    day = store.load_day(day_date)
    if day.get("restaurant_method") not in ("cash", "card"):
        day["restaurant_method"] = store.inherited_restaurant_method(day_date)
    if day.get("surcharge_pct") is None:
        day["surcharge_pct"] = store.inherited_surcharge_pct(day_date, day.get("place"))
    if day.get("tax_included") is None:
        day["tax_included"] = store.inherited_tax_included(day_date, day.get("place"))
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


# A pasted link is stored as a menu_files row with this mime and the URL as its
# bytes. Reusing that table gets per-place scoping, the six-file limit, ordering
# and deletion for nothing, and needs no migration on the live database.
LINK_MIME = "text/uri-list"
MAX_URL = 2000


def safe_menu_url(raw):
    """The URL if it is safe to hand a coworker, else None.

    This is the one input in the app that another person's browser is invited
    to click, so the scheme is an allowlist and nothing else gets through. A
    'javascript:' href runs script on a page the whole office opens, and
    'data:' is a page of someone else's choosing wearing our name. Reject
    outright -- never try to clean one up.
    """
    text = (raw or "").strip()
    if not text or len(text) > MAX_URL:
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return text


def link_label(url):
    """The host, which says where a link goes without anyone typing a name."""
    host = urlparse(url).netloc.split("@")[-1].split(":")[0]
    return host[4:] if host.lower().startswith("www.") else host


def menu_view(place):
    """What the pages need to show a menu: never the bytes."""
    if not place:
        return []
    out = []
    for f in store.list_menu_files(place):
        if f["mime"] == LINK_MIME:
            meta, blob = store.load_menu_file(f["id"])
            url = safe_menu_url(blob.decode("utf-8", "replace"))
            if url is None:
                continue          # stored before a rule tightened: don't serve it
            out.append({"id": f["id"], "filename": f["filename"],
                        "kind": "link", "url": url})
        else:
            out.append({"id": f["id"], "filename": f["filename"],
                        "kind": "pdf" if f["mime"] == "application/pdf" else "image"})
    return out


@app.get("/menu/<file_id>")
def menu_file(file_id):
    """Public: coworkers have to be able to read the menu."""
    meta, blob = store.load_menu_file(file_id)
    if meta is None:
        return jsonify({"error": "No such file"}), 404
    if meta["mime"] == LINK_MIME:
        # A pasted link is never served back from our own origin -- the page
        # links straight at the destination instead.
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


@app.post("/api/menu-link")
@admin_required
def add_menu_link():
    """Some restaurants just have a web page; photographing a screen is silly."""
    body = request.get_json(silent=True) or {}
    place = (body.get("place") or "").strip()
    if not place:
        return jsonify({"error": "Set the Place first — menus are saved "
                                 "per restaurant."}), 400

    url = safe_menu_url(body.get("url"))
    if url is None:
        return jsonify({"error": "That isn't a web address. It has to start "
                                 "with http:// or https://"}), 400

    if len(store.list_menu_files(place)) >= MENU_FILE_LIMIT:
        return jsonify({"error": f"{place} already has {MENU_FILE_LIMIT} menu "
                                 "files — remove one first."}), 400

    blob = url.encode("utf-8")
    store.save_menu_file(place, link_label(url), LINK_MIME, blob)
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


# One order can hold several things. Capped on the public form only -- the one
# anybody can post to -- and far above what one person orders for one lunch.
MAX_LINES = 10
MAX_TEXT = 150


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _line(desc, fallback, kind=None):
    """One item row, before its price. A backup that just repeats the item
    means nothing and is dropped."""
    row = {"desc": desc}
    if kind:
        row["kind"] = kind
    if fallback and fallback.casefold() != desc.casefold():
        row["fallback"] = fallback
    return row


def order_lines(body, capped=False):
    """The item rows one order adds: its food lines, then an optional drink.

    Reads the lines form ({"items": [{"desc", "fallback"}, ...]}) and the old
    single box ({"item": "..."}), so a page loaded before the change still
    works. Every line may name a backup in case they're out of it; a backup
    with nothing before it is ignored. Empty lines are skipped -- the form
    always keeps a blank one at the bottom.
    """
    raw = body.get("items")
    if isinstance(raw, list):
        wanted = [line if isinstance(line, dict) else {"desc": line} for line in raw]
    else:
        wanted = [{"desc": body.get("item")}]
    rows = [_line(_text(line.get("desc")), _text(line.get("fallback")))
            for line in wanted if _text(line.get("desc"))]
    if capped and len(rows) > MAX_LINES:
        raise ValueError(f"That's more than {MAX_LINES} things — split it into two orders")
    drink = _text(body.get("drink"))
    if drink:
        rows.append(_line(drink, _text(body.get("drink_fallback")), kind="drink"))
    if capped and any(len(r["desc"]) > MAX_TEXT or len(r.get("fallback", "")) > MAX_TEXT
                      for r in rows):
        raise ValueError(f"Keep each line under {MAX_TEXT} characters")
    return rows


@app.post("/api/public/order")
def api_public_order():
    """Anyone may add or change THEIR OWN order, until the day is closed."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    method = body.get("method") or "cash"
    venmo_user = (body.get("venmo_user") or "").strip()

    if not name:
        return jsonify({"error": "Enter your name"}), 400
    try:
        rows = order_lines(body, capped=True)
    except ValueError as err:
        return jsonify({"error": str(err)}), 400
    # A drink on its own is a fine order; nothing at all is not.
    if not rows:
        return jsonify({"error": "Enter what you'd like"}), 400
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

        # The same dish typed two ways makes two lines, so the count never adds
        # up against the receipt -- on 28 Aug six rice plates showed as 2, 2, 1
        # and 1, and the missing plate went unseen. Offer the wording everyone
        # else used, one line at a time. `items_ok` holds the lines already
        # answered "mine is different"; it is kept apart from `confirm` above
        # so answering the name question never silently answers this one, and
        # answering for one line never waves through the next. The old page's
        # `item_ok: true` still skips the check, as it always did.
        if body.get("item_ok") is not True:
            said = body.get("items_ok")
            answered = ({d.casefold() for d in said if isinstance(d, str)}
                        if isinstance(said, list) else set())
            # Food only, on both sides: drinks are short, and "Coke" vs "Coke
            # Zero" already stay apart.
            existing = [i["desc"] for o in day["orders"] for i in o["items"]
                        if i.get("kind") != "drink"]
            food = [r["desc"] for r in rows if r.get("kind") != "drink"]
            for index, desc in enumerate(food):
                if desc.casefold() in answered:
                    continue
                # Already worded exactly like somebody else's -- the thing we
                # are trying to encourage, so never interrupt it, even when
                # some third wording of the same dish is also on the list.
                if any(d.casefold() == desc.casefold() for d in existing):
                    continue
                close = core.similar_items(desc, existing)
                if close:
                    return jsonify({"error": "similar_item", "line": index, "desc": desc,
                                    "match": close[0],
                                    "count": sum(1 for d in existing
                                                 if d.casefold() == close[0].casefold())}), 409

        if order is None:
            order = {"name": name, "items": [], "paid_cents": None}
            day["orders"].append(order)
        # Every line is its own item row, added in this one locked write. A
        # drink is an item too, so it is priced, counted and totalled exactly
        # like food -- which is right: it is a line on the receipt.
        for row in rows:
            order["items"].append({**row, "price_cents": _price_from(menus, day["place"],
                                                                     row["desc"])})
        order["method"] = method
        if method == "venmo" and venmo_user:
            order["venmo_user"] = venmo_user

    for row in rows:
        store.learn_item(place, row["desc"], None)
    return jsonify(public_view(store.load_day(day_date)))


def _price_from(menus, place, desc):
    for item in menus.get(place, []):
        if item["desc"].casefold() == desc.casefold():
            return item["price_cents"]
    return None


def _item_key(item):
    return item["desc"].casefold(), item.get("kind") == "drink"


def _todays_price(day, desc):
    """The price this wording already carries today, if everyone who ordered
    it agrees on one."""
    key = desc.casefold()
    prices = {i["price_cents"] for o in day["orders"] for i in o["items"]
              if i["desc"].casefold() == key and i["price_cents"] is not None}
    return prices.pop() if len(prices) == 1 else None


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
    """The organiser typing someone's order: the same lines, backups and drink
    as the ordering page, with the money they handed over."""
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("Enter a name")
    rows = order_lines(body)
    if not rows:
        raise ValueError("Enter an item")
    # A price typed with the order only makes sense for a single item. The
    # form no longer sends one -- prices come off the receipt in step 3 -- but
    # an older page might.
    typed = _text(body.get("price"))
    if typed and len(rows) != 1:
        raise ValueError("Type prices in step 3 when there are several items")

    menus = store.load_menus()
    order = find_order(day, name)
    if order is None:
        order = {"name": name, "items": [], "paid_cents": None}
        day["orders"].append(order)
    for row in rows:
        price = (core.parse_price(typed) if typed
                 else _price_from(menus, day["place"], row["desc"]))
        order["items"].append({**row, "price_cents": price})
        queue_learn(day["place"], row["desc"], price)
    if (body.get("paid") or "").strip():
        order["paid_cents"] = core.parse_price(body["paid"])
    if body.get("method"):
        order["method"] = parse_method(body["method"])


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
                       ("restaurant_paid", "restaurant_paid_cents"),
                       ("receipt_subtotal", "receipt_subtotal_cents")):
        if field in body:
            raw = (body.get(field) or "").strip()
            day[key] = core.parse_price(raw) if raw else None
    # A count of things, not money -- parse_price would multiply it by 100.
    if "receipt_items" in body:
        raw = str(body.get("receipt_items") or "").strip()
        if not raw:
            day["receipt_items"] = None
        elif raw.isdigit():
            day["receipt_items"] = int(raw)
        else:
            raise ValueError(f"'{raw}' is not a number of items")
    # A percentage, likewise not money.
    if "surcharge_pct" in body:
        raw = str(body.get("surcharge_pct") or "").strip()
        # Only a genuinely empty box clears it. Stripping the "%" first would
        # turn a stray "%%" into a blank and silently wipe a saved figure.
        if not raw:
            day["surcharge_pct"] = None
        else:
            try:
                pct = float(raw.rstrip("%").strip())
            except ValueError:
                raise ValueError(f"'{raw}' is not a percentage")
            if not 0 <= pct <= 100:
                raise ValueError("A surcharge is between 0 and 100 percent")
            day["surcharge_pct"] = pct
    # Whether this place's prices already include tax. Saved on the day, and
    # carried forward to later visits to the same restaurant by resolved().
    if "tax_included" in body:
        value = body.get("tax_included")
        if not isinstance(value, bool):
            raise ValueError("Say whether the prices include tax")
        day["tax_included"] = value


def act_merge_items(day, body):
    """Rewrite several wordings of one dish to a single agreed wording.

    Only `desc` changes -- never a price. group_items() and set_price_for_desc()
    keep keying on exact text exactly as before, so the money path is untouched;
    all this does is make six rice plates show as one line of six instead of
    four lines the receipt cannot be checked against. One-way: the original
    wordings are not kept, which is why the organiser confirms first.
    """
    into = (body.get("into") or "").strip()
    wordings = body.get("from")
    if not into:
        raise ValueError("Pick the wording to keep")
    if not isinstance(wordings, list) or not wordings:
        raise ValueError("Nothing to merge")
    keys = {w.casefold() for w in wordings if isinstance(w, str)}
    for order in day["orders"]:
        for item in order["items"]:
            if item["desc"].casefold() in keys:
                item["desc"] = into


def act_change_given(day, body):
    require_order(day, body)["change_given"] = bool(body.get("given"))


def act_settle(day, body):
    """They have paid what they still owed, in cash or on Venmo.

    The amount is worked out here from their priced items, never taken from
    the page, so a double tap finds nothing left to settle instead of
    recording the money twice.
    """
    order = require_order(day, body)
    method = parse_method(body.get("method"))
    still = core.outstanding_of(order)
    if still is None:
        raise ValueError(f"Price {order['name']}'s items first")
    if not still:
        raise ValueError(f"{order['name']} doesn't owe anything")
    if core.paid_total(order) is None:
        order["paid_cents"] = still
        order["method"] = method
    elif order.get("paid_cents") is not None and method == core.method_of(order):
        order["paid_cents"] += still
    else:
        # Paid the other way, so it goes in the other pot. Cash and Venmo are
        # counted apart because only cash can pay the restaurant.
        order.setdefault("topups", []).append({"cents": still, "method": method})


def act_remove_topup(day, body):
    """Undo one later payment -- tapped on the wrong person, say."""
    order = require_order(day, body)
    later = order.get("topups") or []
    index = body.get("index")
    if not isinstance(index, int) or not 0 <= index < len(later):
        raise ValueError("No such payment")
    later.pop(index)
    if not later:
        order.pop("topups", None)


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
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("items must be a list")

    # The panel no longer shows prices -- they are typed once in step 3 -- so
    # each line keeps the price it already had, matched on wording and kind one
    # for one. A new or reworded line takes today's price for that wording, or
    # the remembered one. An older page that still sends a price is obeyed.
    kept = {}
    for item in order["items"]:
        kept.setdefault(_item_key(item), []).append(item["price_cents"])
    menus = None
    parsed = []
    for row in rows:
        desc = _text(row.get("desc"))
        if not desc:
            raise ValueError("Every item needs a name")
        # kind and fallback ride along: dropping them turned a drink into food
        # and lost its backup every time someone's order was edited.
        item = _line(desc, _text(row.get("fallback")),
                     kind="drink" if row.get("kind") == "drink" else None)
        if "price" in row:
            raw = _text(row.get("price"))
            price = core.parse_price(raw) if raw else None
        else:
            same = kept.get(_item_key(item))
            price = same.pop(0) if same else None
            if price is None:
                price = _todays_price(day, desc)
            if price is None:
                menus = menus if menus is not None else store.load_menus()
                price = _price_from(menus, day["place"], desc)
        item["price_cents"] = price
        parsed.append(item)

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
    "delete-person": act_delete_person, "merge-items": act_merge_items,
    "settle": act_settle, "remove-topup": act_remove_topup,
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
