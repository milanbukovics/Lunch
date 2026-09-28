/* Rendering only. Every dollar figure is computed by lunchcore on the server
   and arrives pre-formatted -- do not do money arithmetic here. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

let state = null;
let date = new Date().toLocaleDateString("en-CA"); // local YYYY-MM-DD
let step = "orders";
let savedDays = [];

// --- server ---------------------------------------------------------------

async function api(path, body) {
  const options = body
    ? { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, ...body }) }
    : {};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Something went wrong");
  return data;
}

async function load() {
  state = await api(`/api/day?date=${date}`);
  savedDays = (await api("/api/days")).days;
  render();
}

async function send(path, body, note) {
  try {
    state = await api(path, body);
    render();
    if (note) toast(note);
  } catch (err) {
    toast(err.message, true);
  }
}

let toastTimer;
function toast(message, isError) {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.className = "toast"), 2600);
}

// --- menu files -----------------------------------------------------------
// Menus belong to a restaurant, not a day: upload once and every future day at
// that place shows it. That needs the Place field filled in.

function renderMenuAdmin() {
  const place = state.place.trim();
  const files = state.menu || [];

  $("menuPlaceHint").textContent = place
    ? `Saved for ${place}, so it comes back every time you order from there.`
    : "Name the restaurant in Place above and these save against it.";

  $("adminMenuStrip").replaceChildren(...files.map((file) => {
    const wrap = el("div", "menuItem");
    if (file.kind === "link") {
      // The server has already checked this is http(s); noreferrer as well as
      // noopener because it points somewhere we do not control.
      const link = el("a", "menuLink", file.filename);
      link.href = file.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      wrap.append(link);
    } else if (file.kind === "pdf") {
      const link = el("a", "menuPdf", file.filename);
      link.href = `/menu/${file.id}`;
      link.target = "_blank";
      link.rel = "noopener";
      wrap.append(link);
    } else {
      const thumb = el("a", "menuThumb");
      thumb.href = `/menu/${file.id}`;
      thumb.target = "_blank";
      thumb.rel = "noopener";
      const img = el("img");
      img.src = `/menu/${file.id}`;
      img.alt = file.filename;
      thumb.append(img);
      wrap.append(thumb);
    }
    const drop = el("button", "x", "×");
    drop.type = "button";
    drop.title = `Remove ${file.filename}`;
    drop.onclick = () => removeMenuFile(file.id);
    wrap.append(drop);
    return wrap;
  }));
}

/* Phone photos run to several megabytes, which is slow on office wifi and
   wasteful in the database. Redrawing through a canvas cuts that to a few
   hundred KB. 2400px is deliberately generous -- the whole point is being able
   to zoom in and read small print. PDFs are uploaded untouched. */
const MAX_EDGE = 2400;

function shrinkImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("bad image")),
                    "image/jpeg", 0.85);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`Couldn't read ${file.name} — try a JPG, PNG or PDF`));
    };
    img.src = url;
  });
}

/* A blank Place is the one thing that stops an upload, so say which field to
   fill and put the cursor in it. Returns false so callers can stop. */
function needsPlace() {
  if (state.place.trim()) return false;
  toast("Name the restaurant first — the menu is saved against it.", true);
  $("place").focus();
  return true;
}

