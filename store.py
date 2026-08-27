"""Where days and menus live.

Two backends, chosen by environment:

    DATABASE_URL unset  ->  JSON files under data/, exactly as before
    DATABASE_URL set    ->  SQL (SQLite in tests, Postgres in production)

Days are stored whole, as the same dict lunchcore already passes around, so the
money engine and its tests are completely untouched by any of this.

The database exists for CONCURRENCY, not size. With several people submitting
orders at once, a read-modify-write of a whole JSON file loses updates. Every
mutation here runs inside one locked transaction instead -- see `edit_day`.
"""

import json
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import lunchcore as core

_engine = None
_meta = None
_days = None
_menus = None
_files = None
_lock = threading.Lock()        # several requests can reach _connect() at once
_write_lock = threading.Lock()  # serialises SQLite writes (see _write_txn)


def database_url():
    return os.environ.get("DATABASE_URL") or ""


def using_db():
    return bool(database_url())


# --- schema ----------------------------------------------------------------

def _connect():
    """Build the engine and tables on first use, so the file backend never
    imports SQLAlchemy at all."""
    global _engine, _meta, _days, _menus, _files
    if _engine is not None:
        return _engine

    with _lock:                       # double-checked: only one thread builds it
        if _engine is not None:
            return _engine

        from sqlalchemy import (Column, Integer, LargeBinary, MetaData, String,
                                Table, Text, create_engine)

        url = database_url()
        # Render hands out the old postgres:// prefix that SQLAlchemy 2 rejects
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)

        if url.startswith("sqlite"):
            engine = create_engine(url, future=True,
                                   connect_args={"timeout": 30,
                                                 "check_same_thread": False})
            _tune_sqlite(engine)
        else:
            engine = create_engine(url, pool_pre_ping=True, future=True)
        meta = MetaData()
        days = Table("days", meta,
                     Column("date", String(10), primary_key=True),
                     Column("data", Text, nullable=False))
        menus = Table("menus", meta,
                      Column("place", String(200), primary_key=True),
                      Column("data", Text, nullable=False))
        # Menu photos/PDFs live here rather than on disk: hosts wipe the
        # filesystem on every deploy, and these must survive that.
        menu_files = Table("menu_files", meta,
                           Column("id", String(32), primary_key=True),
                           Column("place", String(200), nullable=False, index=True),
                           Column("filename", String(255), nullable=False),
                           Column("mime", String(100), nullable=False),
                           Column("size", Integer, nullable=False),
                           Column("uploaded", String(32), nullable=False),
                           Column("data", LargeBinary, nullable=False))
        meta.create_all(engine, checkfirst=True)
        # publish only once fully built, so no thread sees a half-set-up module
        _meta, _days, _menus, _files, _engine = (meta, days, menus, menu_files,
                                                 engine)
    return _engine


def reset_for_tests():
    """Drop the cached engine so a test can point DATABASE_URL somewhere new."""
    global _engine, _meta, _days, _menus, _files
    if _engine is not None:
        _engine.dispose()
    _engine = _meta = _days = _menus = _files = None


def _tune_sqlite(engine):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")        # readers don't block writers
        cursor.execute("PRAGMA busy_timeout=30000")      # wait rather than error
        cursor.close()


@contextmanager
def _write_txn():
    """Serialised read-modify-write, so two simultaneous orders can't interleave.

    Postgres (production) locks the individual row via SELECT ... FOR UPDATE, so
    different days never block each other. SQLite is the tests/local-dev backend
    and is single-process by definition, so a process-wide lock gives exactly the
    same guarantee without fighting pysqlite's transaction handling.
    """
    engine = _connect()
    if engine.dialect.name == "sqlite":
        with _write_lock, engine.begin() as conn:
            yield conn
    else:
        with engine.begin() as conn:
            yield conn


def _is_sqlite():
    return _connect().dialect.name == "sqlite"


