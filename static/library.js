"use strict";
// Your library, on top of app.js (shares its globals): Watch later, watched
// videos, what was sent where (with MeTube's download progress), select mode
// with bulk actions, saved searches (+ alerts via auth.js), and the
// "New from favorites" feed. Everything lives in the settings blob, so it
// syncs encrypted with the account like the filters do.

const LATER_MAX = 300, WATCHED_MAX = 3000, SENT_MAX = 500, SAVED_MAX = 50;
const vkey = (v) => `${v.source}:${v.id}`;
const lib = {
  later: [],              // [{...video, added}] newest first
  watched: new Set(),     // video keys, oldest first (Set keeps insertion order)
  sent: {},               // key -> { t: target id, k: kind, url, at }
  saved: [],              // [{ id, name, params, alertId }]
  feedSeenAt: 0,          // seconds; feed items first seen after this are "new"
  feed: { items: [], pending: 0, updated: 0 },
  feedNewSince: 0,        // feedSeenAt when the feed view was opened (keeps NEW marks while looking)
  feedFilter: "",         // show one favorite only
  status: {},             // url -> { status, msg, percent } from MeTube
  sel: new Map(),         // select mode: key -> video
  selecting: false,
};

/* ---------- persistence (called by app.js) ---------- */
function libSettings() {
  return {
    later: lib.later, watched: [...lib.watched], sent: lib.sent, saved: lib.saved, feedSeenAt: lib.feedSeenAt,
  };
}
function libLoad(s) {
  const okVideo = (v) => v && typeof v.source === "string" && typeof v.id === "string" && /^https:\/\//.test(v.url || "");
  lib.later = (Array.isArray(s.later) ? s.later : []).filter(okVideo).slice(0, LATER_MAX);
  lib.watched = new Set((Array.isArray(s.watched) ? s.watched : []).filter((k) => typeof k === "string").slice(-WATCHED_MAX));
  lib.sent = s.sent && typeof s.sent === "object" ? s.sent : {};
  lib.saved = (Array.isArray(s.saved) ? s.saved : []).filter((x) => x && x.id && x.params).slice(0, SAVED_MAX);
  lib.feedSeenAt = +s.feedSeenAt || 0;
}

/* ---------- watched / watch later / sent ---------- */
const isWatched = (v) => lib.watched.has(vkey(v));
function markWatched(v) {
  const k = vkey(v);
  if (lib.watched.has(k)) return;
  lib.watched.add(k);
  while (lib.watched.size > WATCHED_MAX) lib.watched.delete(lib.watched.values().next().value);
  saveSettings();
  for (const n of document.querySelectorAll(`.card[data-key="${CSS.escape(k)}"]`)) n.classList.add("watched");
}
function unmarkWatched(v) {
  lib.watched.delete(vkey(v)); saveSettings();
  for (const n of document.querySelectorAll(`.card[data-key="${CSS.escape(vkey(v))}"]`)) n.classList.remove("watched");
}

const inLater = (v) => lib.later.some((x) => vkey(x) === vkey(v));
function slimVideo(v) {
  const keep = ["source", "id", "title", "url", "embed_url", "thumb", "thumb_alt", "duration", "duration_exact", "preview",
    "views", "rating", "uploader", "uploader_url", "published"];
  const out = Object.fromEntries(keep.map((k) => [k, v[k]]));
  return { ...out, actors: (v.actors || []).slice(0, 6), tags: (v.tags || []).slice(0, 6) };
}
function toggleLater(v, on = !inLater(v)) {
  lib.later = lib.later.filter((x) => vkey(x) !== vkey(v));
  if (on) {
    if (lib.later.length >= LATER_MAX) { alert(`Watch later holds up to ${LATER_MAX} videos.`); return; }
    lib.later.unshift({ ...slimVideo(v), added: Date.now() });
  }
  saveSettings();
  for (const b of document.querySelectorAll(`.card[data-key="${CSS.escape(vkey(v))}"] .later-btn`)) syncLaterBtn(b, on);
  renderViewTabs();
  if (state.view === "later") render();
}
function syncLaterBtn(b, on) {
  b.classList.toggle("on", on);
  b.setAttribute("aria-pressed", String(on));
  b.title = on ? "Remove from Watch later" : "Save to Watch later";
  b.setAttribute("aria-label", b.title);
}

