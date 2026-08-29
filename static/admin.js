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
    if (file.kind === "pdf") {
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

function render() {
  const t = state.totals;

  $("dayLabel").textContent = new Date(date + "T12:00").toLocaleDateString(undefined,
    { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  if ($("place") !== document.activeElement) $("place").value = state.place;
  for (const [id, value] of [["receiptTotal", state.receipt],
                             ["receiptSubtotal", state.receipt_subtotal],
                             ["receiptItems", state.receipt_items],
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
  // Named for what left your pocket, not "receipt total" -- a receipt carries
  // three different numbers and the subtotal field above wants a different one.
  $("receiptLabel").textContent = byCard ? "Charged to card" : "Total you paid";

  $("lockBtn").textContent = state.locked ? "Reopen orders" : "Close orders";
  $("lockBtn").classList.toggle("locked", state.locked);

  // rebuilt on every response, so an item is suggestable the moment it's typed
  $("menuList").replaceChildren(...state.suggestions.map((desc) => {
    const option = el("option");
    option.value = desc;
    return option;
  }));

  $("priceBadge").textContent = t.unpriced ? String(t.unpriced) : "";
  const owedCount = state.people.filter(
    (p) => p.status === "paid" && p.change !== "0.00" && !p.change_given).length;
  $("changeBadge").textContent = owedCount ? String(owedCount) : "";

  renderMenuAdmin();
  renderTally();
  renderPeople();
  renderCall();
  renderPrices();
  renderChange();

  renderCashBar(t);
}

/* Paying by card asks a different question than paying by cash: not "do I have
   enough bills?" but "am I square once the card is charged?" */
function renderCashBar(t) {
  $("cashLine").textContent =
    `${t.people} people · ${t.unpaid} unpaid · items $${t.items} · ` +
    (t.has_receipt ? `receipt $${t.due}` : `estimated bill $${t.bill}`);

  const label = t.has_receipt ? "receipt" : "bill";
  const main = $("cashRow");
  const venmo = $("venmoRow");
  const net = $("netRow");

  if (t.by_card) {
    main.className = "verdictBig";
    main.textContent =
      `COLLECTED  $${t.collected}  (cash $${t.cash_in} · Venmo $${t.venmo_in})` +
      (t.change_out !== "0.00" ? `  ·  $${t.change_out} to return` : "");
    venmo.className = "cashline";
    venmo.textContent = `CARD  $${t.due} charged` +
      (t.has_receipt ? "" : `  (estimated — no receipt entered yet)`);
  } else {
    if (t.cash_short_cents > 0) {
      main.className = "verdictBig r";
      main.textContent =
        `CASH  in $${t.cash_in} · change out $${t.cash_change} · on hand $${t.cash_on_hand}` +
        ` · ${label} $${t.due}  →  ⚠ SHORT $${t.cash_short} of your own money`;
    } else {
      main.className = "verdictBig g";
      main.textContent =
        `CASH  in $${t.cash_in} · change out $${t.cash_change} · on hand $${t.cash_on_hand}` +
        ` · ${label} $${t.due}  →  $${t.pocket} left after paying`;
    }
    venmo.className = "cashline" + (t.any_venmo ? "" : " hidden");
    venmo.textContent = `VENMO  in $${t.venmo_in}` +
      (t.venmo_change !== "0.00" ? ` · $${t.venmo_change} to send back`
                                 : " · nothing to send back");
  }

  if (t.unpriced) {
    net.className = "cashline";
    net.textContent =
      `${t.unpriced} item${t.unpriced > 1 ? "s" : ""} still unpriced — these totals are incomplete`;
  } else if (t.by_card) {
    net.className = "cashline " + (t.pocket_short ? "r" : "g");
    net.textContent = `NET  ` + (t.pocket_short
      ? `you are down $${t.pocket_abs} of your own money`
      : `$${t.pocket_abs} in your favour (cash $${t.cash_on_hand} + Venmo $${t.venmo_held})`);
  } else {
    net.className = "cashline " + (t.net_short ? "r" : "g");
    net.textContent = `NET  ` +
      (t.net_short ? `short $${t.net_surplus} overall` : `surplus $${t.net_surplus} overall`);
  }
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
         `${person.name} → ${m === "venmo" ? "Venmo" : "cash"}`)));
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
             el("div", "what", person.items.join(", ") || "—"));
  row.append(paidField(person));

  const pencil = el("button", "x pencil", "✎");
  pencil.title = `Edit ${person.name}'s order`;
  pencil.onclick = () => { editing = person.name; renderPeople(); };
  row.append(pencil);

  const remove = el("button", "x", "×");
  remove.title = `Remove ${person.name}`;
  remove.onclick = () => {
    if (person.paid != null &&
        !confirm(`${person.name} already gave you $${person.paid}. Remove them anyway?`)) return;
    send("/api/delete-person", { name: person.name }, `Removed ${person.name}`);
  };
  row.append(remove);
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

  const itemBox = el("div", "editItems");
  const addItemRow = (desc = "", price = "") => {
    const line = el("div", "editLine");
    line.append(el("span", "editLabel", "Item"));
    const descInput = el("input", "editDesc");
    descInput.value = desc;
    descInput.setAttribute("list", "menuList");
    const money = el("div", "money");
    money.append(el("i", null, "$"));
    const priceInput = el("input", "editPrice");
    priceInput.value = price;
    priceInput.placeholder = "later";
    priceInput.inputMode = "decimal";
    money.append(priceInput);
    const drop = el("button", "x", "×");
    drop.title = "Remove this item";
    drop.onclick = () => line.remove();
    line.append(descInput, money, drop);
    itemBox.append(line);
    return descInput;
  };
  for (const item of person.item_rows) addItemRow(item.desc, item.price);
  if (!person.item_rows.length) addItemRow();
  form.append(itemBox);

  const add = el("button", "linkBtn", "+ add item");
  add.onclick = () => addItemRow().focus();
  form.append(add);

  const close = () => { editing = null; renderPeople(); };
  const save = () => {
    const items = [...itemBox.querySelectorAll(".editLine")].map((line) => ({
      desc: line.querySelector(".editDesc").value,
      price: line.querySelector(".editPrice").value,
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
        item.append(el("div", "count", `${g.count}×`), el("div", null, g.desc));
        return item;
      })
    : [el("div", "empty", "Nothing ordered yet.")]));

  const t = state.totals;
  $("callTotals").innerHTML =
    `Subtotal <b>$${t.items}</b> &nbsp;·&nbsp; with tax <b>$${t.bill}</b>` +
    (t.unpriced ? ` &nbsp;·&nbsp; ${t.unpriced} item(s) unpriced, so this is incomplete` : "");
}

function callText() {
  const t = state.totals;
  return [`${state.place || "Lunch"} — ${date}`, "",
          ...state.groups.map((g) => `${g.count}x ${g.desc}`), "",
          `Subtotal: $${t.items}`, `With tax:  $${t.bill}`].join("\n");
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

   This used to compare the typed total against bill_cents -- items plus 4.712%
   GET -- but a receipt total is whatever the restaurant charged. Doner Shack
   prices include tax and add 3% for the card, so the two could never agree:
   on 28 Aug it read "off by $23.50" on a day a flawless order would still have
   read "off by $3.87". Permanently red means ignored, and a missing plate went
   through. So compare like with like instead -- untaxed items against the
   receipt's own subtotal, and a plain count of things -- which reads zero when
   the order is actually right. The old estimate is still shown when no
   subtotal has been typed, so older days behave as before. */
function checkReceipt() {
  const verdict = $("receiptVerdict");
  const t = state.totals;
  const raw = $("receiptTotal").value.trim();
  const typed = raw ? Math.round(parseFloat(raw.replace(/[$,]/g, "")) * 100) : null;

  if (raw && Number.isNaN(typed)) {
    verdict.className = "verdict r"; verdict.textContent = "not a number";
    return;
  }

  const parts = [];
  let bad = false;

  if (t.subtotal_diff_cents !== null && t.subtotal_diff_cents !== undefined) {
    if (t.subtotal_diff_cents === 0) parts.push("✓ items match the receipt");
    else parts.push(`$${t.subtotal_diff} ${t.subtotal_diff_cents > 0 ? "over" : "under"}`);
    bad = bad || t.subtotal_diff_cents !== 0;
  }
  if (t.count_diff !== null && t.count_diff !== undefined) {
    if (t.count_diff === 0) parts.push(`${t.keyed_items} items`);
    else parts.push(`${t.keyed_items} keyed, ${t.receipt_items_count} on the receipt`);
    bad = bad || t.count_diff !== 0;
  }
  if (t.surcharge_pct !== null && t.surcharge_pct !== undefined) {
    parts.push(`${t.surcharge_pct}% surcharge`);
  }

  // Nothing to compare against yet -- fall back to the old estimate.
  if (!parts.length) {
    if (!raw) { verdict.className = "verdict"; verdict.textContent = ""; return; }
    const diff = typed - t.bill_cents;
    bad = diff !== 0;
    parts.push(diff === 0 ? `✓ matches my total of $${t.bill}`
                          : `off by $${(Math.abs(diff) / 100).toFixed(2)} from my $${t.bill}`
                            + " — type the receipt subtotal for a real check");
  }
  if (t.restaurant_change) parts.push(`they gave you $${t.restaurant_change} back`);

  verdict.className = "verdict " + (bad ? "r" : "g");
  verdict.textContent = parts.join(" · ");
}

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
                         receipt_subtotal: $("receiptSubtotal").value,
                         receipt_items: $("receiptItems").value,
                         restaurant_paid: $("cashHanded").value }, "Receipt saved");
}

