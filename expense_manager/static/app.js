"use strict";

// ------------------------------------------------------------------ utilities
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (v, precise = false) => (precise ? inr2 : inr0).format(v || 0);
const short = (v) => (Math.abs(v) >= 1e5 ? "₹" + (v / 1e5).toFixed(1) + "L" : Math.abs(v) >= 1e3 ? "₹" + (v / 1e3).toFixed(1) + "k" : "₹" + Math.round(v));

const qs = new URLSearchParams(location.search);
if (qs.get("token")) { localStorage.setItem("em_token", qs.get("token")); history.replaceState(null, "", location.pathname); }

// "" at http://mac:8642/, "/expenses" behind `tailscale serve --set-path /expenses`.
const BASE = location.pathname.replace(/\/$/, "");

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const tok = localStorage.getItem("em_token");
  if (tok) headers["X-Token"] = tok;
  const res = await fetch(BASE + path, { ...opts, headers, body: opts.body && typeof opts.body !== "string" && !(opts.body instanceof Blob) ? JSON.stringify(opts.body) : opts.body });
  if (res.status === 401) {
    const t = prompt("Access token (see Settings on the Mac, or ~/.expense-manager/token):");
    if (t) { localStorage.setItem("em_token", t.trim()); return api(path, opts); }
    throw new Error("unauthorized");
  }
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || res.statusText); }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function modal(html) {
  $("#modal-box").innerHTML = html; $("#modal").classList.remove("hidden");
  return $("#modal-box");
}
const closeModal = () => $("#modal").classList.add("hidden");
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

const COLORS = {
  "Food & Dining": "#ff8a65", Groceries: "#9ccc65", Shopping: "#ba68c8", Travel: "#4fc3f7", Fuel: "#ffb74d",
  "Bills & Utilities": "#90a4ae", Entertainment: "#f06292", Health: "#4db6ac", "Rent & Housing": "#a1887f",
  Education: "#7986cb", "Personal Care": "#f48fb1", Transfers: "#64b5f6", Cash: "#aed581", "Loans & EMI": "#ce93d8", "Fees & Charges": "#e57373",
  Investments: "#81c784", Insurance: "#4dd0e1", "Gifts & Donations": "#ffd54f", Other: "#757d8a", Refund: "#80cbc4",
  Income: "#3ecf8e", "Self Transfer": "#5c6370", "Credit Card Payment": "#5c6370",
};
const color = (c) => COLORS[c] || "#8b93a1";
const INSTR = { UPI: "UPI", CREDIT_CARD: "Credit card", DEBIT_CARD: "Debit card", CARD: "Card", NETBANKING: "Net banking", ATM: "ATM", OTHER: "Other" };

Chart.defaults.color = "#8b93a1";
Chart.defaults.borderColor = "#2a303a";
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
const charts = {};
const isPhone = () => window.matchMedia("(max-width: 700px)").matches;
function chart(id, cfg) { charts[id]?.destroy(); charts[id] = new Chart($("#" + id), cfg); }

// ------------------------------------------------------------------ state
const state = { month: new Date().toISOString().slice(0, 7), tab: "dashboard", categories: [], nonSpend: [], rawStatus: "unparsed" };
$("#month").value = state.month;

function shiftMonth(delta) {
  const [y, m] = state.month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  state.month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  $("#month").value = state.month; state.dayFilter = null; refresh();
}
$("#prev-month").onclick = () => shiftMonth(-1);
$("#next-month").onclick = () => shiftMonth(1);
$("#month").onchange = (e) => { if (e.target.value) { state.month = e.target.value; state.dayFilter = null; refresh(); } };

$$("nav button").forEach((b) => (b.onclick = () => { state.dayFilter = null; showTab(b.dataset.tab); }));
window.addEventListener("hashchange", () => {
  const t = location.hash.slice(1);
  if (TABS.includes(t) && t !== state.tab) showTab(t);
});
const TABS = ["dashboard", "transactions", "review", "settings"];

function showTab(tab) {
  state.tab = tab;
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + tab));
  if (location.hash.slice(1) !== tab) location.hash = tab;
  window.scrollTo(0, 0);
  refresh();
}

function refresh() {
  ({ dashboard: loadDashboard, transactions: loadTransactions, review: loadReview, settings: loadSettings })[state.tab]()
    .catch((e) => toast("Error: " + e.message));
  updateReviewBadge();
}