def _upsert(conn, table, key_column, key, payload):
    """Insert or overwrite one row atomically. Two requests racing to create the
    same key must not collide with a UNIQUE violation."""
    dialect = conn.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect in ("postgresql", "postgres"):
        from sqlalchemy.dialects.postgresql import insert
    else:                                   # portable fallback
        from sqlalchemy import update
        done = conn.execute(update(table).where(key_column == key)
                            .values(data=payload)).rowcount
        if not done:
            conn.execute(table.insert().values(**{key_column.name: key, "data": payload}))
        return
    stmt = insert(table).values(**{key_column.name: key, "data": payload})
    conn.execute(stmt.on_conflict_do_update(index_elements=[key_column],
                                            set_={"data": payload}))


def _lock_row(conn, table, key_column, key):
    """Read a row with the write lock already held.

    On SQLite the process-wide lock in _write_txn already makes this exclusive,
    so a plain SELECT suffices. Postgres locks the individual row instead, so
    concurrent days don't block each other.
    """
    from sqlalchemy import select
    stmt = select(table.c.data).where(key_column == key)
    if conn.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    return conn.execute(stmt).first()


# --- days ------------------------------------------------------------------

def load_day(day_date):
    if not using_db():
        return core.load_day(day_date)

    from sqlalchemy import select
    engine = _connect()
    with engine.connect() as conn:
        row = conn.execute(select(_days.c.data)
                           .where(_days.c.date == day_date)).first()
    return _normalise(json.loads(row[0]), day_date) if row else core.new_day(day_date)


def _normalise(day, day_date):
    """Same defaults load_day() applies, so old rows gain new fields safely."""
    day.setdefault("date", day_date)
    day.setdefault("place", "")
    day.setdefault("orders", [])
    day.setdefault("receipt_cents", None)
    day.setdefault("restaurant_paid_cents", None)
    day.setdefault("restaurant_method", None)
    day.setdefault("locked", False)
    day.setdefault("organiser", "")
    return day


def save_day(day):
    if not using_db():
        core.save_day(day)
        return

    with _write_txn() as conn:
        _upsert(conn, _days, _days.c.date, day["date"], json.dumps(day))


def list_saved_dates():
    if not using_db():
        return core.list_saved_dates()

    from sqlalchemy import select
    engine = _connect()
    with engine.connect() as conn:
        rows = conn.execute(select(_days.c.date).order_by(_days.c.date.desc())).all()
    return [r[0] for r in rows]


@contextmanager
def edit_day(day_date):
    """Read-modify-write a day under a lock, so simultaneous submissions can't
    clobber each other. This is the whole reason the database exists.

    Yields the day dict; whatever it looks like on exit is what gets stored.
    """
    if not using_db():
        # Single local user, no contention -- the plain path is fine.
        day = core.load_day(day_date)
        yield day
        core.save_day(day)
        return

    with _write_txn() as conn:
        row = _lock_row(conn, _days, _days.c.date, day_date)
        day = _normalise(json.loads(row[0]), day_date) if row else core.new_day(day_date)
        yield day
        _upsert(conn, _days, _days.c.date, day_date, json.dumps(day))


# --- menus -----------------------------------------------------------------

def load_menus():
    if not using_db():
        return core.load_menus()

    from sqlalchemy import select
    engine = _connect()
    with engine.connect() as conn:
        rows = conn.execute(select(_menus.c.place, _menus.c.data)).all()
    return {place: json.loads(data) for place, data in rows}


def save_menus(menus):
    if not using_db():
        core.save_menus(menus)
        return

    from sqlalchemy import delete, select
    with _write_txn() as conn:
        have = {r[0] for r in conn.execute(select(_menus.c.place)).all()}
        for place, items in menus.items():
            _upsert(conn, _menus, _menus.c.place, place, json.dumps(items))
        for gone in have - set(menus):
            conn.execute(delete(_menus).where(_menus.c.place == gone))


def learn_item(place, desc, price_cents):
    """Remember one item, atomically.

    Rewriting the whole menu blob would lose entries whenever two people order
    different new items at once, so this locks just that place's row and merges
    a single item -- matching core.learn_item's rules, including never wiping a
    known price with a blank one.
    """
    if not using_db():
        menus = core.load_menus()
        core.learn_item(menus, place, desc, price_cents)
        core.save_menus(menus)
        return

    key = place or ""
    _connect()
    with _write_txn() as conn:
        row = _lock_row(conn, _menus, _menus.c.place, key)
        items = json.loads(row[0]) if row else []
        for item in items:
            if item["desc"].casefold() == desc.casefold():
                if price_cents is not None:
                    item["price_cents"] = price_cents
                break
        else:
            items.append({"desc": desc, "price_cents": price_cents})
            items.sort(key=lambda i: i["desc"].casefold())
        _upsert(conn, _menus, _menus.c.place, key, json.dumps(items))