async function uploadMenuFiles(files) {
  if (needsPlace()) return;
  const place = state.place.trim();

  for (const file of files) {
    try {
      let blob = file, name = file.name;
      if (file.type !== "application/pdf") {
        blob = await shrinkImage(file);
        name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
      }
      const form = new FormData();
      form.append("place", place);
      form.append("file", blob, name);
      const response = await fetch("/api/menu-file", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Upload failed");
      state.menu = data.menu;
    } catch (err) {
      toast(err.message, true);
      break;                        // a cap or a bad file: stop, don't spam
    }
  }
  renderMenuAdmin();
}

/* Some places just have a web page, and photographing a screen to upload it is
   silly. The server does the checking -- it only accepts http and https, since
   this ends up as a link every coworker is invited to click. */
async function addMenuLink() {
  if (needsPlace()) return;
  const box = $("menuUrl");
  const url = box.value.trim();
  if (!url) { box.focus(); return; }

  try {
    const response = await fetch("/api/menu-link", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, place: state.place.trim() }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not add that link");
    state.menu = data.menu;
    box.value = "";
    renderMenuAdmin();
    toast("Link added");
  } catch (err) {
    toast(err.message, true);
    box.focus();
  }
}

async function removeMenuFile(id) {
  try {
    const response = await fetch("/api/menu-file/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, place: state.place.trim() }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not remove it");
    state.menu = data.menu;
    renderMenuAdmin();
    toast("Removed");
  } catch (err) {
    toast(err.message, true);
  }
}

// --- render ---------------------------------------------------------------

/* One item in words. A drink names what to get if they're out of it, so the
   organiser sees it wherever the order is listed, not only on the call sheet. */
function itemText(row) {
  const alt = row.fallback ? ` (or ${row.fallback})` : "";
  return row.desc + alt;
}

function itemsText(person) {
  const rows = person.item_rows || person.items.map((desc) => ({ desc }));
  return rows.map(itemText).join(", ") || "—";
}

function render() {
  const t = state.totals;

  $("dayLabel").textContent = new Date(date + "T12:00").toLocaleDateString(undefined,
    { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  if ($("place") !== document.activeElement) $("place").value = state.place;
  for (const [id, value] of [["receiptTotal", state.receipt],
                             ["receiptItems", state.receipt_items],
                             ["surchargePct", state.surcharge_pct],
                             ["cashHanded", state.restaurant_paid]]) {
    if ($(id) !== document.activeElement) $(id).value = value;
  }

  // A card leaves no change, so that field is meaningless in card mode
  const byCard = state.restaurant_method === "card";
  for (const button of $("payMethod").children) {
    button.className = "tog" + (button.dataset.m === state.restaurant_method
                                ? " on " + state.restaurant_method : "");
  }
  $("handedField").classList.toggle("hidden", byCard);
  // A card fee is only ever charged on a card, so the box goes with the choice.
  $("surchargeField").classList.toggle("hidden", !byCard);
  // Named for what left your pocket, not "receipt total" -- a receipt carries
  // several numbers and this is specifically the one that hit your account.
  $("receiptLabel").textContent = byCard ? "Charged to card" : "Total you paid";
  for (const button of $("taxMode").children) {
    const on = (button.dataset.tax === "in") === state.tax_included;
    button.className = "tog" + (on ? " on tax" : "");
  }

  $("lockBtn").textContent = state.locked ? "Reopen orders" : "Close orders";
  $("lockBtn").classList.toggle("locked", state.locked);

  // rebuilt on every response, so an item is suggestable the moment it's typed
  $("menuList").replaceChildren(...state.suggestions.map((desc) => {
    const option = el("option");
    option.value = desc;
    return option;
  }));

  $("priceBadge").textContent = t.unpriced ? String(t.unpriced) : "";
  // Things still to do in step 4: money to collect, and change to hand back.
  const toDo = state.people.filter((p) => owesYou(p) || changeDue(p)).length;
  $("changeBadge").textContent = toDo ? String(toDo) : "";

  renderMenuAdmin();
  renderTally();
  renderPeople();
  renderCall();
  renderPrices();
  renderChange();

  renderCashBar(t);
}

/* One answer first: are you ahead or behind, all money counted?

   This used to open with the CASH line, which on 23 Sept read "SHORT $14.87
   of your own money" in red above a green "NET surplus $5.13" -- two verdicts
   that looked like they disagreed. Both were true: the cash after change was
   $14.87 short at the till, and the $20 that came in on Venmo covered it. So
   the net is the headline now, the sum that makes it sits under it, and the
   till shortfall is a note explaining where it went. */
function renderCashBar(t) {
  $("barFacts").textContent =
    `${t.people} people · ${t.unpaid} unpaid · items $${t.items}` +
    (t.has_receipt ? ` · ${t.by_card ? "charged" : "receipt"} $${t.due}` : "");

  const verdict = $("barVerdict");
  const sum = $("barMath");
  const notes = $("barNotes");

  if (!t.people) {
    verdict.className = "verdictBig";
    verdict.textContent = "No orders yet";
    sum.textContent = "";
    notes.className = "cashline hidden";
    return;
  }
  if (t.unpriced) {
    verdict.className = "verdictBig a";
    verdict.textContent = `Not final yet — ${t.unpriced} item${t.unpriced > 1 ? "s" : ""}`
      + ` still need${t.unpriced > 1 ? "" : "s"} a price (step 3)`;
    sum.textContent = "";
    notes.className = "cashline hidden";
    return;
  }

  const amount = `$${t.net_surplus}`;
  verdict.className = "verdictBig" + (t.net_zero ? "" : t.net_short ? " r" : " g");
  verdict.textContent = t.net_zero ? "You're exactly even"
    : `${t.has_receipt ? "You're" : "About"} ${amount} ${t.net_short ? "behind" : "ahead"}`
      + (t.has_receipt ? "" : " — an estimate until you type the receipt");

  // The sum behind the headline, pot by pot, in the order the money moved.
  const paidOut = !t.has_receipt ? "estimated bill"
                : t.by_card ? "charged to your card" : "receipt";
  sum.textContent =
    `$${t.cash_on_hand} cash` + (t.cash_change !== "0.00" ? ` (after $${t.cash_change} change)` : "")
    + ` + $${t.venmo_held} Venmo`
    + (t.venmo_change !== "0.00" ? ` (after $${t.venmo_change} sent back)` : "")
    + ` − $${t.due} ${paidOut} = ${t.net_short ? "−" : ""}${amount}`;

  const lines = [];
  if (!t.by_card && t.cash_short_cents > 0) {
    lines.push(t.has_receipt
      ? `At the till your cash was $${t.cash_short} short, so that came out of your own pocket`
        + (!t.net_short && t.any_venmo ? " — the Venmo money pays it back." : ".")
      : `The cash is about $${t.cash_short} short of the bill — bring that much of your own.`);
  }
  if (t.owing) {
    lines.push(`${t.owing} ${t.owing === 1 ? "person still owes" : "people still owe"} you`
      + ` $${t.outstanding} (step 4)`
      + (t.net_short ? ` — once paid you'll be $${t.after_collect}`
                       + ` ${t.after_short ? "behind" : "ahead"}` : ""));
  }
  notes.textContent = lines.join("  ·  ");
  notes.className = "cashline" + (lines.length ? "" : " hidden");
}

function renderTally() {
  const box = $("tally");
  box.replaceChildren(...state.groups.map((g) => {
    const chip = el("span");
    chip.append(el("b", null, `${g.count}× `), g.desc);
    return chip;
  }));
}

function statusClass(person) {
  const base = person.unpriced ? "needs" : person.status === "short" ? "short"
             : person.status === "paid" ? "paid" : "";
  return base + (person.method === "venmo" ? " venmo" : "");
}

// Priced, and has paid nothing or not enough.
const owesYou = (p) => !p.unpriced && (p.status === "unpaid" || p.status === "short");
// Paid more than they owe, and the change hasn't gone back yet.
const changeDue = (p) => p.status === "paid" && p.change !== "0.00" && !p.change_given;

/* Payments made later and the other way -- $15 cash at lunch, the last $2 on
   Venmo that evening. Each can be undone on its own, e.g. a mis-tap. */
function topupChips(person) {
  return (person.topups || []).map((payment, index) => {
    const chip = el("span", "chip topup " + payment.method,
                    `+ $${payment.amount} ${payment.method === "venmo" ? "Venmo" : "cash"}`);
    const undo = el("button", "x", "×");
    undo.type = "button";
    undo.title = "Undo this payment";
    undo.onclick = () => send("/api/remove-topup", { name: person.name, index },
                              `Removed ${person.name}'s $${payment.amount}`);
    chip.append(undo);
    return chip;
  });
}

/* Cash/Venmo box that edits in place -- for people who pay after their order is
   already on the list. Blank clears the payment again. The method matters: only
   cash can be spent at the restaurant. */
function paidField(person) {
  const wrap = el("div", "paidWrap");
  const money = el("div", "money");
  money.append(el("i", null, "$"));

  const input = el("input", "paidIn" + (person.paid == null ? " blank" : ""));
  input.value = person.paid ?? "";
  input.placeholder = "nothing yet";
  input.inputMode = "decimal";
  input.title = "What they gave you";
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } };
  input.onblur = () => {
    const value = input.value.trim();
    if (value === (person.paid ?? "")) return;
    send("/api/payment", { name: person.name, paid: value },
         value ? `${person.name} paid $${value}` : `Cleared ${person.name}'s payment`);
  };

  money.append(input);
  wrap.append(money, methodToggle(person.method, (m) =>
    send("/api/method", { name: person.name, method: m },
         `${person.name} → ${m === "venmo" ? "Venmo" : "cash"}`)),
    ...topupChips(person));   // later payments sit with the first one
  return wrap;
}

/* Two-state pill. Cash is the default, so it is one click only when it isn't. */
function methodToggle(current, onPick) {
  const pill = el("div", "toggle");
  for (const value of ["cash", "venmo"]) {
    const button = el("button", "tog" + (current === value ? " on " + value : ""),
                      value === "cash" ? "Cash" : "Venmo");
    button.type = "button";
    button.onclick = () => { if (current !== value) onPick(value); };
    pill.append(button);
  }
  return pill;
}

let editing = null;   // name of the person whose row is expanded, if any

function renderPeople() {
  const list = $("peopleList");
  if (!state.people.length) {
    editing = null;
    list.replaceChildren(el("div", "empty", "No orders yet — add the first one above."));
    return;
  }
  list.replaceChildren(...state.people.map((person) =>
    person.name === editing ? editPanel(person) : personRow(person)));
}

function personRow(person) {
  const row = el("div", "row " + statusClass(person));
  row.append(el("div", "who", person.name),
             el("div", "what", itemsText(person)));
  row.append(paidField(person));

  const pencil = el("button", "x pencil", "✎");
  pencil.title = `Edit ${person.name}'s order`;
  pencil.onclick = () => { editing = person.name; renderPeople(); };

  const remove = el("button", "x", "×");
  remove.title = `Remove ${person.name}`;
  remove.onclick = () => {
    if (person.paid != null &&
        !confirm(`${person.name} already gave you $${person.paid}. Remove them anyway?`)) return;
    send("/api/delete-person", { name: person.name }, `Removed ${person.name}`);
  };
  // Kept together, so a crowded row never strands one of them on a line alone.
  const actions = el("div", "rowActions");
  actions.append(pencil, remove);
  row.append(actions);
  return row;
}

/* The row expands in place rather than opening a modal, so it stays obvious
   whose order is being changed. Nothing is sent until Save. */
function editPanel(person) {
  const panel = el("div", "row editing " + statusClass(person));
  const form = el("div", "editForm");

  const nameRow = el("div", "editLine");
  nameRow.append(el("span", "editLabel", "Name"));
  const nameInput = el("input", "editName");
  nameInput.value = person.name;
  nameRow.append(nameInput);
  form.append(nameRow);

  /* Wording and backup only. There used to be a price box here too, with
     "later" as its placeholder, and nobody could tell what it was for --
     prices are typed once, in step 3. The server keeps each line's price. */
  const itemBox = el("div", "editItems");
  const addLine = (row = {}) => {
    const drink = Boolean(row.drink);
    const line = el("div", "editLine");
    line.dataset.kind = drink ? "drink" : "";
    line.append(el("span", "editLabel", drink ? "Drink" : "Item"));
    const descInput = el("input", "editDesc");
    descInput.value = row.desc || "";
    descInput.setAttribute("list", "menuList");
    const altInput = el("input", "editAlt");
    altInput.value = row.fallback || "";
    altInput.placeholder = "backup (optional)";
    altInput.setAttribute("list", "menuList");
    altInput.setAttribute("aria-label", "If they're out");
    const drop = el("button", "x", "×");
    drop.type = "button";
    drop.title = drink ? "Remove this drink" : "Remove this item";
    drop.onclick = () => line.remove();
    // Labelled, so a filled-in backup never reads as a second item.
    line.append(descInput, el("span", "editOr", "if out"), altInput, drop);
    itemBox.append(line);
    return descInput;
  };
  for (const row of person.item_rows) addLine(row);
  if (!person.item_rows.length) addLine();
  form.append(itemBox);

  const adders = el("div", "editAdders");
  const addItem = el("button", "linkBtn", "+ add item");
  addItem.type = "button";
  addItem.onclick = () => addLine().focus();
  const addDrink = el("button", "linkBtn", "+ add drink");
  addDrink.type = "button";
  addDrink.onclick = () => addLine({ drink: true }).focus();
  adders.append(addItem, addDrink);
  form.append(adders);

  const close = () => { editing = null; renderPeople(); };
  const save = () => {
    // kind travels with each line: without it a drink came back as food, and
    // its backup was lost, every time an order was edited.
    const items = [...itemBox.querySelectorAll(".editLine")].map((line) => ({
      desc: line.querySelector(".editDesc").value,
      fallback: line.querySelector(".editAlt").value,
      kind: line.dataset.kind === "drink" ? "drink" : undefined,
    }));
    send("/api/edit-person",
         { name: person.name, new_name: nameInput.value, items },
         `Updated ${nameInput.value.trim() || person.name}`);
    editing = null;   // render() repaints the list from the response
  };

  const buttons = el("div", "editButtons");
  const cancel = el("button", "ghostBtn", "Cancel");
  cancel.onclick = close;
  const ok = el("button", "primary", "Save");
  ok.onclick = save;
  buttons.append(cancel, ok);
  form.append(buttons);

  form.onkeydown = (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (event.key === "Enter") { event.preventDefault(); save(); }
  };

  panel.append(form);
  setTimeout(() => nameInput.focus(), 0);
  return panel;
}

function renderCall() {
  $("callTitle").textContent =
    `${state.place || "Lunch"} — ${new Date(date + "T12:00").toLocaleDateString(undefined,
      { weekday: "long", month: "long", day: "numeric" })}`;

  $("callList").replaceChildren(...(state.groups.length
    ? state.groups.map((g) => {
        const item = el("li");
        const words = el("div");
        words.append(el("span", null, g.desc));
        // What to get if they're out: read to the restaurant with the order.
        if (g.fallbacks && g.fallbacks.length) {
          words.append(el("small", "fallbacks", "if they're out: "
            + g.fallbacks.map((f) => `${f.desc}` + (f.count > 1 ? ` ×${f.count}` : ""))
                         .join(", ")));
        }
        item.append(el("div", "count", `${g.count}×`), words);
        return item;
      })
    : [el("div", "empty", "Nothing ordered yet.")]));

  // What the till should come to: tax on top unless this place's prices
  // already include it, and the card fee only when paying by card.
  const t = state.totals;
  const totals = $("callTotals");
  totals.replaceChildren("Subtotal ", el("b", null, `$${t.items}`),
                         " · expected ", el("b", null, `$${t.estimate}`),
                         ` (${t.charge_basis})`);
  if (t.unpriced) {
    totals.append(` · ${t.unpriced} item(s) unpriced, so this is incomplete`);
  }
}

function callText() {
  const t = state.totals;
  return [`${state.place || "Lunch"} — ${date}`, "",
          ...state.groups.map((g) => `${g.count}x ${g.desc}`
            + ((g.fallbacks && g.fallbacks.length)
               ? `  (if out: ${g.fallbacks.map((f) => f.desc + (f.count > 1 ? ` x${f.count}` : "")).join(", ")})`
               : "")), "",
          `Subtotal: $${t.items}`, `Expected: $${t.estimate} (${t.charge_basis})`].join("\n");
}

function renderPrices() {
  const box = $("priceRows");
  if (!state.groups.length) {
    box.replaceChildren(el("div", "empty", "Nothing to price yet."));
    return;
  }
  box.replaceChildren(...state.groups.map((g) => {
    const row = el("div", "row " + (g.price ? "paid" : "needs"));
    row.append(el("div", "count", `${g.count}×`),
               el("div", "who", g.desc),
               el("div", "what", g.names.join(", ")));
    if (g.mixed) row.append(el("div", "chip a", "mixed prices"));

    const wrap = el("div", "money");
    wrap.append(el("i", null, "$"));
    const input = el("input", "priceIn");
    input.value = g.price;
    input.placeholder = "from receipt";
    input.inputMode = "decimal";
    const commit = () => {
      if (input.value.trim() === g.price) return;
      send("/api/price", { desc: g.desc, price: input.value },
           `${g.desc} — ${g.count} order${g.count > 1 ? "s" : ""} updated`);
    };
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } };
    input.onblur = commit;
    wrap.append(input);
    row.append(wrap);
    return row;
  }));

  const done = state.priced_groups, all = state.total_groups;
  $("priceBadge").textContent = done < all ? String(all - done) : "";
  renderMergeOffers();
  checkReceipt();
  checkCount();
}

