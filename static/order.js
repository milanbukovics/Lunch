/* Public ordering page. Deliberately knows nothing about money -- the server
   never sends it a price, so there is nothing here to leak. */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

let state = null;
let method = "cash";
const date = new Date().toLocaleDateString("en-CA");

function myName() {
  try { return localStorage.getItem("lunchName") || ""; } catch { return ""; }
}
function rememberName(name) {
  try { localStorage.setItem("lunchName", name); } catch { /* private mode */ }
}
function myVenmo() {
  try { return localStorage.getItem("lunchVenmo") || ""; } catch { return ""; }
}

let toastTimer;
function toast(message, isError) {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.className = "toast"), 2800);
}

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
  state = await api(`/api/public/day?date=${date}`);
  render();
}

/* Everyone has this page open at once, so it has to keep up with itself.
   Without this you only ever see the orders that existed when you opened it. */
const REFRESH_MS = 8000;
let refreshTimer;

async function refresh() {
  try {
    state = await api(`/api/public/day?date=${date}`);
    render();
  } catch {
    /* A failed poll is not worth a toast -- the next one in 8s probably works,
       and the person is usually mid-typing when the wifi hiccups. */
  }
}

function pollWhenVisible() {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, REFRESH_MS);
}

// Phones background the page constantly; catch up the moment it comes back
// rather than making someone wait out the interval staring at stale orders.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refresh();
});

function render() {
  $("pubPlace").textContent = state.place || "Lunch";
  $("pubDate").textContent = new Date(date + "T12:00").toLocaleDateString(undefined,
    { weekday: "long", month: "long", day: "numeric" });

  // Rebuilding the list under an open dropdown closes it, so leave it alone
  // while someone is actually typing into the box.
  if (document.activeElement !== $("pItem")) {
    $("menuList").replaceChildren(...state.suggestions.map((desc) => {
      const option = el("option");
      option.value = desc;
      return option;
    }));
  }

  $("lockPill").classList.toggle("hidden", !state.locked);
  $("orderForm").classList.toggle("hidden", state.locked);
  $("closedNote").textContent = state.locked
    ? "Orders are closed for today — talk to whoever is picking up if you need a change."
    : "";

  const count = state.orders.reduce((n, o) => n + o.items.length, 0);
  $("allCount").textContent = count ? String(count) : "";

  renderMine();
  renderEveryone();
}

function renderMine() {
  const name = myName().trim().toLowerCase();
  const me = state.orders.find((o) => o.name.trim().toLowerCase() === name);
  const box = $("mine");

  if (!me || !me.items.length) {
    box.replaceChildren();
    return;
  }
  const nodes = [el("h3", "groupHead", "Your order")];
  me.items.forEach((desc, index) => {
    const row = el("div", "row " + (me.method === "venmo" ? "venmo" : ""));
    row.append(el("div", "what", desc));
    row.append(el("div", "chip" + (me.method === "venmo" ? " v" : ""),
                  me.method === "venmo" ? "Venmo" : "Cash"));
    if (!state.locked) {
      const drop = el("button", "x", "×");
      drop.title = "Remove this";
      drop.onclick = async () => {
        try {
          state = await api("/api/public/remove", { name: me.name, index });
          render();
          toast("Removed");
        } catch (err) { toast(err.message, true); }
      };
      row.append(drop);
    }
    nodes.push(row);
  });
  box.replaceChildren(...nodes);
}

function renderEveryone() {
  const box = $("everyone");
  const rows = [];
  for (const order of state.orders) {
    for (const desc of order.items) {
      const row = el("div", "row " + (order.method === "venmo" ? "venmo" : ""));
      row.append(el("div", "who", order.name), el("div", "what", desc));
      rows.push(row);
    }
  }
  box.replaceChildren(...(rows.length
    ? rows
    : [el("div", "empty", "Nobody has ordered yet — be first.")]));
}

// --- wiring ---------------------------------------------------------------

$("pMethod").onclick = (event) => {
  const button = event.target.closest(".tog");
  if (!button) return;
  method = button.dataset.m;
  for (const b of $("pMethod").children) {
    b.className = "tog" + (b === button ? " on " + method : "");
  }
  $("venmoField").classList.toggle("hidden", method !== "venmo");
};

$("orderForm").onsubmit = async (event) => {
  event.preventDefault();
  const name = $("pName").value.trim();
  const item = $("pItem").value.trim();
  if (!name || !item) return;
  try {
    state = await api("/api/public/order",
                      { name, item, method, venmo_user: $("pVenmo").value });
    rememberName(name);
    try { localStorage.setItem("lunchVenmo", $("pVenmo").value.trim()); } catch { /* ignore */ }
    $("pItem").value = "";
    render();
    toast(`Added ${item}`);
  } catch (err) {
    toast(err.message, true);
  }
};

$("pubTabs").onclick = (event) => {
  const button = event.target.closest(".step");
  if (!button) return;
  for (const b of $("pubTabs").children) b.classList.toggle("active", b === button);
  for (const panel of document.querySelectorAll(".pubMain .panel")) {
    panel.classList.toggle("hidden", panel.dataset.tab !== button.dataset.tab);
  }
};

$("pName").value = myName();
$("pVenmo").value = myVenmo();
load().then(() => {
  if (!myName()) $("pName").focus(); else $("pItem").focus();
  pollWhenVisible();
}).catch((err) => toast(err.message, true));
