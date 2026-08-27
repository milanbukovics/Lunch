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
// The date this browser last ordered under the remembered name. Lets someone
// add a second item without being asked "is that you?" every time, while a
// genuinely different person on a different device still gets asked.
function orderedToday(name) {
  try {
    return localStorage.getItem("lunchOrderedOn") === date
        && myName().trim().toLowerCase() === name.trim().toLowerCase();
  } catch { return false; }
}
function rememberOrdered() {
  try { localStorage.setItem("lunchOrderedOn", date); } catch { /* private mode */ }
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
  if (!response.ok) {
    const failure = new Error(data.error || "Something went wrong");
    failure.data = data;          // callers may need the detail, not just the text
    throw failure;
  }
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
  // Blank, not "Lunch" -- the brand beside it already says that, and showing
  // both reads as "Lunch Lunch" on every day before a place is chosen.
  $("pubPlace").textContent = state.place || "";
  $("pubDate").textContent = new Date(date + "T12:00").toLocaleDateString(undefined,
    { weekday: "long", month: "long", day: "numeric" });

  // Who to ask, shown all day rather than only once orders close -- by the time
  // you need to ask, it is too late to go looking for the name.
  const who = (state.organiser || "").trim();
  $("pubOrganiser").textContent = who ? `${who} is picking up` : "";
  $("pubOrganiser").classList.toggle("hidden", !who);

  // Named so people know who will be asking them for money. Built from el(),
  // which sets textContent -- the organiser types this name at login and it
  // renders on a page every coworker loads, so it must never become markup.
  $("venmoNote").replaceChildren(
    el("span", null, `If you've paid ${who || "the organiser"} on Venmo before, `
                     + "you can leave this blank."),
    el("br"),
    el("span", null, who
      ? `Nothing to pay now — ${who} will request the amount once the receipt is in.`
      : "Nothing to pay now — you'll get a request once the receipt is in."));

  $("lockPill").classList.toggle("hidden", !state.locked);
  $("orderForm").classList.toggle("hidden", state.locked);
  $("closedNote").textContent = !state.locked ? ""
    : who ? `Orders are closed — talk to ${who} if you need a change.`
          : "Orders are closed for today — talk to whoever is picking up if you need a change.";

  const count = state.orders.reduce((n, o) => n + o.items.length, 0);
  $("allCount").textContent = count ? String(count) : "";

  renderMenu();
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

// --- the menu -------------------------------------------------------------

function renderMenu() {
  const files = state.menu || [];
  $("menuBox").classList.toggle("hidden", !files.length);
  if (!files.length) return;

  $("menuStrip").replaceChildren(...files.map((file) => {
    if (file.kind === "pdf") {
      // Handed to the phone's own PDF viewer, where zoom and paging already
      // work properly -- far better than anything reimplemented here.
      const link = el("a", "menuPdf", file.filename);
      link.href = `/menu/${file.id}`;
      link.target = "_blank";
      link.rel = "noopener";
      return link;
    }
    const thumb = el("button", "menuThumb");
    thumb.type = "button";
    const img = el("img");
    img.src = `/menu/${file.id}`;
    img.alt = file.filename;
    img.loading = "lazy";
    thumb.append(img);
    thumb.onclick = () => openLightbox(file);
    return thumb;
  }));
}

/* Menu photos are the reason zoom exists here: the print is small and people
   read them on a phone. Pointer events cover mouse, touch and pen alike. */
let zoom = 1, panX = 0, panY = 0;
const pointers = new Map();
let pinchStart = 0, zoomStart = 1, lastTap = 0;

function applyZoom() {
  $("lbImg").style.transform =
    `translate(${panX}px, ${panY}px) scale(${zoom})`;
  $("lbPct").textContent = Math.round(zoom * 100) + "%";
  $("lbStage").classList.toggle("grabbable", zoom > 1);
}

function setZoom(next, originX, originY) {
  const clamped = Math.min(8, Math.max(1, next));
  if (clamped === 1) {
    panX = panY = 0;                       // snap back rather than drift
  } else if (originX != null) {
    // Keep the point under the cursor/fingers put as the scale changes.
    const factor = clamped / zoom;
    const rect = $("lbStage").getBoundingClientRect();
    const cx = originX - rect.left - rect.width / 2;
    const cy = originY - rect.top - rect.height / 2;
    panX = cx - (cx - panX) * factor;
    panY = cy - (cy - panY) * factor;
  }
  zoom = clamped;
  applyZoom();
}

function openLightbox(file) {
  $("lbImg").src = `/menu/${file.id}`;
  $("lbImg").alt = file.filename;
  zoom = 1; panX = panY = 0;
  applyZoom();
  $("lightbox").classList.remove("hidden");
  document.body.classList.add("noScroll");
}

function closeLightbox() {
  $("lightbox").classList.add("hidden");
  document.body.classList.remove("noScroll");
  $("lbImg").src = "";                     // stop it holding the image in memory
  pointers.clear();
}

function wireLightbox() {
  const stage = $("lbStage");

  $("lbIn").onclick = () => setZoom(zoom * 1.4);
  $("lbOut").onclick = () => setZoom(zoom / 1.4);
  $("lbReset").onclick = () => setZoom(1);
  $("lbClose").onclick = closeLightbox;
  // A tap on the backdrop closes; a tap on the image itself must not.
  $("lightbox").onclick = (e) => { if (e.target === $("lightbox")) closeLightbox(); };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("lightbox").classList.contains("hidden")) {
      closeLightbox();
    }
  });

  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    setZoom(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY);
  }, { passive: false });

  stage.addEventListener("pointerdown", (e) => {
    stage.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      pinchStart = spread();
      zoomStart = zoom;
    } else {
      const now = Date.now();
      if (now - lastTap < 300) setZoom(zoom > 1 ? 1 : 2.5, e.clientX, e.clientY);
      lastTap = now;
    }
  });

  stage.addEventListener("pointermove", (e) => {
    const previous = pointers.get(e.pointerId);
    if (!previous) return;
    const next = { x: e.clientX, y: e.clientY };

    if (pointers.size === 2) {
      pointers.set(e.pointerId, next);
      const [a, b] = [...pointers.values()];
      if (pinchStart > 0) {
        setZoom(zoomStart * (spread() / pinchStart),
                (a.x + b.x) / 2, (a.y + b.y) / 2);
      }
      return;
    }
    if (zoom > 1) {                        // panning only makes sense zoomed in
      panX += next.x - previous.x;
      panY += next.y - previous.y;
      applyZoom();
    }
    pointers.set(e.pointerId, next);
  });

  const release = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinchStart = 0;
  };
  stage.addEventListener("pointerup", release);
  stage.addEventListener("pointercancel", release);
}