/* Lines that look like one dish written several ways.

   Six rice plates written four ways showed as 2, 2, 1 and 1, so the count
   never reached six and the receipt's five could not be contradicted. Merging
   makes it one line of six. Every wording and its count is listed, because the
   organiser is the one confirming these really are the same dish -- the server
   only suggests, and it rewrites descriptions, never prices. */
function renderMergeOffers() {
  const offers = state.merge_suggestions || [];
  $("mergeOffers").replaceChildren(...offers.map((offer) => {
    const box = el("div", "mergeOffer");
    box.append(el("b", null,
      `${offer.variants.length} lines look like the same dish (${offer.total} total)`));

    const list = el("ul", "mergeList");
    for (const v of offer.variants) {
      list.append(el("li", null, `${v.count}×  ${v.desc}`));
    }
    box.append(list);

    const go = el("button", "primary", `Merge into "${offer.into}"`);
    go.type = "button";
    go.onclick = () => send("/api/merge-items",
      { into: offer.into, from: offer.variants.map((v) => v.desc) },
      `Merged into one line of ${offer.total}`);
    box.append(go);
    return box;
  }));
}

/* Does the order match the receipt?

   The till is modelled forwards on the server -- food, then 4.712% tax unless
   this place's prices already include it, then the card fee when the card
   paid -- and the sum is shown, so a match or a gap can be followed by eye.
   The first version assumed Doner Shack's model everywhere (tax already in the
   prices), so at every place that adds tax on top a correct receipt read "the
   fee looks like 4.7%" and could never go green. */