// Called by auth.js after a successful send.
function markSent(v, target) {
  const keys = Object.keys(lib.sent);
  if (keys.length >= SENT_MAX) delete lib.sent[keys.sort((a, b) => lib.sent[a].at - lib.sent[b].at)[0]];
  lib.sent[vkey(v)] = { t: target.id, k: target.kind, name: target.name, url: v.url, at: Date.now() };
  delete lib.status[v.url];
  saveSettings();
  for (const n of document.querySelectorAll(`.card[data-key="${CSS.escape(vkey(v))}"]`)) syncSentBadge(n, v);
  if (target.has_status) setTimeout(pollStatus, 4000);
}
window.AggLib = { markSent };

const STATUS_TEXT = { queued: "Queued", downloading: "Downloading", finished: "Downloaded ✓", error: "Download failed" };
function syncSentBadge(node, v) {
  const s = lib.sent[vkey(v)];
  let badge = node.querySelector(".sent-badge");
  if (!s) { badge?.remove(); return; }
  if (!badge) {
    badge = Object.assign(document.createElement("span"), { className: "sent-badge" });
    node.querySelector(".thumb").appendChild(badge);
  }
  const st = lib.status[s.url];
  badge.className = "sent-badge" + (st ? ` st-${st.status}` : "");
  badge.textContent = st ? STATUS_TEXT[st.status] + (st.status === "downloading" && st.percent != null ? ` ${st.percent}%` : "")
    : `Sent to ${s.name || s.k}`;
  badge.title = st?.msg || `Sent to ${s.name || s.k} ${new Date(s.at).toLocaleString()}`;
}

// MeTube progress: every 20 s while something sent in the last two days isn't done.
let statusTimer = 0;
async function pollStatus() {
  clearTimeout(statusTimer);
  const targets = window.AggAuth?.targets?.() || [];
  const byTarget = {};
  for (const s of Object.values(lib.sent)) {
    const t = targets.find((x) => x.id === s.t);
    if (!t?.has_status || Date.now() - s.at > 2 * 86400e3) continue;
    const st = lib.status[s.url]?.status;
    if (st === "finished" || st === "error") continue;
    (byTarget[s.t] ||= []).push(s.url);
  }
  const ids = Object.keys(byTarget);
  if (!ids.length) return;
  if (!document.hidden) {
    for (const id of ids) {
      try { Object.assign(lib.status, await window.AggAuth.targetStatus(id, byTarget[id])); } catch { /* MeTube unreachable: try later */ }
    }
    for (const n of document.querySelectorAll(".card[data-key]")) if (n._video) syncSentBadge(n, n._video);
  }
  statusTimer = setTimeout(pollStatus, 20000);
}

/* ---------- card decorations (called from app.js card()) ---------- */
function decorateCard(node, v) {
  node.dataset.key = vkey(v);
  node._video = v;
  node.classList.toggle("watched", isWatched(v));
  const row = node.querySelector(".chip-row");
  const later = Object.assign(document.createElement("button"), { type: "button", className: "later-btn", innerHTML: '<span aria-hidden="true">🕒</span>' });
  syncLaterBtn(later, inLater(v));
  later.addEventListener("click", () => toggleLater(v));
  row.insertBefore(later, row.querySelector(".dl-btn"));
  syncSentBadge(node, v);

  const sel = Object.assign(document.createElement("span"), { className: "sel", role: "checkbox" });
  sel.setAttribute("aria-hidden", "true");
  node.querySelector(".thumb").appendChild(sel);
  node.classList.toggle("selected", lib.sel.has(vkey(v)));

  if (v.feed?.kind === "performer" || (v.feed && v.first_seen > lib.feedNewSince && lib.feedNewSince)) {
    const tag = Object.assign(document.createElement("div"), { className: "feed-for" });
    if (v.first_seen > lib.feedNewSince && lib.feedNewSince) tag.append(Object.assign(document.createElement("span"), { className: "new-badge", textContent: "NEW" }));
    if (v.feed.kind === "performer") tag.append(`★ ${v.feed.name}${v.feed.also ? ` +${v.feed.also}` : ""}`); // channels are named on the card already
    node.querySelector(".info").prepend(tag);
  }
}