async function updateReviewBadge() {
  try {
    const r = await api("/api/raw?status=unparsed&limit=1");
    const n = r.counts.unparsed || 0, b = $("#review-badge");
    b.textContent = n; b.classList.toggle("hidden", !n);
  } catch { /* ignore */ }
}

// ------------------------------------------------------------------ dashboard
async function loadDashboard() {
  const [s, trend, ins] = await Promise.all([
    api(`/api/summary?month=${state.month}`), api("/api/trend?months=12"), api(`/api/insights?month=${state.month}`),
  ]);

  $("#k-spend").textContent = money(s.total_spend);
  const ch = s.change_pct;
  $("#k-spend-sub").innerHTML = ch == null ? `${s.txn_count} transactions`
    : `<span class="${ch > 0 ? "neg" : "pos"}">${ch > 0 ? "▲" : "▼"} ${Math.abs(ch)}%</span> vs ${s.is_current ? "same period " : ""}last month · ${s.txn_count} txns`;
  $("#k-avg").textContent = money(s.avg_per_day);
  $("#k-avg-sub").textContent = s.is_current ? `projected ${money(s.projected)} this month` : `last month ${money(s.prev_month_total)}`;
  $("#k-income").textContent = money(s.income);
  $("#k-income-sub").textContent = s.income ? `saved ${money(s.income - s.total_spend)} (${Math.round((1 - s.total_spend / s.income) * 100)}%)` : "";
  $("#k-invested").textContent = money(s.invested);
  $("#k-cc").textContent = money(s.cc_bill_paid);

  chart("c-daily", {
    data: {
      labels: s.daily.map((d) => d.date.slice(8)),
      datasets: [
        { type: "bar", label: "Spent", data: s.daily.map((d) => d.amount), backgroundColor: "#6c8cff", borderRadius: 4, yAxisID: "y" },
        { type: "line", label: "Cumulative", data: s.daily.map((d) => d.cumulative), borderColor: "#f5a524", pointRadius: 0, tension: .25, yAxisID: "y1" },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.raw)}` } } },
      scales: { y: { ticks: { callback: short } }, y1: { position: "right", grid: { display: false }, ticks: { callback: short } } },
      onClick: (_, els) => { if (els.length) openDay(s.daily[els[0].index].date); },
    },
  });

  const cats = s.by_category.filter((c) => c.amount > 0);
  chart("c-cat", {
    type: "doughnut",
    data: { labels: cats.map((c) => c.category), datasets: [{ data: cats.map((c) => c.amount), backgroundColor: cats.map((c) => color(c.category)), borderWidth: 0 }] },
    options: {
      maintainAspectRatio: false, cutout: "62%",
      plugins: { legend: { position: isPhone() ? "bottom" : "right", labels: { boxWidth: 10, font: { size: 11 } } },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${money(c.raw)} (${Math.round(c.raw / s.total_spend * 100)}%)` } } },
      onClick: (_, els) => { if (els.length) { $("#f-category").value = cats[els[0].index].category; showTab("transactions"); } },
    },
  });

  const trendCats = [...new Set(trend.flatMap((t) => Object.keys(t.by_category)))]
    .sort((a, b) => trend.reduce((s, t) => s + (t.by_category[b] || 0), 0) - trend.reduce((s, t) => s + (t.by_category[a] || 0), 0));
  chart("c-trend", {
    data: {
      labels: trend.map((t) => new Date(t.month + "-01").toLocaleString("en-IN", { month: "short", year: "2-digit" })),
      datasets: [
        ...trendCats.map((c) => ({ type: "bar", label: c, data: trend.map((t) => t.by_category[c] || 0), backgroundColor: color(c), stack: "s" })),
        { type: "line", label: "Income", data: trend.map((t) => t.income), borderColor: "#3ecf8e", pointRadius: 3, tension: .25 },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { filter: (i) => i.raw > 0, callbacks: {
        label: (c) => `${c.dataset.label}: ${money(c.raw)}`,
        footer: (items) => "Total spend: " + money(trend[items[0].dataIndex].spend) } } },
      scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: short } } },
      onClick: (_, els) => { if (els.length) { state.month = trend[els[0].index].month; $("#month").value = state.month; refresh(); } },
    },
  });

  const withBudget = s.by_category.filter((c) => c.budget);
  $("#budgets").innerHTML = withBudget.length ? withBudget.map((c) => {
    const pct = Math.min(100, (c.amount / c.budget) * 100);
    const expected = s.is_current ? (s.days_elapsed / s.days_in_month) * 100 : 100;
    const col = pct >= 100 ? "var(--neg)" : pct > expected ? "var(--warn)" : "var(--pos)";
    return `<div class="b"><div class="top"><span><span class="dot" style="background:${color(c.category)}"></span>${esc(c.category)}</span>
      <span>${money(c.amount)} / ${money(c.budget)}</span></div><div class="bar"><div style="width:${pct}%;background:${col}"></div></div></div>`;
  }).join("") : `<div class="muted">No budgets yet. Set them in <a href="#settings" onclick="showTab('settings')">Settings</a>.</div>`;

  $("#t-merchants").innerHTML = s.top_merchants.map((m) =>
    `<tr><td><span class="dot" style="background:${color(m.category)}"></span>${esc(m.merchant)} <span class="muted">×${m.count}</span></td><td class="num">${money(m.amount)}</td></tr>`
  ).join("") || `<tr><td class="muted">No data</td></tr>`;

  $("#recurring-total").textContent = ins.recurring.length ? `≈ ${money(ins.recurring_total)}/mo` : "";
  $("#t-recurring").innerHTML = ins.recurring.map((r) =>
    `<tr><td>${esc(r.merchant)} <span class="muted">${esc(r.category)} · ${r.months_seen} mo</span></td><td class="num">${money(r.monthly)}</td></tr>`
  ).join("") || `<tr><td class="muted">Needs ≥3 months of history</td></tr>`;

  $("#t-biggest").innerHTML = ins.biggest.map((b) =>
    `<tr><td>${esc(b.merchant || "—")} <span class="muted">${b.txn_time.slice(5, 10).split("-").reverse().join("/")} · ${esc(b.category)}</span></td><td class="num">${money(b.amount)}</td></tr>`
  ).join("") || `<tr><td class="muted">No data</td></tr>`;

  $("#t-accounts").innerHTML = s.by_account.map((a) =>
    `<tr><td>${esc(a.account)} <span class="muted">×${a.count}</span></td><td class="num">${money(a.amount)}</td></tr>`
  ).join("") || `<tr><td class="muted">No data</td></tr>`;

  const inst = s.by_instrument.filter((i) => i.amount > 0);
  chart("c-instr", {
    type: "doughnut",
    data: { labels: inst.map((i) => INSTR[i.instrument] || i.instrument), datasets: [{ data: inst.map((i) => i.amount),
      backgroundColor: ["#6c8cff", "#f5a524", "#3ecf8e", "#ba68c8", "#4fc3f7", "#ff6b6b", "#8b93a1"], borderWidth: 0 }] },
    options: { maintainAspectRatio: false, cutout: "60%", plugins: { legend: { position: isPhone() ? "bottom" : "right", labels: { boxWidth: 10 } },
      tooltip: { callbacks: { label: (c) => `${c.label}: ${money(c.raw)}` } } } },
  });

  chart("c-weekday", {
    type: "bar",
    data: { labels: ins.weekday.map((w) => w.day), datasets: [{ data: ins.weekday.map((w) => w.amount), backgroundColor: "#4fc3f7", borderRadius: 4 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => money(c.raw) } } },
      scales: { y: { ticks: { callback: short } } } },
  });
}