function checkReceipt() {
  const verdict = $("receiptVerdict");
  const t = state.totals;
  const raw = $("receiptTotal").value.trim();
  const typed = raw ? Math.round(parseFloat(raw.replace(/[$,]/g, "")) * 100) : null;

  if (raw && Number.isNaN(typed)) {
    verdict.className = "verdict r"; verdict.textContent = "not a number";
    return;
  }
  if (!has(t.expected_charge)) {
    verdict.className = "verdict"; verdict.textContent = "";
    return;
  }

  const parts = [];
  let offer = null;
  const countWrong = has(t.count_diff) && t.count_diff !== 0;
  const moneyWrong = has(t.charge_diff_cents) && !t.charge_ok;

  /* Two things can go wrong and they want different answers, so say which.
     If the receipt lists a different NUMBER of things, something was never
     rung up and it is the order. If it lists the right number for the wrong
     money, the order was fine and it is a price, the tax or the fee. */
  if (countWrong) {
    parts.push(`${t.keyed_items} keyed, ${t.receipt_items_count} on the receipt`);
  } else if (has(t.count_diff)) {
    parts.push(`${t.keyed_items} items`);
  }
  if (t.unpriced) parts.push(`${t.unpriced} still unpriced`);

  // The sum the till should have done.
  const sum = `$${t.items}` + (t.tax_on_top ? ` + $${t.tax_part} tax` : "")
    + (t.fee_part ? ` + $${t.fee_part} card fee` : "") + ` = $${t.expected_charge}`;

  if (moneyWrong) {
    parts.push(`charged $${t.charge_diff} ${t.charge_diff_cents > 0 ? "more" : "less"}`
               + ` than ${sum}`);
    if (!countWrong && t.tax_switch) {
      // The other tax setting would explain it exactly. Offered, never
      // assumed: guessing could hide a missing item that costs about 4.7%.
      offer = el("button", "soft small", t.tax_on_top ? "Their prices already include tax"
                                                      : "They add tax on top");
      offer.type = "button";
      offer.onclick = () => send("/api/receipt", { tax_included: t.tax_on_top },
        `Remembered for ${state.place || "this place"}`);
    } else if (!countWrong && t.by_card && has(t.implied_pct) && t.implied_pct > 0) {
      parts.push(`the card fee looks like ${t.implied_pct}%`
                 + (has(t.expected_pct) ? `, not ${t.expected_pct}%` : ""));
    } else if (!countWrong && has(t.implied_pct) && t.implied_pct < 0) {
      parts.push(`charged less than the food ${t.tax_on_top ? "and its tax " : ""}`
                 + "— check the prices");
    }
  } else if (has(t.charge_diff_cents)) {
    parts.push(`✓ ${sum}` + (t.charge_diff_cents === 0 ? " — matches"
      : ` — ${Math.abs(t.charge_diff_cents)}¢ off, just rounding`));
  } else {
    parts.push(`expecting ${sum}`);
  }
  if (t.restaurant_change) parts.push(`they gave you $${t.restaurant_change} back`);

  /* Red is reserved for "your order and the receipt disagree" -- the signal
     that catches a plate nobody made. A fee that crept up is real money but is
     not a mis-rung order, so it gets amber and keeps red meaning one thing.
     Amber also covers "not checked yet": green must never appear off a
     comparison that never ran, which is how this went wrong before. */
  if (countWrong) {
    verdict.className = "verdict r";
  } else if (moneyWrong) {
    verdict.className = "verdict a";
  } else if (t.charge_ok && !t.unpriced && has(t.charge_diff_cents)) {
    verdict.className = "verdict g";
  } else {
    verdict.className = "verdict a";
    if (!has(t.charge_diff_cents)) parts.push("type what you paid to check the money");
  }
  verdict.replaceChildren(el("span", null, parts.join(" · ")), ...(offer ? [offer] : []));
}