/* ---------- select mode + bulk bar ---------- */
const selectMode = () => lib.selecting;
function setSelecting(on) {
  lib.selecting = on;
  if (!on) lib.sel.clear();
  document.body.classList.toggle("select-mode", on);
  $("select-toggle").setAttribute("aria-pressed", String(on));
  $("select-toggle").textContent = on ? "Done" : "Select";
  for (const n of document.querySelectorAll(".card.selected")) n.classList.remove("selected");
  renderBulkBar();
}
function toggleSelected(v, node) {
  const k = vkey(v);
  if (lib.sel.has(k)) lib.sel.delete(k);
  else if (lib.sel.size >= 50) { alert("Select up to 50 videos at a time."); return; }
  else lib.sel.set(k, v);
  node.classList.toggle("selected", lib.sel.has(k));
  renderBulkBar();
}
function renderBulkBar() {
  const bar = $("bulkbar");
  bar.hidden = !lib.selecting;
  $("bulk-count").textContent = `${lib.sel.size} selected`;
  for (const b of bar.querySelectorAll("[data-needs-sel]")) b.disabled = !lib.sel.size;
  $("bulk-send").hidden = !document.body.classList.contains("has-targets");
}
function bulk(action) {
  const list = [...lib.sel.values()];
  if (action === "all") {
    for (const n of document.querySelectorAll("#grid .card[data-key]")) {
      if (lib.sel.size >= 50) break;
      lib.sel.set(n.dataset.key, n._video); n.classList.add("selected");
    }
    return renderBulkBar();
  }
  if (action === "send") {
    window.AggAuth?.sendVideos(list).then((ok) => { if (ok) setSelecting(false); });
    return;
  }
  if (action === "later") list.forEach((v) => toggleLater(v, true));
  if (action === "watched") list.forEach(markWatched);
  if (action === "unwatched") list.forEach(unmarkWatched);
  setSelecting(false);
}

/* ---------- feed: new from favorites ---------- */
let feedTimer = 0, feedPolls = 0;
async function loadFeed() {
  clearTimeout(feedTimer);
  const performers = favs.list.map((f) => f.name), channels = favChannels.list.map((f) => f.name);
  if (!performers.length && !channels.length) { lib.feed = { items: [], pending: 0, updated: 0 }; renderViewTabs(); return; }
  try {
    const res = await fetch("/api/feed", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ performers, channels }) });
    if (!res.ok) return; // signed out on a login-only server
    lib.feed = await res.json();
  } catch { return; }
  if (!lib.feedSeenAt) { lib.feedSeenAt = Date.now() / 1000; saveSettings(); } // first time: nothing counts as new
  renderViewTabs();
  if (state.view === "feed") render();
  // While favorites are still being looked up, check again soon; otherwise every 15 minutes.
  feedTimer = setTimeout(loadFeed, lib.feed.pending && feedPolls++ < 40 ? 8000 : 15 * 60e3);
}
let feedChangeTimer = 0;
function feedChanged() { clearTimeout(feedChangeTimer); feedChangeTimer = setTimeout(() => { feedPolls = 0; loadFeed(); }, 1500); }

