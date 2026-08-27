# Lunch

Group lunch ordering and settling-up. Coworkers add their own orders from a shared link; whoever is picking up gets a private page for prices, the receipt, and working out everyone's change.

Hawaii food tax and whole-dollar rounding are built in, so change is always bills.

## Three ways to run it

**Locally** — double-click `Lunch.bat`, which serves on `127.0.0.1:8420` and stores days as JSON files in `data/`. No internet, nothing shared.

> **The organiser password** is generated on first run and saved to `data/admin_password.txt`, which is git-ignored. It's printed in the console window the first time. Edit that file to choose your own. There is deliberately no password anywhere in this source — the repo is public.

**Sandbox** — double-click `Sandbox.bat` to try things out. Serves on port **8421** and writes to `sandbox.db`, so it cannot touch your real orders in `data/` — setting `DATABASE_URL` sends every read and write to SQLite instead, so those files are never opened at all. It seeds a few fake orders and prints an address your phone can reach:

```
  This PC     http://127.0.0.1:8421
  Your phone  http://192.168.1.42:8421      <- same wifi
  Admin       http://127.0.0.1:8421/admin   password: test   (fake data only)
```

Delete `sandbox.db` to wipe it and start over.

> Windows will ask to allow Python through the firewall the first time — say yes to **Private networks**, or the phone address won't answer. If the prompt gets dismissed, in an **admin** PowerShell:
>
> ```powershell
> New-NetFirewallRule -DisplayName "Lunch sandbox" -Direction Inbound `
>   -Protocol TCP -LocalPort 8421 -Profile Private -Action Allow
> ```

**Hosted** — see [Deploying](#deploying) below. Same app, with a database so several people can order at once.

### Why the sandbox uses a different port

Windows lets two processes bind the same port without either one erroring, and which of them answers a given request is undefined. If the sandbox shared 8420 with the real app, a sandbox request could be served by the real app and write to real orders. So the sandbox stays on 8421 **and** refuses to start if anything is already answering there.

## Who sees what

| | |
|---|---|
| `/` | Anyone with the link. Name, item, cash-or-Venmo. A second tab shows what everyone's getting — **names and items only, never prices.** |
| `/admin` | Password. The four steps: orders, call-in list, receipt prices, settle up. |
| `/history` | Password. Spend per person, per place, over time. |

---

## Deploying

### 1 · A database that doesn't expire

Sign up at [neon.tech](https://neon.tech), create a project, and copy the connection string (`postgresql://...?sslmode=require`). Free, permanent, 0.5 GB — this app writes about 2 KB a day.

> **Why not Render's free Postgres?** It expires 30 days after creation, then gets deleted along with all its data after a 14-day grace period, and free instances have no backups. For something meant to keep lunch history that's a countdown, not a free tier. Neon's free tier has no expiry clock. Nothing in the code changes either way — `store.py` normalises the URL and already sets `pool_pre_ping`, which is what Neon's idle auto-suspend needs.

### 2 · The web service