const has = (v) => v !== null && v !== undefined;

/* The app knows what it thinks you are holding; only you can see the actual
   notes. On 28 Aug the totals agreed at $244 but the split did not -- one $15
   Venmo payment had been filed as cash -- and finding that took a manual
   reconciliation. When the total is right and only the split is wrong, the
   orders that could explain it are nameable, so name them. */
function checkCount() {
  const verdict = $("countVerdict");
  const t = state.totals;
  const cashRaw = $("countCash").value.trim();
  const venmoRaw = $("countVenmo").value.trim();

  if (!cashRaw && !venmoRaw) {
    verdict.className = "verdict"; verdict.textContent = ""; return;
  }
  const cents = (text) => Math.round(parseFloat(text.replace(/[$,]/g, "")) * 100);
  const cash = cashRaw ? cents(cashRaw) : null;
  const venmo = venmoRaw ? cents(venmoRaw) : null;
  if ((cashRaw && Number.isNaN(cash)) || (venmoRaw && Number.isNaN(venmo))) {
    verdict.className = "verdict r"; verdict.textContent = "not a number";
    return;
  }
  if (cash === null || venmo === null) {
    verdict.className = "verdict"; verdict.textContent = "enter both to check";
    return;
  }

  const money = (c) => `$${(Math.abs(c) / 100).toFixed(2)}`;
  const expectCash = Math.round(parseFloat(t.cash_on_hand.replace(/,/g, "")) * 100);
  const expectVenmo = Math.round(parseFloat(t.venmo_held.replace(/,/g, "")) * 100);
  const cashGap = cash - expectCash;
  const totalGap = (cash + venmo) - (expectCash + expectVenmo);

  if (totalGap === 0 && cashGap === 0) {
    verdict.className = "verdict g";
    verdict.textContent = `✓ cash and Venmo both match`;
    return;
  }
  if (totalGap === 0) {
    // Same money, wrong pot. Whose payment is exactly the size of the gap?
    const size = Math.abs(cashGap);
    const wrongWay = cashGap < 0 ? "cash" : "Venmo";
    const rightWay = cashGap < 0 ? "Venmo" : "cash";
    const suspects = (state.people || [])
      .filter((p) => p.method === wrongWay.toLowerCase() && p.paid
                     && Math.round(parseFloat(p.paid.replace(/,/g, "")) * 100) === size)
      .map((p) => p.name);
    verdict.className = "verdict r";
    verdict.textContent =
      `Total is right. ${money(cashGap)} is filed as ${wrongWay} but you're holding `
      + `it in ${rightWay}.`
      + (suspects.length ? `  Paid exactly ${money(cashGap)} ${wrongWay}: `
                           + `${suspects.join(", ")} — switch whoever sent it.` : "");
    return;
  }
  verdict.className = "verdict r";
  verdict.textContent =
    `${money(totalGap)} ${totalGap < 0 ? "short" : "over"} overall — `
    + `expected cash $${t.cash_on_hand} and Venmo $${t.venmo_held}.`;
}