// Feed items, one per video (a video can belong to several favorites), filtered like results.
function feedVideos() {
  const on = new Set(enabledSources());
  const excludes = splitList(els.exclude.value).map(norm);
  const seen = new Map();
  for (const v of lib.feed.items) {
    if (!on.has(v.source) || isBlocked(v) || state.blocked.has(chKey(v.uploader))) continue;
    if (excludes.some((x) => norm(v.title).includes(x))) continue;
    if (els.hideWatched.checked && isWatched(v)) continue;
    const k = vkey(v), prev = seen.get(k);
    if (prev) { prev.feed = { ...prev.feed, also: (prev.feed.also || 0) + 1 }; continue; }
    seen.set(k, { ...v });
  }
  return [...seen.values()].sort((a, b) => b.first_seen - a.first_seen || (Date.parse(b.published) || 0) - (Date.parse(a.published) || 0));
}
const feedNewCount = () => feedVideos().filter((v) => v.first_seen > lib.feedSeenAt).length;

/* ---------- the Feed and Watch later views ---------- */
function renderViewTabs() {
  for (const b of els.views.querySelectorAll("[data-view]")) b.setAttribute("aria-selected", String(b.dataset.view === state.view));
  const n = state.view === "feed" ? 0 : feedNewCount();
  const badge = $("feed-badge");
  badge.hidden = !n;
  badge.textContent = n > 99 ? "99+" : n;
  $("later-count").textContent = lib.later.length ? `(${lib.later.length})` : "";
}