function openDay(d) {
  state.dayFilter = d;
  showTab("transactions");
}

// ------------------------------------------------------------------ transactions
function catOptions(sel, { includeAll = false } = {}) {
  return (includeAll ? `<option value="">All categories</option>` : "") +
    state.categories.map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("");
}

let searchTimer;
$("#f-q").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadTransactions, 250); };
["#f-category", "#f-direction", "#f-instrument"].forEach((s) => ($(s).onchange = loadTransactions));

function txnQuery() {
  const p = new URLSearchParams();
  if (state.dayFilter) { p.set("date_from", state.dayFilter); p.set("date_to", state.dayFilter); }
  else p.set("month", state.month);
  for (const [k, s] of [["q", "#f-q"], ["category", "#f-category"], ["direction", "#f-direction"], ["instrument", "#f-instrument"]])
    if ($(s).value) p.set(k, $(s).value);
  return p;
}

async function loadTransactions() {
  const p = txnQuery();
  $("#export").href = BASE + "/api/export.csv?" + p + (localStorage.getItem("em_token") ? "&token=" + encodeURIComponent(localStorage.getItem("em_token")) : "");
  const r = await api("/api/transactions?" + p);
  const nonSpend = new Set(r.non_spend_categories);
  $("#txn-summary").innerHTML = `${r.total} txns · <span class="neg">−${money(r.sum_debit)}</span> · <span class="pos">+${money(r.sum_credit)}</span>` +
    (state.dayFilter ? ` · <a href="#" id="clear-day">showing ${state.dayFilter} ✕</a>` : "");
  $("#clear-day")?.addEventListener("click", (e) => { e.preventDefault(); state.dayFilter = null; loadTransactions(); });

  if (!r.items.length) { $("#txn-list").innerHTML = `<div class="empty">No transactions for this filter.</div>`; return; }
  const days = {};
  for (const t of r.items) (days[t.txn_time.slice(0, 10)] ||= []).push(t);
  $("#txn-list").innerHTML = Object.entries(days).map(([d, items]) => {
    const spent = items.reduce((s, t) => s + (t.excluded || nonSpend.has(t.category) ? 0 : t.direction === "debit" ? t.amount : -t.amount), 0);
    const label = new Date(d + "T00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    return `<div class="day"><div class="day-head"><span>${label}</span><span>${spent >= 0 ? "Spent " + money(spent) : "Net refund " + money(-spent)}</span></div>
      ${items.map(txnRow).join("")}</div>`;
  }).join("");

  $$(".txn select.cat").forEach((s) => (s.onchange = () => changeCategory(+s.dataset.id, s.value, s.dataset.merchant)));
  $$(".txn [data-act]").forEach((b) => (b.onclick = () => txnAction(b.dataset.act, +b.dataset.id, b.dataset.ex === "1")));
  $$(".txn .m").forEach((m) => (m.onclick = () => openTxn(+m.dataset.id)));
}

function txnRow(t) {
  const srcIcons = (t.sources || t.source).split(",").map((s) => ({ sms: "💬", email: "📧", manual: "✍️" }[s] || s)).join("");
  const acct = [t.bank, t.account_last4 ? "••" + t.account_last4 : ""].filter(Boolean).join(" ");
  return `<div class="txn ${t.excluded ? "excluded" : ""}">
    <div class="time">${t.txn_time.slice(11, 16)}</div>
    <div class="m" data-id="${t.id}" style="cursor:pointer">
      <div class="name">${esc(t.merchant || t.payee_vpa || "Unknown")}${t.parsed_by === "llm" ? '<span class="chip">AI</span>' : ""}${t.notes ? '<span class="chip">📝</span>' : ""}</div>
      <div class="meta"><span class="cat-m"><span class="dot" style="background:${color(t.category)}"></span>${esc(t.category)}${t.payee_vpa || t.ref_no ? " · " : ""}</span>${[t.payee_vpa && esc(t.payee_vpa), t.ref_no && "ref " + esc(t.ref_no)].filter(Boolean).join(" · ")}</div>
    </div>
    <select class="cat" data-id="${t.id}" data-merchant="${esc(t.merchant || t.payee_vpa || "")}">${catOptions(t.category)}</select>
    <div class="acct">${esc(INSTR[t.instrument] || t.instrument || "")} ${esc(acct)}</div>
    <div class="amt ${t.direction === "credit" ? "pos" : ""}">${t.direction === "credit" ? "+" : "−"}${money(t.amount, true)}</div>
    <div class="actions"><span title="${esc(t.sources || t.source)}">${srcIcons}</span>
      <button class="icon-btn" data-act="exclude" data-id="${t.id}" data-ex="${t.excluded}" title="${t.excluded ? "Include in totals" : "Exclude from totals"}">${t.excluded ? "↺" : "⊘"}</button>
      <button class="icon-btn" data-act="delete" data-id="${t.id}" title="Delete">🗑</button></div>
  </div>`;
}

function changeCategory(id, category, merchant) {
  const box = modal(`<h3>Change category to “${esc(category)}”</h3>
    ${merchant ? `<p>Apply to every transaction from <b>${esc(merchant)}</b>, including future ones?</p>` : ""}
    <div class="row"><button class="btn" id="cc-one">Just this one</button>
    ${merchant ? `<button class="btn ghost" id="cc-all">All from ${esc(merchant)}</button>` : ""}</div>`);
  const go = async (all) => {
    closeModal();
    const r = await api(`/api/transactions/${id}`, { method: "PATCH", body: { category, apply_to_similar: all } });
    toast(all ? `Saved rule · ${r.updated_similar} transactions updated` : "Category updated");
    refresh();
  };
  $("#cc-one", box).onclick = () => go(false);
  $("#cc-all", box)?.addEventListener("click", () => go(true));
}

async function txnAction(act, id, excluded) {
  if (act === "delete") {
    if (!confirm("Delete this transaction?")) return;
    await api(`/api/transactions/${id}`, { method: "DELETE" });
  } else if (act === "exclude") {
    await api(`/api/transactions/${id}`, { method: "PATCH", body: { excluded: !excluded } });
  }
  refresh();
}

async function openTxn(id) {
  const t = await api(`/api/transactions/${id}`);
  const box = modal(`<h3>${t.direction === "credit" ? "Credit" : "Debit"} · ${money(t.amount, true)}</h3>
    <div class="form">
      <label>Merchant</label><input id="e-merchant" value="${esc(t.merchant || "")}" />
      <label>Category</label><select id="e-cat">${catOptions(t.category)}</select>
      <label>Amount</label><input id="e-amount" type="number" step="0.01" value="${t.amount}" />
      <label>Date & time</label><input id="e-time" type="datetime-local" value="${t.txn_time.replace(" ", "T").slice(0, 16)}" />
      <label>Notes</label><input id="e-notes" value="${esc(t.notes || "")}" />
      <label>Details</label><div class="muted">${esc(INSTR[t.instrument] || "")} · ${esc(t.bank || "")} ${t.account_last4 ? "••" + esc(t.account_last4) : ""}
        ${t.payee_vpa ? "<br>UPI: " + esc(t.payee_vpa) : ""}${t.ref_no ? "<br>Ref: " + esc(t.ref_no) : ""}
        <br>Category via: ${esc(t.category_source || "")} · parsed by ${esc(t.parsed_by || "")}</div>
    </div>
    <div class="row"><label><input type="checkbox" id="e-similar" /> Apply category to all from this merchant</label></div>
    <div class="row"><button class="btn" id="e-save">Save</button><button class="btn ghost" id="e-cancel">Cancel</button>
      <span class="spacer"></span>
      <button class="btn ghost" id="e-exclude">${t.excluded ? "Include in totals" : "Exclude from totals"}</button>
      <button class="btn danger" id="e-delete">Delete</button></div>
    ${t.raw_messages.map((r) => `<div class="raw"><div class="head"><span>${r.source === "sms" ? "💬" : "📧"} ${esc(r.sender || "")}</span><span>${esc(r.received_at)}</span></div>
      ${r.subject ? `<div><b>${esc(r.subject)}</b></div>` : ""}<pre>${esc((r.text || r.body).slice(0, 1500))}</pre></div>`).join("")}`);
  $("#e-cancel", box).onclick = closeModal;
  $("#e-exclude", box).onclick = () => { closeModal(); txnAction("exclude", id, !!t.excluded); };
  $("#e-delete", box).onclick = () => { closeModal(); txnAction("delete", id); };
  $("#e-save", box).onclick = async () => {
    const body = { merchant: $("#e-merchant").value, notes: $("#e-notes").value, amount: parseFloat($("#e-amount").value),
      txn_time: $("#e-time").value, apply_to_similar: $("#e-similar").checked };
    if ($("#e-cat").value !== t.category || body.apply_to_similar) body.category = $("#e-cat").value;
    await api(`/api/transactions/${id}`, { method: "PATCH", body });
    closeModal(); toast("Saved"); refresh();
  };
}

$("#add-txn").onclick = () => {
  const now = new Date(); now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  const box = modal(`<h3>Add transaction</h3><div class="form">
    <label>Amount</label><input id="n-amount" type="number" step="0.01" autofocus />
    <label>Type</label><select id="n-dir"><option value="debit">Spent (debit)</option><option value="credit">Received (credit)</option></select>
    <label>Merchant</label><input id="n-merchant" placeholder="e.g. Local vegetable vendor" />
    <label>Category</label><select id="n-cat"><option value="">Auto</option>${catOptions()}</select>
    <label>Method</label><select id="n-instr"><option value="OTHER">Cash / other</option><option>UPI</option><option value="CREDIT_CARD">Credit card</option><option value="DEBIT_CARD">Debit card</option><option>NETBANKING</option></select>
    <label>Date & time</label><input id="n-time" type="datetime-local" value="${now.toISOString().slice(0, 16)}" />
    <label>Notes</label><input id="n-notes" />
  </div><div class="row"><button class="btn" id="n-save">Add</button><button class="btn ghost" id="n-cancel">Cancel</button></div>`);
  $("#n-cancel", box).onclick = closeModal;
  $("#n-save", box).onclick = async () => {
    const amount = parseFloat($("#n-amount").value);
    if (!(amount > 0)) return toast("Enter an amount");
    await api("/api/transactions", { method: "POST", body: { amount, direction: $("#n-dir").value, merchant: $("#n-merchant").value || null,
      category: $("#n-cat").value || null, instrument: $("#n-instr").value, txn_time: $("#n-time").value, notes: $("#n-notes").value || null } });
    closeModal(); toast("Added"); refresh();
  };
};

// ------------------------------------------------------------------ review
$$("#raw-status button").forEach((b) => (b.onclick = () => {
  state.rawStatus = b.dataset.s;
  $$("#raw-status button").forEach((x) => x.classList.toggle("active", x === b));
  loadReview();
}));

async function loadReview() {
  const r = await api(`/api/raw?status=${state.rawStatus}&limit=300`);
  $$("#raw-status button").forEach((b) => {
    const n = r.counts[b.dataset.s] || 0;
    b.textContent = b.textContent.replace(/ \(\d+\)$/, "") + ` (${n})`;
  });
  $("#raw-list").innerHTML = r.items.length ? r.items.map((m) => `<div class="raw">
    <div class="head"><span>${m.source === "sms" ? "💬" : m.source === "email" ? "📧" : "✍️"} ${esc(m.sender || "")} ${m.subject ? "· " + esc(m.subject) : ""}</span>
    <span>${esc(m.received_at)} ${m.note ? "· " + esc(m.note) : ""}</span></div>
    <pre>${esc((m.text || m.body).slice(0, 2000))}</pre>
    ${["unparsed", "ignored"].includes(m.status) ? `<div class="row">
      <button class="btn ghost" data-re="${m.id}">Retry parse${state.llm ? " (with AI)" : ""}</button>
      <button class="btn ghost" data-add="${m.id}">Add manually</button>
      ${m.status === "unparsed" ? `<button class="btn ghost" data-dis="${m.id}">Not a transaction</button>` : ""}</div>` : ""}
    ${m.transaction_id ? `<div class="row"><a href="#" data-open="${m.transaction_id}">View transaction #${m.transaction_id}</a></div>` : ""}
  </div>`).join("") : `<div class="empty">Nothing here 🎉</div>`;

  $$("[data-re]").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    const res = await api(`/api/raw/${b.dataset.re}/reprocess`, { method: "POST", body: { use_llm: true } });
    toast(res.status === "parsed" ? "Parsed ✓" : res.status === "duplicate" ? "Merged with an existing transaction" : `Still ${res.status}: ${res.reason || ""}`);
    refresh();
  }));
  $$("[data-dis]").forEach((b) => (b.onclick = async () => { await api(`/api/raw/${b.dataset.dis}/dismiss`, { method: "POST" }); refresh(); }));
  $$("[data-add]").forEach((b) => (b.onclick = async () => {
    const m = r.items.find((x) => x.id === +b.dataset.add);
    await api(`/api/raw/${m.id}/dismiss`, { method: "POST" });
    $("#add-txn").click();
    const amt = /(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)/i.exec(m.body);
    if (amt) $("#n-amount").value = amt[1].replace(/,/g, "");
    $("#n-time").value = m.received_at.replace(" ", "T").slice(0, 16);
  }));
  $$("[data-open]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openTxn(+a.dataset.open); }));
}

$("#paste-go").onclick = async () => {
  const text = $("#paste-text").value.trim();
  if (!text) return;
  const r = await api("/api/ingest/text", { method: "POST", body: { text, sender: $("#paste-sender").value, source: "manual" } });
  const t = r.transaction;
  $("#paste-result").innerHTML = r.status === "parsed"
    ? `<span class="ok">✓ ${t.direction} ${money(t.amount, true)} · ${esc(t.merchant || t.payee_vpa || "?")} · ${esc(t.category)}</span>`
    : r.status === "duplicate" ? `Matched existing transaction #${r.transaction_id}` : `<span class="bad">${esc(r.status)}${r.reason ? ": " + esc(r.reason) : ""}</span>`;
  if (r.status === "parsed") $("#paste-text").value = "";
  updateReviewBadge();
};

// ------------------------------------------------------------------ settings
function qrSvg(text) {
  if (typeof qrcode !== "function") return `<code>${esc(text)}</code>`;
  const q = qrcode(0, "M"); q.addData(text); q.make();
  return q.createSvgTag(4, 8);
}

async function loadSettings() {
  const [st, budgets, rules] = await Promise.all([api("/api/status"), api("/api/budgets"), api("/api/rules")]);
  state.llm = st.llm.enabled;
  const g = st.gmail;
  $("#s-gmail").innerHTML = g.configured
    ? `<p class="ok">Connected as ${esc(g.user)}</p><p class="muted">Polls every ${g.poll_minutes} min for mail from: ${g.senders.map(esc).join(", ")}<br>Last sync: ${esc(g.last_sync || "never")}</p>`
    : `<p class="bad">Not configured.</p><ol class="steps">
        <li>Turn on <b>2-Step Verification</b> on your Google account</li>
        <li>Create an <b>App password</b> at <a href="https://myaccount.google.com/apppasswords" target="_blank">myaccount.google.com/apppasswords</a></li>
        <li>Put <code>GMAIL_USER</code> and <code>GMAIL_APP_PASSWORD</code> in <code>.env</code> and restart</li></ol>`;
  $("#sync-now").disabled = $("#backfill").disabled = !g.configured;

  $("#s-sms").innerHTML = `<p class="muted">Last SMS received: ${esc(st.sms.last_received || "never")} · Forwarder last seen: ${esc(st.sms.last_heartbeat || st.sms.last_received || "never")}</p>
    <ol class="steps">
      <li>Install <b>SMS to URL Forwarder</b> (<a href="https://github.com/bogkonstantin/android_income_sms_gateway_webhook" target="_blank">F-Droid / Play Store</a>) or any forwarder that can POST JSON</li>
      <li>Add a forwarding rule. Sender: <code>*</code> (the server ignores non-bank senders). Webhook URL:</li>
      ${st.sms.webhook_urls.map((u, i) => `<div class="code" style="margin:4px 0">${esc(u)}</div>${i === 0 && /\.ts\.net|\/\/100\./.test(u)
        ? '<div class="muted">↑ Tailscale address: works from anywhere while Tailscale is on, on both phone and Mac. Recommended.</div>'
        : ""}`).join("")}
      <li>Set the JSON payload template to: <code>{"from":"%from%","text":"%text%","receivedStamp":%receivedStamp%}</code></li>
      <li>Phone and Mac must be on the same Wi-Fi (or use <b>Tailscale</b> for anywhere access)</li>
      <li>Exclude the forwarder app from battery optimisation</li>
    </ol>
    <h4 style="margin:14px 0 6px">Import old SMS</h4>
    <p class="muted">On the phone, use <b>SMS Backup &amp; Restore</b> → Back up → Messages only → save locally, then copy the
      <code>sms-*.xml</code> file to this Mac and upload it here. Only bank SMS are imported, and anything already captured is skipped.</p>
    <div class="row"><input type="file" id="sms-file" accept=".xml,text/xml" /><button class="btn" id="sms-import">Import</button></div>
    <div id="sms-import-result" class="muted"></div>
    ${st.token ? `<p class="muted">Token: <code>${esc(st.token)}</code></p>
      <p class="muted">Scan these with your phone camera or Google Lens so you don't have to type the token:</p>
      <div class="qrs">
        <div><div class="qr">${qrSvg(st.sms.webhook_urls[0])}</div><b>Forwarder URL</b><br><span class="muted">copy into the app's rule</span></div>
        <div><div class="qr">${qrSvg(st.dashboard_url)}</div><b>Phone dashboard</b><br><span class="muted">open and bookmark</span></div>
      </div>` : ""}`;

  $("#s-llm").innerHTML = st.llm.enabled
    ? `<p class="ok">Enabled · <code>${esc(st.llm.model)}</code> via <code>${esc(st.llm.base_url)}</code></p><p class="muted">Used only for messages the rules can’t parse and for merchants no rule matches.</p>`
    : `<p class="muted">Off. The deterministic parser and category rules handle almost everything. To enable, set <code>LLM_ENABLED=true</code> in <code>.env</code>.
       Use Ollama for fully local processing (<code>ollama pull llama3.1</code>) or any OpenAI-compatible endpoint.</p>`;

  const spendCats = state.categories.filter((c) => !state.nonSpend.includes(c) && c !== "Refund");
  $("#budget-editor").innerHTML = spendCats.map((c) =>
    `<label><span class="dot" style="background:${color(c)}"></span>${esc(c)}</label><input type="number" min="0" step="500" data-budget="${esc(c)}" value="${budgets[c] ?? ""}" placeholder="—" />`
  ).join("");

  $("#sms-import").onclick = async () => {
    const f = $("#sms-file").files[0];
    if (!f) return toast("Choose the sms-*.xml file first");
    $("#sms-import").disabled = true;
    $("#sms-import-result").textContent = `Importing ${(f.size / 1e6).toFixed(1)} MB… this can take a minute`;
    try {
      const r = await api("/api/import/sms-backup", { method: "POST", body: f, headers: { "Content-Type": "application/xml" } });
      $("#sms-import-result").innerHTML = r.ok
        ? `<span class="ok">✓ ${r.total_sms || 0} SMS scanned, ${r.bank_sms || 0} from banks: ${r.parsed || 0} new transactions, ${r.duplicate || 0} merged with email, ${r.exists || 0} already imported, ${r.ignored || 0} ignored, ${r.unparsed || 0} need review</span>`
        : `<span class="bad">${esc(r.error)}</span>`;
      updateReviewBadge();
    } catch (e) { $("#sms-import-result").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
    finally { $("#sms-import").disabled = false; }
  };

  $("#rule-category").innerHTML = catOptions();
  $("#t-rules").innerHTML = rules.map((r) =>
    `<tr><td><code>${esc(r.pattern)}</code></td><td><span class="dot" style="background:${color(r.category)}"></span>${esc(r.category)}</td>
     <td class="num"><button class="icon-btn" data-delrule="${r.id}">🗑</button></td></tr>`
  ).join("") || `<tr><td class="muted">No custom rules yet</td></tr>`;
  $$("[data-delrule]").forEach((b) => (b.onclick = async () => { await api(`/api/rules/${b.dataset.delrule}`, { method: "DELETE" }); loadSettings(); }));
}

async function doSync(days) {
  $("#sync-result").textContent = "Syncing… (a large backfill can take a minute)";
  $("#sync-now").disabled = $("#backfill").disabled = true;
  try {
    const r = await api("/api/sync/gmail", { method: "POST", body: days ? { days } : {} });
    $("#sync-result").innerHTML = r.ok
      ? `<span class="ok">✓ ${r.fetched} emails since ${r.since}: ${r.parsed} new, ${r.duplicate} merged, ${r.ignored} ignored, ${r.unparsed} need review, ${r.exists} already seen</span>`
      : `<span class="bad">${esc(r.error)}</span>`;
  } finally { loadSettings(); updateReviewBadge(); }
}
$("#sync-now").onclick = () => doSync();
$("#backfill").onclick = () => doSync(parseInt($("#backfill-days").value) || 90);
$("#recat").onclick = async () => {
  $("#recat-result").textContent = "Working…";
  const r = await api("/api/recategorize", { method: "POST" });
  $("#recat-result").textContent = `${r.updated} updated`;
};
$("#save-budgets").onclick = async () => {
  const body = {};
  $$("[data-budget]").forEach((i) => (body[i.dataset.budget] = i.value ? parseFloat(i.value) : null));
  await api("/api/budgets", { method: "PUT", body });
  $("#budget-result").textContent = "Saved ✓";
};
$("#rule-add").onclick = async () => {
  const pattern = $("#rule-pattern").value.trim();
  if (!pattern) return;
  const r = await api("/api/rules", { method: "POST", body: { pattern, category: $("#rule-category").value } });
  toast(`Rule added · ${r.updated} transactions updated`);
  $("#rule-pattern").value = ""; loadSettings();
};

// ------------------------------------------------------------------ boot
(async () => {
  const c = await api("/api/categories");
  state.categories = c.categories; state.nonSpend = c.non_spend;
  $("#f-category").innerHTML = catOptions(null, { includeAll: true });
  const st = await api("/api/status").catch(() => null);
  state.llm = st?.llm.enabled;
  showTab(["dashboard", "transactions", "review", "settings"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "dashboard");
})();
