"""Copy the local data/ JSON files into the database. Idempotent.

    set DATABASE_URL=postgresql://...
    python import_days.py
"""

import json
import sys

import lunchcore as core
import store


def main():
    if not store.using_db():
        sys.exit("Set DATABASE_URL first — there is nothing to import into.")

    dates = core.list_saved_dates()          # always the FILE backend
    if not dates:
        print(f"No day files under {core.DATA_DIR}")
    for day_date in sorted(dates):
        day = core.load_day(day_date)
        store.save_day(day)
        print(f"  imported {day_date}  ({len(day['orders'])} orders)")

    if core.MENUS_FILE.exists():
        menus = json.loads(core.MENUS_FILE.read_text(encoding="utf-8"))
        existing = store.load_menus()
        # merge rather than overwrite, so re-running can't lose learned prices
        for place, items in menus.items():
            have = {i["desc"].casefold(): i for i in existing.get(place, [])}
            for item in items:
                have.setdefault(item["desc"].casefold(), item)
            existing[place] = sorted(have.values(), key=lambda i: i["desc"].casefold())
        store.save_menus(existing)
        print(f"  imported menus for {len(menus)} place(s)")

    print(f"\nDone. {len(store.list_saved_dates())} days now in the database.")


if __name__ == "__main__":
    main()