function renderLibraryView() {
  els.more.parentElement.hidden = true;
  let list, summary;
  if (state.view === "later") {
    list = lib.later;
    summary = list.length ? `<b>${list.length}</b> saved to watch later` : "Nothing saved yet. Tap 🕒 on a video to keep it here.";
  } else {
    if (!lib.feedNewSince) lib.feedNewSince = lib.feedSeenAt;
    lib.feedSeenAt = Date.now() / 1000; saveSettings();
    const all = feedVideos();
    list = lib.feedFilter ? all.filter((v) => favKey(v.feed.name) === lib.feedFilter) : all;
    const names = favs.list.length + favChannels.list.length;
    const fresh = all.filter((v) => v.first_seen > lib.feedNewSince).length;
    summary = !names ? "Star performers or channels (☆) and their newest videos collect here."
      : `<b>${list.length}</b> recent videos from ${names} favorite${names === 1 ? "" : "s"}` +
        (fresh ? ` · <b>${fresh} new</b> since you last looked` : "") +
        (lib.feed.pending ? ` · checking ${lib.feed.pending} more…` : "") +
        (lib.feed.updated ? ` · checked ${new Date(lib.feed.updated * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "");
  }
  els.summary.innerHTML = summary;
  if (state.view === "feed" && lib.feed.items.length) els.summary.append(feedFilterChips());
  const frag = document.createDocumentFragment();
  for (const v of list) frag.appendChild(card(v));
  els.grid.replaceChildren(frag);
  els.mCount.textContent = `${list.length} shown`;
}

function feedFilterChips() {
  const wrap = Object.assign(document.createElement("div"), { className: "feed-filter" });
  const names = new Map();
  for (const v of feedVideos()) {
    const k = favKey(v.feed.name);
    const e = names.get(k) || names.set(k, { name: v.feed.name, n: 0, fresh: 0 }).get(k);
    e.n++; if (v.first_seen > lib.feedNewSince) e.fresh++;
  }
  const mk = (k, text) => {
    const b = chip(text, lib.feedFilter === k ? "on" : "", () => { lib.feedFilter = lib.feedFilter === k ? "" : k; render(); }, "Show only this favorite");
    return b;
  };
  wrap.append(mk("", "All"));
  for (const [k, e] of [...names].sort((a, b) => b[1].fresh - a[1].fresh || a[1].name.localeCompare(b[1].name))) {
    wrap.append(mk(k, `${e.name}${e.fresh ? ` · ${e.fresh} new` : ""}`));
  }
  return wrap;
}

/* ---------- saved searches (+ alerts) ---------- */
function currentParams() {
  const { minSec, maxSec } = lengthRange();
  return {
    q: els.q.value.trim(), actor: els.actor.value.trim(), channel: els.channel.value.trim(), tags: splitList(els.tags.value),
    min_sec: minSec, max_sec: maxSec, sources: enabledSources(), sort: els.sort.value,
  };
}
function describeParams(p) {
  return [p.q && `"${p.q}"`, p.actor, p.channel, p.tags?.length && p.tags.join(", ")].filter(Boolean).join(" · ") || "Popular";
}
function saveCurrentSearch() {
  const params = currentParams();
  if (lib.saved.length >= SAVED_MAX) return alert(`You can keep up to ${SAVED_MAX} saved searches.`);
  const name = prompt("Name this search:", describeParams(params));
  if (!name) return;
  lib.saved.unshift({ id: crypto.randomUUID?.() || String(Date.now()), name: name.trim().slice(0, 80), params, alertId: null });
  saveSettings(); renderSaved();
}
function applySaved(x) {
  const p = x.params;
  els.q.value = p.q || ""; els.actor.value = p.actor || ""; els.channel.value = p.channel || "";
  els.tags.value = (p.tags || []).join(", ");
  els.lenMin.value = Math.floor((p.min_sec || 0) / 60);
  els.lenMax.value = p.max_sec == null ? SLIDER_MAX : Math.floor(p.max_sec / 60);
  if (p.sources?.length) for (const i of els.sources.querySelectorAll("input")) i.checked = p.sources.includes(i.value);
  if (p.sort) els.sort.value = p.sort;
  if (p.actor || p.channel || p.tags?.length) els.adv.classList.add("open");
  syncSlider(); saveSettings(); search();
  if (isMobile()) closeSheet();
}
async function toggleAlert(x) {
  if (!window.AggAuth?.alertDialog) return;
  const id = await window.AggAuth.alertDialog(x);
  if (id === undefined) return; // cancelled
  x.alertId = id; saveSettings(); renderSaved();
}
async function removeSaved(x) {
  if (!confirm(`Remove the saved search "${x.name}"?`)) return;
  if (x.alertId) await window.AggAuth?.deleteAlert(x.alertId).catch(() => {});
  lib.saved = lib.saved.filter((y) => y.id !== x.id);
  saveSettings(); renderSaved();
}
function renderSaved() {
  const box = $("saved-list");
  box.replaceChildren(...(lib.saved.length ? lib.saved.map((x) => {
    const row = Object.assign(document.createElement("div"), { className: "saved" });
    const go = Object.assign(document.createElement("button"), { type: "button", className: "saved-go", title: describeParams(x.params) });
    go.append(Object.assign(document.createElement("span"), { className: "saved-name", textContent: x.name }),
      Object.assign(document.createElement("span"), { className: "saved-desc", textContent: describeParams(x.params) }));
    go.addEventListener("click", () => applySaved(x));
    const bell = Object.assign(document.createElement("button"), { type: "button", className: "saved-bell" + (x.alertId ? " on" : ""), textContent: x.alertId ? "🔔" : "🔕" });
    bell.title = x.alertId ? "Alert on: you're notified about new videos. Click to change." : "Get notified about new videos for this search";
    bell.setAttribute("aria-label", bell.title);
    bell.addEventListener("click", () => toggleAlert(x));
    const rm = Object.assign(document.createElement("button"), { type: "button", className: "fav-rm", textContent: "✕", title: `Remove "${x.name}"` });
    rm.addEventListener("click", () => removeSaved(x));
    row.append(go, bell, rm);
    return row;
  }) : [Object.assign(document.createElement("p"), { className: "hint", textContent: "Save a search to run it again in one tap, and optionally get alerts about new videos." })]));
}

/* ---------- start-up (called from app.js init) ---------- */
function libInit() {
  renderSaved();
  renderViewTabs();
  $("save-search").addEventListener("click", saveCurrentSearch);
  $("select-toggle").addEventListener("click", () => setSelecting(!lib.selecting));
  for (const b of $("bulkbar").querySelectorAll("[data-bulk]")) b.addEventListener("click", () => bulk(b.dataset.bulk));
  window.addEventListener("targetschange", () => { renderBulkBar(); pollStatus(); });
  setTimeout(loadFeed, 1200);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });
}
