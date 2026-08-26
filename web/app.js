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

// --- render ---------------------------------------------------------------

function render() {
  const t = state.totals;

  $("dayLabel").textContent = new Date(date + "T12:00").toLocaleDateString(undefined,
    { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  if ($("place") !== document.activeElement) $("place").value = state.place;
  for (const [id, value] of [["receiptTotal", state.receipt],
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
  $("receiptLabel").textContent = byCard ? "Charged to card" : "Receipt total";

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
  checkReceipt();
}

function checkReceipt() {
  const verdict = $("receiptVerdict");
  const raw = $("receiptTotal").value.trim();
  const t = state.totals;

  if (!raw) { verdict.className = "verdict"; verdict.textContent = ""; return; }
  const typed = Math.round(parseFloat(raw.replace(/[$,]/g, "")) * 100);
  if (Number.isNaN(typed)) {
    verdict.className = "verdict r"; verdict.textContent = "not a number";
    return;
  }
  const parts = [];
  const diff = typed - t.bill_cents;
  if (diff === 0) parts.push(`✓ matches my total of $${t.bill}`);
  else parts.push(`off by $${(Math.abs(diff) / 100).toFixed(2)} from my $${t.bill}`);
  if (t.restaurant_change) parts.push(`they gave you $${t.restaurant_change} back`);
  verdict.className = "verdict " + (diff === 0 ? "g" : "r");
  verdict.textContent = parts.join(" · ");
}

function saveReceipt() {
  send("/api/receipt", { receipt: $("receiptTotal").value,
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

for (const id of ["receiptTotal", "cashHanded"]) {
  $(id).onchange = saveReceipt;
  $(id).onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } };
}

$("payMethod").onclick = (event) => {
  const button = event.target.closest(".tog");
  if (!button || button.dataset.m === state.restaurant_method) return;
  send("/api/receipt", { method: button.dataset.m },
       button.dataset.m === "card" ? "Paying by card" : "Paying with cash");
};

$("place").onchange = () => send("/api/place", { place: $("place").value }, "Place saved");
$("place").onblur = () => { if ($("place").value !== state.place) $("place").onchange(); };

$("prevDay").onclick = () => shift(-1);
$("nextDay").onclick = () => shift(1);
$("todayBtn").onclick = () => goto(new Date().toLocaleDateString("en-CA"));
$("pickDay").onclick = (e) => { e.stopPropagation(); openCalendar(); };
$("receiptTotal").oninput = checkReceipt;

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

$("quitBtn").onclick = async () => {
  if (!confirm("Stop the Lunch Calculator? Everything is already saved.")) return;
  await fetch("/api/quit", { method: "POST" }).catch(() => {});
  document.body.innerHTML =
    '<p style="padding:60px;text-align:center;color:#6b7787">' +
    "Stopped. You can close this tab.</p>";
};

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