function saveReceipt() {
  send("/api/receipt", { receipt: $("receiptTotal").value,
                         receipt_items: $("receiptItems").value,
                         surcharge_pct: $("surchargePct").value,
                         restaurant_paid: $("cashHanded").value }, "Receipt saved");
}

function renderChange() {
  const box = $("changeList");
  if (!state.people.length) {
    box.replaceChildren(el("div", "empty", "Nobody to settle up with yet."));
    return;
  }

  // Separate headed groups, one per thing to do: collecting money, handing
  // over bills and sending Venmo back are different physical actions. People
  // who still owed you used to sit under "Nothing owed" -- meaning nothing
  // owed TO them -- which read as if they were square.
  const owedBack = (p) => p.status === "paid" && p.change !== "0.00";
  const groups = [
    ["Still owes you", state.people.filter(owesYou)],
    ["Hand back cash", state.people.filter((p) => owedBack(p) && p.method === "cash")],
    ["Send back on Venmo", state.people.filter((p) => owedBack(p) && p.method === "venmo")],
    ["Needs a price first", state.people.filter((p) => p.unpriced)],
    ["Square", state.people.filter((p) => !p.unpriced && p.status === "paid" && !owedBack(p))],
  ];

  const nodes = [];
  for (const [title, people] of groups) {
    if (!people.length) continue;
    people.sort((a, b) => Number(a.change_given) - Number(b.change_given));
    nodes.push(el("h3", "groupHead", `${title} (${people.length})`));
    nodes.push(...people.map(changeRow));
  }
  box.replaceChildren(...nodes);
}