Push to GitHub, then on [render.com](https://render.com): **New → Blueprint**, point it at the repo. `render.yaml` creates the web service.

Set two environment variables in the Render dashboard:

- **`DATABASE_URL`** — the Neon connection string from step 1.
- **`ADMIN_PASSWORD`** — your organiser password. **If you don't set it, admin access stays locked** (the app generates an unknowable one rather than falling back to something guessable, since this repo is public).

`SECRET_KEY` is generated automatically.

The service is named `wasalunch`, so you get **`https://wasalunch.onrender.com`** free. Rename it in `render.yaml` to change that.

**Free tier sleeps** after ~15 minutes idle. The first person to open it each morning waits ~40 seconds while it wakes; everyone after that is instant. Nothing is lost while it sleeps — the data lives in the database.

### Moving your existing days across

```bash
set DATABASE_URL=<the External Database URL from Render>
python import_days.py
```

Copies everything in `data/` into the database. Safe to run more than once.

### Storage

`DATABASE_URL` unset → JSON files in `data/`. Set → Postgres. That switch is why the local mode still works exactly as it always did.

Your `data/` folder is **git-ignored** — real names and orders never get published, even though the repo is public.

## The four steps match your day

### 1 · Take orders
```
Name [ Kai ]   Item [ Plate lunch ]   Gave me $ [ 20 ]   [ Add ]
```
Type, Tab, Tab, Enter — never needs the mouse. Focus jumps back to Name so you can keep going.

- **No price here.** Prices come off the receipt later.
- **"Gave me $"** is where you record cash as it's handed to you. Optional — leave it blank if they'll pay later.
- **Cash or Venmo** — a toggle next to every amount. It sticks between entries, so a run of Venmo payers is one click, not one per person. Venmo rows are tinted blue.
- **The cash box stays editable on every row.** If someone pays after their order is already on the list, just click their box and type it — no need to delete and re-add them. Same for correcting a wrong amount, or clearing it back to unpaid by emptying the box. It's editable in step 4 too, which is where late payments usually turn up.
- Typing a name that already exists **adds to that person** rather than making a second row. `kai` finds `Kai`.
- **Items you've typed before are suggested as you type** — from the moment you first type them, price or no price. Your current place's items come first, then everything else you've ordered anywhere, so a suggestion is never missing.
- **The pencil (✎) on any row** expands it in place so you can fix things afterwards — rename the person, retype an item, correct just their price, or add and remove items. Enter saves, Escape cancels. Nothing is sent until you press Save, and a bad entry is rejected whole rather than half-applied.
- Underneath, a running tally shows duplicates at a glance: `3× Plate lunch · 2× Saimin`.

### 2 · Call it in
The order grouped and enlarged for reading down a phone line, with a **Copy** button.

### 3 · Receipt prices
One row per item, **not** per person:

```
3×  Plate lunch    Kai, Sam, Leilani     $ [ 16.50 ]
1×  Saimin         Mo                    $ [       ]
```

Type each price **once** and everyone who ordered it updates. The badge on the tab counts what's still missing.

**Receipt total box** — type the total off the receipt and it checks its own math. If it doesn't match, you mistyped a price, and you'll know *before* handing out change.

### 4 · Hand out change
One card per person, sorted so people owed money come first:

```
Kai    Plate lunch    owed $18    gave $20    $2.00 back    [ ] handed over
```

Tick people off as you go so you don't lose your place. Short-payers show red; anyone still unpriced shows amber instead of a wrong number.

## The bar along the bottom

You pay the restaurant in **cash**, so Venmo money can't be spent there. The bar keeps the two apart:

```
CASH    in $60 · change out $6 · on hand $54 · receipt $126.30  →  ⚠ SHORT $72.30 of your own money
VENMO   in $80 · $8 to send back
NET     surplus $6.70 overall
```

Three lines, three questions:

- **CASH** — *can I actually pay?* This is bills in your pocket versus the bill. Red means you'd be fronting your own money. **Check this before you leave.**
- **VENMO** — what you've received digitally, and what you owe back.
- **NET** — *am I whole overall?* Everything in, minus everything out.

Without the split, the old single total would have claimed "$140 collected, you're covered" while you were holding $54 in bills against a $126 bill.

### If you pay by card, say so

Step 3 has a **Cash | Card** toggle for how *you* paid the restaurant.

Pick **Card** and the cash-shortfall warning disappears — you don't need bills at all, so there's no shortfall to have. The bar switches to the question that actually matters:

```
COLLECTED  $190.00  (cash $40 · Venmo $150)  ·  $17 to return
CARD       $171.06 charged
NET        $1.94 in your favour (cash $30 + Venmo $143)
```

It still goes red if you're genuinely out of pocket — card removes the *cash* constraint, not the possibility of losing money.

The choice **carries forward**: set it once and following days inherit it, so there's no daily click. Earlier days keep whatever they had.

## Step 3 also records what actually happened

Enter the **receipt total** and — paying cash — the **cash you handed over**. The app checks the receipt against its own math, works out the change the restaurant gave you, and uses the real figure rather than its estimate for everything above. On card there's no change, so that second field hides itself.

## Any day, not just today

Top bar: `[◀] [ Thu, Aug 20, 2026 ] [▶]  [Today]`. Click the date for a calendar — days with orders are dotted. Any date opens, so you can backfill a day you missed. Just looking doesn't create anything.

## The math

Each person's items are summed, multiplied by **1.04712** (Hawaii food tax, no tip), then **rounded up to the whole dollar**, so change is always bills.

> $16.50 → ×1.04712 → 17.277 → **owes $18** → gave $20 → **$2 back**

The round-up means the group pays slightly more than the bill — that gap is the **surplus** at the bottom.

## Files

- `data/lunch_YYYY-MM-DD.json` — one per day, plain text you can open and read.
- `data/menus.json` — remembered prices per place, so items you've ordered before arrive pre-filled.

Only what you typed is stored. Totals are recalculated every time and never saved, so they can't go stale.

Everything saves the instant you change it. There's no save button and nothing to lose.

## Tests

```bash
python tests.py
```

Runs everything against a temporary directory and a throwaway SQLite database, so `data/` is never opened. Covers the money rules (including the real Aug 20 day as a fixture, which must still reconcile to $142 collected / $137 on hand / $10.70 left), the cash-versus-Venmo split, card mode, storage parity between the file and database backends, that the public page exposes no money, that admin routes reject anonymous callers, and that twenty simultaneous orders all land.

## Under the hood

- `app.py` — the Flask app: pages, JSON API, and the admin password check
- `lunchcore.py` — all the money math
- `store.py` — storage. `DATABASE_URL` unset → JSON files; set → Postgres/SQLite. Mutations are one locked read-modify-write, so two people ordering in the same second can't overwrite each other
- `templates/`, `static/` — the pages themselves
- `sandbox.py` — the throwaway-database launcher
- `import_days.py` — one-shot migration of `data/` into a database

All arithmetic happens in Python, never in the browser.

**Superseded, kept for now:** `server.py` (the earlier standard-library server), `web/` (its pages), and `lunch_calculator.py` (the original desktop window). All three still work against `data/`, and several test suites still drive them, which is why they haven't been removed yet.