function renderChange() {
  const box = $("changeList");
  if (!state.people.length) {
    box.replaceChildren(el("div", "empty", "Nobody to settle up with yet."));
    return;
  }

  // Two different physical actions -- handing over bills vs sending money back --
  // so they get separate headed groups rather than one mixed list.
  const owedBack = (p) => p.status === "paid" && p.change !== "0.00";
  const groups = [
    ["Hand back cash", state.people.filter((p) => owedBack(p) && p.method === "cash")],
    ["Send back on Venmo", state.people.filter((p) => owedBack(p) && p.method === "venmo")],
    ["Nothing owed", state.people.filter((p) => !owedBack(p))],
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
             el("div", "what", person.items.join(", ") || "—"));

  if (person.unpriced) {
    row.append(el("div", "chip a", "needs a price first"));
    return row;
  }
  row.append(el("div", "chip", `owed $${person.owed}`));
  row.append(paidField(person));

  if (person.paid == null) return row;   // nothing to settle until they pay

  if (person.status === "short") {
    row.append(el("div", "big r", `still owes $${person.change.replace("-", "")}`));
    return row;
  }
  row.append(el("div", "big g", person.change === "0.00" ? "square" : `$${person.change} back`));

  // Venmo people get a tap-through charge link for the rounded amount
  if (person.method === "venmo" && !person.change_given) {
    if (person.venmo_link) {
      const link = el("a", "venmoBtn", `Request $${person.owed}`);
      link.href = person.venmo_link;
      link.target = "_blank";
      link.rel = "noopener";
      row.append(link);
    } else {
      const ask = el("button", "venmoBtn ghostBtn", "+ Venmo username");
      ask.onclick = () => {
        const handle = prompt(`${person.name}'s Venmo username?`, "");
        if (handle) send("/api/venmo-user", { name: person.name, venmo_user: handle },
                         `Saved ${person.name}'s Venmo`);
      };
      row.append(ask);
    }
  }

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

$("orderForm").onsubmit = async (event) => {
  event.preventDefault();
  const name = $("fName").value.trim();
  const item = $("fItem").value.trim();
  if (!name || !item) return;
  try {
    state = await api("/api/order",
                      { name, item, paid: $("fPaid").value, method: newMethod });
    render();
    $("fName").value = $("fItem").value = $("fPaid").value = "";
    $("fName").focus();
    toast(`Added ${item} for ${name}`);
  } catch (err) {
    toast(err.message, true);
  }
};

for (const id of ["receiptTotal", "receiptSubtotal", "receiptItems", "cashHanded"]) {
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