function spread() {
  const [a, b] = [...pointers.values()];
  return Math.hypot(a.x - b.x, a.y - b.y);
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

function hideSamePrompt() {
  $("samePrompt").classList.add("hidden");
}

/* Someone typed a name that already has an order. It is usually them adding a
   second item, but it may be a second person with the same first name -- and
   merging those two would put both on one rounded total, billing them as one
   person. Only they can tell us which, so ask. Everything here is built with
   el(), which sets textContent: the name and items come from coworkers. */
function askIfSamePerson(info) {
  const box = $("samePrompt");
  const items = info.items.join(", ");

  const yes = el("button", "primary", "Yes — add this to my order");
  yes.type = "button";
  yes.onclick = () => submitOrder("add");

  const no = el("button", "ghost", `I'm a different ${info.name}`);
  no.type = "button";
  no.onclick = () => {
    hideSamePrompt();
    const hint = $("nameHint");
    hint.textContent =
      `Add a last initial so you two don't get mixed up — "${info.name} B".`;
    hint.classList.remove("hidden");
    $("pName").focus();
    $("pName").select();
  };

  const buttons = el("div", "sameBtns");
  buttons.append(yes, no);
  box.replaceChildren(
    el("p", null, `${info.name} already ordered ${items}. Is that you?`),
    buttons);
  box.classList.remove("hidden");
}

async function submitOrder(confirm) {
  const name = $("pName").value.trim();
  const item = $("pItem").value.trim();
  if (!name || !item) return;

  const body = { name, item, method, venmo_user: $("pVenmo").value };
  // Same name, same device, already ordered today -- that is the same person
  // adding a second item, so don't make them confirm it every time.
  if (confirm || orderedToday(name)) body.confirm = "add";

  try {
    state = await api("/api/public/order", body);
    rememberName(name);
    rememberOrdered();
    try { localStorage.setItem("lunchVenmo", $("pVenmo").value.trim()); } catch { /* ignore */ }
    $("pItem").value = "";
    hideSamePrompt();
    $("nameHint").classList.add("hidden");
    render();
    toast(`Added ${item}`);
  } catch (err) {
    if (err.data && err.data.error === "name_taken") askIfSamePerson(err.data);
    else toast(err.message, true);
  }
}

$("orderForm").onsubmit = (event) => {
  event.preventDefault();
  submitOrder();
};

$("pubTabs").onclick = (event) => {
  const button = event.target.closest(".step");
  if (!button) return;
  for (const b of $("pubTabs").children) b.classList.toggle("active", b === button);
  for (const panel of document.querySelectorAll(".pubMain .panel")) {
    panel.classList.toggle("hidden", panel.dataset.tab !== button.dataset.tab);
  }
};

wireLightbox();
$("pName").value = myName();
$("pVenmo").value = myVenmo();
load().then(() => {
  if (!myName()) $("pName").focus(); else $("pItem").focus();
  pollWhenVisible();
}).catch((err) => toast(err.message, true));