function changeRow(person) {
  const row = el("div", "row " + statusClass(person) + (person.change_given ? " done" : ""));
  row.append(el("div", "who", person.name),
             el("div", "what", itemsText(person)));

  if (person.unpriced) {
    row.append(el("div", "chip a", "needs a price first"));
    return row;
  }

  if (owesYou(person)) {
    row.append(el("div", "chip r", person.status === "short"
      ? `still owes $${person.outstanding.replace(/\.00$/, "")} of $${person.owed}`
      : `owes $${person.owed}`));
    row.append(paidField(person));
    const ask = requestLink(person);
    if (ask) row.append(ask);
    row.append(settleButtons(person));
    return row;
  }

  row.append(el("div", "chip", `owed $${person.owed}`));
  row.append(paidField(person));
  row.append(el("div", "big g", person.change === "0.00" ? "square" : `$${person.change} back`));

  if (person.change !== "0.00") {
    const tick = el("label", "tick");
    const check = el("input");
    check.type = "checkbox";
    check.checked = person.change_given;
    check.onchange = () => send("/api/change-given",
                                { name: person.name, given: check.checked });
    tick.append(check, el("span", null, person.method === "venmo" ? "sent back" : "handed over"));
    row.append(tick);
  }
  return row;
}

/* Venmo payers who still owe get a charge link for exactly what is left --
   all of it before they pay, just the rest after a short payment. This used
   to appear only once they had paid, which is backwards. */
function requestLink(person) {
  if (person.method !== "venmo") return null;
  if (person.venmo_link) {
    const link = el("a", "venmoBtn", `Request $${person.outstanding.replace(/\.00$/, "")}`);
    link.href = person.venmo_link;
    link.target = "_blank";
    link.rel = "noopener";
    return link;
  }
  const ask = el("button", "venmoBtn ghostBtn", "+ Venmo username");
  ask.type = "button";
  ask.onclick = () => {
    const handle = prompt(`${person.name}'s Venmo username?`, "");
    if (handle) send("/api/venmo-user", { name: person.name, venmo_user: handle },
                     `Saved ${person.name}'s Venmo`);
  };
  return ask;
}

/* They've paid what they owed. The page only says HOW it came in; the server
   works out the amount from their priced items, so a double tap can't record
   it twice. The way they said they'd pay comes first. */
function settleButtons(person) {
  const wrap = el("div", "settleBtns");
  const amount = person.outstanding.replace(/\.00$/, "");
  const ways = [["cash", `Paid $${amount} in cash`], ["venmo", `Paid $${amount} on Venmo`]];
  if (person.method === "venmo") ways.reverse();
  for (const [method, label] of ways) {
    const button = el("button", `soft small settle ${method}`, label);
    button.type = "button";
    button.onclick = () => send("/api/settle", { name: person.name, method },
                                `${person.name} is square`);
    wrap.append(button);
  }
  return wrap;
}

// --- calendar -------------------------------------------------------------

let calYear, calMonth;

function openCalendar() {
  const box = $("calendar");
  if (!box.classList.contains("hidden")) return closeCalendar();
  const seed = new Date(date + "T12:00");
  calYear = seed.getFullYear();
  calMonth = seed.getMonth();
  drawCalendar();
  const anchor = $("pickDay").getBoundingClientRect();
  box.style.top = `${anchor.bottom + 8}px`;
  box.style.left = `${Math.max(8, anchor.left)}px`;
  box.classList.remove("hidden");
  setTimeout(() => document.addEventListener("click", outsideCalendar), 0);
}

function closeCalendar() {
  $("calendar").classList.add("hidden");
  document.removeEventListener("click", outsideCalendar);
}

function outsideCalendar(event) {
  if (!$("calendar").contains(event.target) && event.target !== $("pickDay")) closeCalendar();
}