# --- menu photos and PDFs --------------------------------------------------
# Kept per place, not per day: a restaurant's menu is the same every visit, so
# uploading once serves every future day and keeps the stored bytes tiny.

MENU_DIR_NAME = "menus"
MENU_INDEX_NAME = "index.json"
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
              "image/webp": ".webp", "application/pdf": ".pdf"}


def _menu_dir():
    return core.DATA_DIR / MENU_DIR_NAME


def _menu_index():
    path = _menu_dir() / MENU_INDEX_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_menu_index(index):
    _menu_dir().mkdir(parents=True, exist_ok=True)
    (_menu_dir() / MENU_INDEX_NAME).write_text(json.dumps(index, indent=2),
                                               encoding="utf-8")


def _meta_of(row):
    """Metadata only. The bytes are never included in a listing."""
    return {"id": row["id"], "place": row["place"], "filename": row["filename"],
            "mime": row["mime"], "size": row["size"], "uploaded": row["uploaded"]}


def save_menu_file(place, filename, mime, blob):
    """Store one menu file against a place. Returns its id."""
    file_id = secrets.token_hex(16)
    record = {"id": file_id, "place": place, "filename": filename, "mime": mime,
              "size": len(blob),
              "uploaded": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    if not using_db():
        _menu_dir().mkdir(parents=True, exist_ok=True)
        (_menu_dir() / (file_id + EXTENSIONS.get(mime, ""))).write_bytes(blob)
        with _write_lock:
            index = _menu_index()
            index[file_id] = record
            _write_menu_index(index)
        return file_id

    from sqlalchemy import insert
    _connect()
    with _write_txn() as conn:
        conn.execute(insert(_files).values(data=blob, **record))
    return file_id


def list_menu_files(place):
    """Metadata for one place's menu files, oldest first."""
    if not using_db():
        rows = [r for r in _menu_index().values() if r["place"] == place]
        return sorted((_meta_of(r) for r in rows), key=lambda r: r["uploaded"])

    from sqlalchemy import select
    _connect()
    columns = [_files.c.id, _files.c.place, _files.c.filename, _files.c.mime,
               _files.c.size, _files.c.uploaded]
    engine = _connect()
    with engine.connect() as conn:
        rows = conn.execute(select(*columns).where(_files.c.place == place)
                            .order_by(_files.c.uploaded)).mappings().all()
    return [_meta_of(r) for r in rows]


def load_menu_file(file_id):
    """(metadata, bytes) for one file, or (None, None)."""
    if not using_db():
        record = _menu_index().get(file_id)
        if record is None:
            return None, None
        path = _menu_dir() / (file_id + EXTENSIONS.get(record["mime"], ""))
        try:
            return _meta_of(record), path.read_bytes()
        except OSError:
            return None, None

    from sqlalchemy import select
    engine = _connect()
    with engine.connect() as conn:
        row = conn.execute(select(_files).where(_files.c.id == file_id)
                           ).mappings().first()
    return (_meta_of(row), row["data"]) if row else (None, None)


def delete_menu_file(file_id):
    """True if something was removed."""
    if not using_db():
        with _write_lock:
            index = _menu_index()
            record = index.pop(file_id, None)
            if record is None:
                return False
            _write_menu_index(index)
        path = _menu_dir() / (file_id + EXTENSIONS.get(record["mime"], ""))
        path.unlink(missing_ok=True)
        return True

    from sqlalchemy import delete
    _connect()
    with _write_txn() as conn:
        return conn.execute(delete(_files).where(_files.c.id == file_id)).rowcount > 0


# --- helpers that read through whichever backend is active -----------------

def inherited_restaurant_method(day_date):
    """Same rule as lunchcore's, but reading from the active backend."""
    for saved in list_saved_dates():                 # newest first
        if saved <= day_date:
            stored = load_day(saved).get("restaurant_method")
            if stored in ("cash", "card"):
                return stored
    return "cash"
