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
| `/` | Anyone with the link. Name, one thing per line (each with an optional backup in case they're out), an optional drink with its own backup, cash-or-Venmo. A second tab shows what everyone's getting — **names and items only, never prices.** |
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

`SECRET_KEY` is generated automatically. (If a service is ever set up without it, the app makes one key, keeps it in the database, and uses that — rather than a new one at every restart, which would log the organiser out every time the free tier woke up.) Logins last 31 days and renew on every visit.

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
Name   [ Kai ]
Items  1 [ Plate lunch ]        If they're out [ Loco moco ]
       2 [ another item (optional) ]
Drink    [ Coke ]               If they're out [ Diet Coke ]
Gave me $ [ 20 ]   Cash | Venmo   [ Add ]
```
The same lines as the ordering page: **one thing per line**, two to start, and a new one appears as soon as the last is used. Every line — and the drink — can name a **backup** in case the restaurant is out of it; the call list reads them out (`3× Plate lunch — if they're out: Loco moco`). Enter adds the order from any box, and focus jumps back to Name.

- **No price here.** Prices come off the receipt later.
- **"Gave me $"** is where you record cash as it's handed to you. Optional — leave it blank if they'll pay later.
- **Cash or Venmo** — a toggle next to every amount. It sticks between entries, so a run of Venmo payers is one click, not one per person. Venmo rows are tinted blue.
- **The cash box stays editable on every row.** If someone pays after their order is already on the list, just click their box and type it — no need to delete and re-add them. Same for correcting a wrong amount, or clearing it back to unpaid by emptying the box. It's editable in step 4 too, which is where late payments usually turn up.
- Typing a name that already exists **adds to that person** rather than making a second row. `kai` finds `Kai`.
- **Items you've typed before are suggested as you type** — from the moment you first type them, price or no price. Your current place's items come first, then everything else you've ordered anywhere, so a suggestion is never missing.
- **The pencil (✎) on any row** expands it in place so you can fix things afterwards — rename the person, retype an item or its backup, or add and remove items and drinks. Prices aren't in there: they're typed once in step 3, and each line keeps its price when you edit (a reworded line picks up today's price for its new wording). Enter saves, Escape cancels. Nothing is sent until you press Save, and a bad entry is rejected whole rather than half-applied.
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

**The receipt check** — count the things on the receipt and type what you paid, and it works out what the till *should* have charged and shows the sum:

```
13 items · ✓ $211.90 + $9.98 tax = $221.88 — 1¢ off, just rounding
```

- **Tax** — *Added on top* (most places) or *In the prices* (some, like Doner Shack). Set it once per restaurant; it comes back by itself. If a receipt only matches the other setting, the check offers a one-tap switch rather than guessing.
- **Card surcharge %** — the card fee a place adds, charged on top of the tax, and only when you pay by card. Also remembered per restaurant.
- A cent per item of slack covers the till rounding tax line by line; nothing on a menu is that cheap, so a missing plate or drink can't hide in it.

Red means the receipt lists a different number of things than you took orders for — something was never rung up. Amber means the right things for the wrong money: a price, the tax setting or the fee. None of this changes what anyone owes — that is always price × 1.04712, rounded up.

### 4 · Settle up
Grouped by what's left to do:

```
STILL OWES YOU
Deb    Veggie wrap    owes $15          [Request $15]  [Paid $15 on Venmo]  [Paid $15 in cash]
Ron    Plate lunch    still owes $2 of $22              [Paid $2 in cash]   [Paid $2 on Venmo]
HAND BACK CASH
Kai    Plate lunch    owed $18    gave $20    $2.00 back    [ ] handed over
SQUARE
Ian    Plate lunch    owed $17    gave $15  + $2.00 Venmo   square
```

**Paid …** records exactly what they still owed — worked out from their priced items, never typed — in the pot it came in. Paid the same way as before, it's added to their payment; paid the other way (cash at lunch, the last $2 on Venmo), it's kept as its own payment with its own ×, so cash and Venmo never blur. Venmo payers who owe get a request link for exactly the amount outstanding. Tick change off as you hand it back so you don't lose your place.

## The bar along the bottom

One answer first, then the sum that gets there, then only the notes that are true today:

```
You're $5.13 ahead
$207.00 cash (after $28.00 change) + $20.00 Venmo − $221.87 receipt = $5.13
At the till your cash was $14.87 short, so that came out of your own pocket — the Venmo money pays it back.
```

That's 23 Sept, which used to read "SHORT $14.87 of your own money" in red above "NET surplus $5.13" — two lines that looked like they disagreed. Both were true, and the till shortfall is now a note explaining where it went. Venmo money can't be spent at the restaurant, which is why the cash shortfall is still called out — **before a receipt it tells you how much of your own cash to bring.**

When people still owe you, the bar says so and where that leaves you: *"2 people still owe you $17.00 (step 4) — once paid you'll be $1.81 ahead."* Unpriced items make it say *Not final yet* instead of showing a wrong number.

### If you pay by card, say so

Step 3 has a **Cash | Card** toggle for how *you* paid the restaurant. On card there's no till shortfall — you don't need bills at all — and the sum reads *"… − $171.06 charged to your card"*. It still goes red if you're genuinely out of pocket; card removes the *cash* constraint, not the possibility of losing money.

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