function drawCalendar() {
  const box = $("calendar");
  box.replaceChildren();

  const head = el("div", "calHead");
  const back = el("button", null, "◀");
  back.onclick = (e) => { e.stopPropagation(); calMonth--; normaliseMonth(); drawCalendar(); };
  const fwd = el("button", null, "▶");
  fwd.onclick = (e) => { e.stopPropagation(); calMonth++; normaliseMonth(); drawCalendar(); };
  head.append(back, el("strong", null,
    new Date(calYear, calMonth, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" })), fwd);

  const grid = el("div", "calGrid");
  for (const dow of ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]) grid.append(el("div", "dow", dow));

  const first = new Date(calYear, calMonth, 1);
  const lead = (first.getDay() + 6) % 7;                       // Monday-first
  const days = new Date(calYear, calMonth + 1, 0).getDate();
  const today = new Date().toLocaleDateString("en-CA");

  for (let i = 0; i < lead; i++) grid.append(el("div"));
  for (let d = 1; d <= days; d++) {
    const iso = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const button = el("button", null, String(d));
    if (savedDays.includes(iso)) button.classList.add("has");
    if (iso === today) button.classList.add("today");
    if (iso === date) button.classList.add("sel");
    button.onclick = (e) => { e.stopPropagation(); closeCalendar(); goto(iso); };
    grid.append(button);
  }

  box.append(head, grid, el("div", "calLegend", "●  has orders"));
}

function normaliseMonth() {
  calYear += Math.floor(calMonth / 12);
  calMonth = ((calMonth % 12) + 12) % 12;
}

// --- day navigation -------------------------------------------------------

function goto(iso) { date = iso; load().catch((e) => toast(e.message, true)); }

function shift(days) {
  const d = new Date(date + "T12:00");
  d.setDate(d.getDate() + days);
  goto(d.toLocaleDateString("en-CA"));
}

// --- wiring ---------------------------------------------------------------

let newMethod = "cash";   // sticky: a run of Venmo payers shouldn't need re-clicking

$("fMethod").onclick = (event) => {
  const button = event.target.closest(".tog");
  if (!button) return;
  newMethod = button.dataset.m;
  for (const b of $("fMethod").children) {
    b.className = "tog" + (b === button ? " on " + newMethod : "");
  }
};

// The same lines as the ordering page: one thing per line, backups optional.
// Compact: one tight row per item, since the organiser is typing for others.
const newLines = ItemLines.create($("fLines"), { list: "menuList", compact: true,
                                                 more: "another item (optional)" });

$("orderForm").onsubmit = async (event) => {
  event.preventDefault();
  const name = $("fName").value.trim();
  const items = newLines.values();
  const drink = $("fDrink").value.trim();
  if (!name) return;
  if (!items.length && !drink) { newLines.focus(); return; }
  try {
    state = await api("/api/order",
                      { name, items, drink, drink_fallback: $("fDrinkAlt").value.trim(),
                        paid: $("fPaid").value, method: newMethod });
    render();
    $("fName").value = $("fPaid").value = $("fDrink").value = $("fDrinkAlt").value = "";
    newLines.clear();
    $("fName").focus();
    const count = items.length + (drink ? 1 : 0);
    toast(count === 1 ? `Added ${items.length ? items[0].desc : drink} for ${name}`
                      : `Added ${count} things for ${name}`);
  } catch (err) {
    toast(err.message, true);
  }
};

// Remembered per restaurant, like the surcharge: set it once at a place whose
// prices already include tax and every later visit knows.
$("taxMode").onclick = (event) => {
  const button = event.target.closest(".tog");
  if (!button) return;
  const included = button.dataset.tax === "in";
  if (included === state.tax_included) return;
  send("/api/receipt", { tax_included: included },
       included ? "Tax is in the prices here — remembered" : "Tax added on top here — remembered");
};

for (const id of ["receiptTotal", "receiptItems", "surchargePct", "cashHanded"]) {
  $(id).onchange = saveReceipt;
  $(id).onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } };
}

$("payMethod").onclick = (event) => {
  const button = event.target.closest(".tog");
  if (!button || button.dataset.m === state.restaurant_method) return;
  send("/api/receipt", { method: button.dataset.m },
       button.dataset.m === "card" ? "Paying by card" : "Paying with cash");
};

$("menuPick").onchange = (event) => {
  const files = [...event.target.files];
  event.target.value = "";        // so picking the same file twice still fires
  if (files.length) uploadMenuFiles(files);
};

// Clicking the zone opens the picker via the <label>. Catch it first so a blank
// Place explains itself instead of opening a picker that will only fail.
$("menuPickWrap").addEventListener("click", (event) => {
  if (needsPlace()) event.preventDefault();
});

const dropZone = $("menuPickWrap");
for (const name of ["dragenter", "dragover"]) {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("over");
  });
}
for (const name of ["dragleave", "drop"]) {
  dropZone.addEventListener(name, () => dropZone.classList.remove("over"));
}
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  const files = [...event.dataTransfer.files];
  if (files.length) uploadMenuFiles(files);
});

$("menuUrlAdd").onclick = addMenuLink;
// Enter in the box adds it, rather than doing nothing or submitting a form.
$("menuUrl").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); addMenuLink(); }
});

$("place").onchange = () => send("/api/place", { place: $("place").value }, "Place saved");
$("place").onblur = () => { if ($("place").value !== state.place) $("place").onchange(); };

$("prevDay").onclick = () => shift(-1);
$("nextDay").onclick = () => shift(1);
$("todayBtn").onclick = () => goto(new Date().toLocaleDateString("en-CA"));
$("pickDay").onclick = (e) => { e.stopPropagation(); openCalendar(); };
$("receiptTotal").oninput = checkReceipt;
$("countCash").oninput = checkCount;
$("countVenmo").oninput = checkCount;

$("copyBtn").onclick = async () => {
  const text = callText();
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    const area = el("textarea");                 // clipboard API needs a secure context
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast("Copied to clipboard");
  }
};

$("lockBtn").onclick = () =>
  send("/api/lock", { locked: !state.locked },
       state.locked ? "Orders reopened" : "Orders closed");

$("steps").onclick = (event) => {
  const button = event.target.closest(".step");
  if (!button) return;
  step = button.dataset.step;
  document.querySelectorAll(".step").forEach((s) => s.classList.toggle("active", s === button));
  document.querySelectorAll(".panel").forEach(
    (p) => p.classList.toggle("hidden", p.dataset.panel !== step));
  if (step === "orders") $("fName").focus();
};

document.addEventListener("keydown", (event) => {
  if (event.key >= "1" && event.key <= "4" && (event.altKey || event.metaKey)) {
    event.preventDefault();
    document.querySelectorAll(".step")[+event.key - 1].click();
  }
});

load().then(() => $("fName").focus()).catch((err) => toast(err.message, true));
