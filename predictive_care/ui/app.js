/* Demo storefront for the predictive card-care POC.
   Every interaction is emitted to the backend as a behavioural signal; the
   backend decides when the proactive prompt should appear. */

const $ = (id) => document.getElementById(id);
const userId = () => $("user-id").value.trim() || "cust_1001";

let pageEnteredAt = Date.now();
let currentPage = "accounts";
let lastPrediction = null;
let popupOpen = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${detail}`);
  }
  return response.json();
}

async function sendSignal(type, value, metadata = {}) {
  const result = await api("/api/signals", {
    method: "POST",
    body: JSON.stringify({ user_id: userId(), type, value, metadata }),
  });
  applyPrediction(result.prediction);
  await refreshInspector();
  return result;
}

/* ----------------------------------------------------------------- pages */

function showPage(page) {
  const dwell = (Date.now() - pageEnteredAt) / 1000;
  const previous = currentPage;
  currentPage = page;
  pageEnteredAt = Date.now();

  document.querySelectorAll(".page").forEach((el) => el.classList.remove("active"));
  const target = $(`page-${page}`);
  if (target) target.classList.add("active");
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.page === page);
  });

  return { previous, dwell };
}

async function navigate(page) {
  const { dwell } = showPage(page);
  if (page === "card_management") await renderCard();
  await sendSignal("page_view", page, { dwell_seconds: Math.round(dwell) });
}

/* ------------------------------------------------------------------ card */

async function renderCard() {
  const card = await api(`/api/cards/${encodeURIComponent(userId())}`);
  $("card-network").textContent = card.network;
  $("card-last4").textContent = card.last4;
  const pill = $("card-status");
  pill.textContent = card.status;
  pill.className = `status-pill ${card.status}`;
  $("card-reason").textContent = card.blocked_reason || "";

  $("txn-body").innerHTML = card.transactions
    .slice()
    .reverse()
    .map(
      (t) => `<tr><td>${t.merchant}</td><td>$${t.amount.toFixed(2)}</td>
        <td class="${t.status}">${t.status}</td></tr>`
    )
    .join("");
}

/* ---------------------------------------------------------------- search */

const HELP_ARTICLES = [
  ["Why was my debit card blocked?", "We block cards temporarily when we spot unusual activity."],
  ["Replace a lost or stolen card", "Order a replacement card and cancel the old one."],
  ["Activate a new debit card", "Activate in the app or at any ATM."],
  ["Dispute a card transaction", "Open a dispute and get provisional credit."],
];

async function runSearch(query) {
  showPage("search");
  $("search-summary").textContent = `Results for “${query}”`;
  $("search-results").innerHTML = HELP_ARTICLES.map(
    ([title, body]) =>
      `<li><a href="#" data-page="card_management">${title}</a><div class="muted">${body}</div></li>`
  ).join("");
  $("search-results")
    .querySelectorAll("a")
    .forEach((link) =>
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate("card_management");
      })
    );
  await sendSignal("search", query);
}

/* ------------------------------------------------------------ prediction */

function applyPrediction(prediction, { allowPopup = true } = {}) {
  lastPrediction = prediction;
  $("confidence-value").textContent = prediction.confidence.toFixed(2);
  $("confidence-bar").style.width = `${Math.round(prediction.confidence * 100)}%`;
  $("prediction-state").textContent = prediction.should_intervene
    ? `Intervening — predicted issue: ${prediction.issue_type}`
    : prediction.suppressed_by
    ? `Held back (${prediction.suppressed_by}) — issue: ${prediction.issue_type}`
    : `Watching — issue: ${prediction.issue_type}`;

  $("reasons").innerHTML = (prediction.reasons || [])
    .map(
      (r) =>
        `<li class="${r.source}">${r.detail}<br /><span class="weight">${r.source} · +${r.weight.toFixed(
          2
        )}</span></li>`
    )
    .join("") || '<li class="muted">No friction detected yet</li>';

  if (allowPopup && prediction.should_intervene && !popupOpen) openPopup(prediction);
}

function openPopup(prediction) {
  popupOpen = true;
  $("popup-headline").textContent = prediction.headline;
  $("popup-message").textContent = prediction.message;
  $("popup-actions").innerHTML = prediction.actions
    .map(
      (a) =>
        `<button class="${a.primary ? "primary" : "secondary"}" data-action="${a.action}"
          title="${a.reason || ""}">${a.label}</button>`
    )
    .join("");
  $("popup-actions")
    .querySelectorAll("button")
    .forEach((button) =>
      button.addEventListener("click", () => chooseAction(button.dataset.action))
    );
  $("popup-reasons").innerHTML = [
    ...(prediction.reasons || []).map((r) => `<li>${r.detail} (+${r.weight.toFixed(2)})</li>`),
    ...(prediction.memory_hits || []).map((m) => `<li>Memory: ${m.text}</li>`),
    `<li>Confidence ${prediction.confidence.toFixed(2)} ≥ threshold</li>`,
  ].join("");
  $("popup").classList.add("open");
}

function closePopup() {
  popupOpen = false;
  $("popup").classList.remove("open");
}

async function chooseAction(action) {
  closePopup();
  openChat();
  addBubble("user", `${action} my card`);
  const result = await api("/api/actions", {
    method: "POST",
    body: JSON.stringify({ user_id: userId(), action }),
  });
  addBubble("agent", result.message);
  await renderCard();
  await refreshInspector();
  await refreshPrediction();
}

async function dismissPopup() {
  closePopup();
  await api("/api/offer-response", {
    method: "POST",
    body: JSON.stringify({ user_id: userId(), accepted: false }),
  });
  await refreshInspector();
}

async function refreshPrediction() {
  const prediction = await api(
    `/api/prediction/${encodeURIComponent(userId())}?respect_cooldown=true`
  );
  applyPrediction(prediction, { allowPopup: false });
}

/* ------------------------------------------------------------- inspector */

async function refreshInspector() {
  const [memory, signals] = await Promise.all([
    api(`/api/memory/${encodeURIComponent(userId())}`),
    api(`/api/signals/${encodeURIComponent(userId())}`),
  ]);

  const profile = memory.profile || {};
  $("profile").textContent = Object.keys(profile).length
    ? JSON.stringify(profile, null, 1)
    : "No durable facts stored yet";

  $("memories").innerHTML =
    (memory.records || [])
      .map(
        (r) =>
          `<li class="${r.kind}">${r.text}<br /><span class="tag">${r.kind} · ${new Date(
            r.timestamp * 1000
          ).toLocaleTimeString()}</span></li>`
      )
      .join("") || '<li class="muted">Memory is empty</li>';

  $("signals").innerHTML =
    (signals.timeline || [])
      .slice(0, 12)
      .map(
        (s) =>
          `<li>${s.type}: ${s.value || "—"}<br /><span class="ts">${new Date(
            s.timestamp * 1000
          ).toLocaleTimeString()}</span></li>`
      )
      .join("") || '<li class="muted">No signals in this visit</li>';
}

/* ------------------------------------------------------------------ chat */

function openChat() {
  $("chat").classList.add("open");
  $("chat-toggle").style.display = "none";
}

function closeChat() {
  $("chat").classList.remove("open");
  $("chat-toggle").style.display = "block";
}

function addBubble(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  $("chat-log").appendChild(bubble);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function sendChat(message) {
  addBubble("user", message);
  const result = await api("/api/chat", {
    method: "POST",
    body: JSON.stringify({ user_id: userId(), message }),
  });
  (result.tool_calls || []).forEach((call) => addBubble("tool", `tool: ${call.name}`));
  addBubble("agent", result.response);
  await renderCard();
  await refreshInspector();
  await refreshPrediction();
}

/* ------------------------------------------------------------------ boot */

async function reset(forgetMemory) {
  await api("/api/reset", {
    method: "POST",
    body: JSON.stringify({ user_id: userId(), forget_memory: forgetMemory }),
  });
  closePopup();
  $("chat-log").innerHTML = "";
  showPage("accounts");
  await refreshInspector();
  await refreshPrediction();
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((item) =>
    item.addEventListener("click", () => navigate(item.dataset.page))
  );
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = $("search-input").value.trim();
    if (query) runSearch(query);
  });
  $("popup-dismiss").addEventListener("click", dismissPopup);
  $("chat-toggle").addEventListener("click", openChat);
  $("chat-close").addEventListener("click", closeChat);
  $("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const message = $("chat-input").value.trim();
    if (!message) return;
    $("chat-input").value = "";
    sendChat(message);
  });
  $("btn-reset-all").addEventListener("click", () => reset(true));
  $("btn-reset-session").addEventListener("click", () => reset(false));
  $("user-id").addEventListener("change", () => reset(false));
}

async function boot() {
  bindEvents();
  showPage("accounts");
  const config = await api("/api/config");
  $("engine-badge").textContent = `${config.engine} · memory: ${config.memory_backend}`;
  await renderCard();
  await refreshInspector();
  await refreshPrediction();
}

boot();
