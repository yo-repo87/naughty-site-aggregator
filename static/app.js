"use strict";

const SOURCES = {
  pornhub:   { label: "Pornhub",   color: "var(--pornhub)" },
  xvideos:   { label: "XVideos",   color: "var(--xvideos)" },
  spankbang: { label: "SpankBang", color: "var(--spankbang)" },
};
const SLIDER_MAX = 180; // minutes; the top of the slider means "no upper limit"
const DEFAULT_EXCLUDE = "trailer, teaser, preview, promo, sneak peek, #shorts, shorts";
const STORE_KEY = "agg-settings-v1";

const $ = (id) => document.getElementById(id);
const els = {
  form: $("search-form"), q: $("q"), actor: $("actor"), channel: $("channel"), tags: $("tags"),
  channelFind: $("channel-find"), channelList: $("channel-list"), channelActive: $("channel-active"),
  blockedWrap: $("blocked-wrap"), blockedList: $("blocked-list"),
  lenMin: $("len-min"), lenMax: $("len-max"), lenMinLabel: $("len-min-label"), lenMaxLabel: $("len-max-label"),
  fill: $("range-fill"), exclude: $("exclude"), strict: $("strict"), sources: $("sources"),
  sort: $("sort"), depth: $("depth"), summary: $("summary"), grid: $("grid"), more: $("load-more"),
  player: $("player"), playerTitle: $("player-title"), playerOpen: $("player-open"),
  playerIframe: $("player-iframe"), cardTpl: $("card-tpl"),
};

const state = {
  videos: [],        // everything fetched for the current search
  sourceStatus: {},  // last per-source result {ok, count, error}
  page: 0,
  fetched: null,     // params the current result set was fetched with
  loading: false,
  searchSeq: 0,
  selected: new Map(), // channel key -> display name; non-empty = show only these
  blocked: new Map(),  // channel key -> display name; persisted
};

/* ---------- persistence ---------- */
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
}
function saveSettings() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      min: +els.lenMin.value, max: +els.lenMax.value, exclude: els.exclude.value,
      strict: els.strict.checked, sort: els.sort.value, depth: els.depth.value,
      sources: enabledSources(), blocked: [...state.blocked],
    }));
  } catch { /* storage unavailable: settings just won't persist */ }
}

/* ---------- helpers ---------- */
const fmtDur = (sec) => {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const fmtMin = (min) => (min >= 60 ? `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, "0")}m` : `${min}m`);
const fmtCount = (n) => n == null ? "" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n);
const norm = (s) => s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
const splitList = (s) => s.split(",").map((t) => t.trim()).filter(Boolean);
// "BrattySis", "Bratty Sis" and "bratty-sis" are the same channel across sites.
const chKey = (s) => norm(s).replace(/[^a-z0-9]/g, "");

function enabledSources() {
  return [...els.sources.querySelectorAll("input:checked")].map((i) => i.value);
}
function lengthRange() {
  const min = +els.lenMin.value, max = +els.lenMax.value;
  return { minSec: min * 60, maxSec: max >= SLIDER_MAX ? null : max * 60 + 59 };
}

/* ---------- slider ---------- */
function syncSlider(e) {
  let lo = +els.lenMin.value, hi = +els.lenMax.value;
  if (lo > hi) {
    if (e && e.target === els.lenMin) els.lenMax.value = hi = lo; else els.lenMin.value = lo = hi;
  }
  els.lenMinLabel.textContent = lo === 0 ? "0m" : fmtMin(lo);
  els.lenMaxLabel.textContent = hi >= SLIDER_MAX ? "Any" : fmtMin(hi);
  els.fill.style.left = `${(lo / SLIDER_MAX) * 100}%`;
  els.fill.style.right = `${100 - (hi / SLIDER_MAX) * 100}%`;
  // Keep the active thumb on top so both stay draggable when they meet.
  els.lenMin.style.zIndex = lo >= SLIDER_MAX - 5 ? 3 : 1;
}

/* Sites pre-filter by length on their end, so widening the range past what
   was fetched needs a fresh search; narrowing is purely local. */
function rangeNeedsRefetch() {
  if (!state.fetched) return false;
  const { minSec, maxSec } = lengthRange();
  const f = state.fetched;
  return minSec < f.minSec || (f.maxSec != null && (maxSec == null || maxSec > f.maxSec));
}

/* ---------- fetching ---------- */
async function search({ append = false } = {}) {
  const sources = enabledSources();
  if (!sources.length) { els.summary.textContent = "Enable at least one source."; return; }
  const { minSec, maxSec } = lengthRange();
  const params = {
    q: els.q.value.trim(), actor: els.actor.value.trim(), channel: els.channel.value.trim(), tags: splitList(els.tags.value).join(","),
    min_sec: minSec, sources: sources.join(","), pages: els.depth.value,
  };
  if (maxSec != null) params.max_sec = maxSec;

  const seq = ++state.searchSeq;
  state.loading = true;
  if (!append) {
    state.page = 0; state.videos = []; state.sourceStatus = {}; state.selected.clear();
    els.grid.innerHTML = Array.from({ length: 8 }, () => '<div class="skeleton"></div>').join("");
    els.summary.textContent = "Searching " + sources.map((s) => SOURCES[s].label).join(", ") + "…";
  }
  els.more.disabled = true;
  els.more.textContent = "Loading…";

  try {
    const page = state.page + 1;
    const res = await fetch("/api/search?" + new URLSearchParams({ ...params, page }));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (seq !== state.searchSeq) return; // a newer search superseded this one
    state.page = page;
    state.fetched = { ...params, minSec, maxSec };
    const seen = new Set(state.videos.map((v) => v.source + v.id));
    for (const v of data.videos) if (!seen.has(v.source + v.id)) state.videos.push(v);
    for (const [name, st] of Object.entries(data.sources)) {
      const prev = state.sourceStatus[name];
      state.sourceStatus[name] = { ...st, count: (append && prev ? prev.count : 0) + st.count };
    }
  } catch (err) {
    if (seq === state.searchSeq) els.summary.textContent = `Search failed: ${err.message}`;
  } finally {
    if (seq === state.searchSeq) {
      state.loading = false;
      els.more.disabled = false;
      els.more.textContent = "Load more";
      render();
    }
  }
}

/* ---------- filtering + sorting ---------- */
function applyFilters() {
  const { minSec, maxSec } = lengthRange();
  const excludes = splitList(els.exclude.value).map(norm);
  const words = els.strict.checked ? norm(els.q.value).split(/\s+/).filter(Boolean) : [];
  const on = new Set(enabledSources());
  // Channel search box: match the uploader, or the title for results whose
  // site didn't say who uploaded them (Pornhub API-only hits).
  const chWanted = chKey(state.fetched?.channel || "");
  const dropped = { length: 0, words: 0, strict: 0, channel: 0 };

  const kept = state.videos.filter((v) => {
    if (!on.has(v.source)) return false;
    const ck = chKey(v.uploader);
    if (ck && state.blocked.has(ck)) { dropped.channel++; return false; }
    if (chWanted && !(ck ? ck.includes(chWanted) : chKey(v.title).includes(chWanted))) { dropped.channel++; return false; }
    // Minute-rounded durations ("12 min") could be up to 59s longer.
    const top = v.duration + (v.duration_exact ? 0 : 59);
    if (top < minSec || (maxSec != null && v.duration > maxSec) || v.duration === 0) { dropped.length++; return false; }
    const t = norm(v.title);
    if (excludes.some((x) => t.includes(x))) { dropped.words++; return false; }
    if (words.length && !words.every((w) => t.includes(w))) { dropped.strict++; return false; }
    return true;
  });
  // Facets are counted before the tick-box selection so unticked channels stay listed.
  const facets = new Map();
  for (const v of kept) {
    const ck = chKey(v.uploader);
    if (!ck) continue;
    const f = facets.get(ck) || facets.set(ck, { name: v.uploader, n: 0, sources: new Set() }).get(ck);
    f.n++; f.sources.add(v.source);
  }
  const selectedOnly = state.selected.size
    ? kept.filter((v) => state.selected.has(chKey(v.uploader)))
    : kept;
  dropped.notSelected = kept.length - selectedOnly.length;

  const sort = els.sort.value;
  if (sort === "mixed") {
    // Round-robin across sources, preserving each site's own relevance order.
    const buckets = {};
    for (const v of selectedOnly) (buckets[v.source] ||= []).push(v);
    const lists = Object.values(buckets), out = [];
    for (let i = 0; out.length < selectedOnly.length; i++) for (const l of lists) if (l[i]) out.push(l[i]);
    return { list: out, dropped, facets };
  }
  const key = {
    longest: (v) => -v.duration, shortest: (v) => v.duration,
    views: (v) => -(v.views ?? -1), rating: (v) => -(v.rating ?? -1),
  }[sort];
  return { list: selectedOnly.slice().sort((a, b) => key(a) - key(b)), dropped, facets };
}

/* ---------- rendering ---------- */
function renderSources() {
  for (const label of els.sources.querySelectorAll(".source")) {
    const name = label.dataset.source, st = state.sourceStatus[name], c = label.querySelector(".count");
    c.className = "count"; c.title = "";
    if (!st) c.textContent = "";
    else if (st.ok) c.textContent = `${st.count} fetched`;
    else { c.textContent = "error"; c.classList.add("err"); c.title = st.error; }
  }
}

function renderChannels(facets) {
  const find = chKey(els.channelFind.value);
  // Ticked channels stay listed even if the current filters leave them empty.
  for (const [k, name] of state.selected) if (!facets.has(k)) facets.set(k, { name, n: 0, sources: new Set() });
  const rows = [...facets.entries()]
    .filter(([k]) => !find || k.includes(find))
    .sort((a, b) => state.selected.has(b[0]) - state.selected.has(a[0]) || b[1].n - a[1].n || a[1].name.localeCompare(b[1].name));

  els.channelList.replaceChildren(...rows.map(([k, f]) => {
    const row = document.createElement("label");
    row.className = "ch-row";
    row.title = f.name;
    const cb = Object.assign(document.createElement("input"), { type: "checkbox", checked: state.selected.has(k) });
    cb.addEventListener("change", () => { cb.checked ? state.selected.set(k, f.name) : state.selected.delete(k); render(); });
    const name = Object.assign(document.createElement("span"), { className: "name", textContent: f.name });
    const dots = document.createElement("span");
    dots.className = "dots";
    for (const src of f.sources) {
      const i = document.createElement("i");
      i.style.background = SOURCES[src].color;
      i.title = SOURCES[src].label;
      dots.appendChild(i);
    }
    const n = Object.assign(document.createElement("span"), { className: "n", textContent: f.n });
    const block = Object.assign(document.createElement("button"), { type: "button", className: "block", textContent: "✕", title: `Hide ${f.name} everywhere` });
    block.addEventListener("click", (e) => { e.preventDefault(); blockChannel(k, f.name); });
    row.append(cb, name, dots, n, block);
    return row;
  }));
  if (!rows.length) els.channelList.innerHTML = `<p class="hint">${facets.size ? "No channels match." : "No channel info in these results."}</p>`;
  els.channelActive.hidden = state.selected.size === 0;

  els.blockedWrap.hidden = state.blocked.size === 0;
  els.blockedList.replaceChildren(...[...state.blocked].map(([k, name]) =>
    chip(`${name} ✕`, "", () => { state.blocked.delete(k); saveSettings(); render(); }, "Unhide this channel")));
}

function blockChannel(k, name) {
  state.blocked.set(k, name);
  state.selected.delete(k);
  saveSettings();
  render();
}

function showOnlyChannel(name) {
  state.selected.clear();
  state.selected.set(chKey(name), name);
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function render() {
  renderSources();
  if (!state.fetched) return;
  const { list, dropped, facets } = applyFilters();
  renderChannels(facets);
  const hidden = dropped.length + dropped.words + dropped.strict + dropped.channel + dropped.notSelected;
  const parts = [];
  if (dropped.length) parts.push(`${dropped.length} outside length range`);
  if (dropped.words) parts.push(`${dropped.words} excluded by title words`);
  if (dropped.strict) parts.push(`${dropped.strict} missing search words`);
  if (dropped.channel) parts.push(`${dropped.channel} other/hidden channels`);
  if (dropped.notSelected) parts.push(`${dropped.notSelected} outside ticked channels`);
  const errs = Object.entries(state.sourceStatus).filter(([, s]) => !s.ok).map(([n]) => SOURCES[n].label);
  els.summary.innerHTML =
    `Showing <b>${list.length}</b> of ${state.videos.length} fetched` +
    (hidden ? ` · hidden: ${parts.join(", ")}` : "") +
    (errs.length ? ` · <span style="color:var(--accent)">${errs.join(", ")} failed (hover source for details)</span>` : "");

  const frag = document.createDocumentFragment();
  for (const v of list) frag.appendChild(card(v));
  els.grid.replaceChildren(frag);
  if (!list.length) els.grid.innerHTML = `<p class="hint">Nothing matches these filters. Try widening the length range or loading more.</p>`;
  els.more.hidden = state.videos.length === 0;
}

function card(v) {
  const node = els.cardTpl.content.firstElementChild.cloneNode(true);
  const thumb = node.querySelector(".thumb"), img = node.querySelector("img");
  thumb.href = v.url;
  img.src = v.thumb;
  img.addEventListener("error", () => { if (v.thumb_alt && img.src !== v.thumb_alt) img.src = v.thumb_alt; });
  img.alt = v.title;
  node.querySelector(".dur").textContent = (v.duration_exact ? "" : "~") + fmtDur(v.duration);
  const src = node.querySelector(".src");
  src.textContent = SOURCES[v.source].label;
  src.classList.add(v.source);

  const title = node.querySelector(".title");
  title.href = v.url;
  title.textContent = v.title;
  title.title = v.title;

  const metaEl = node.querySelector(".meta");
  if (v.uploader) {
    const up = Object.assign(document.createElement("span"), { className: "uploader", textContent: v.uploader, title: "Show only this channel" });
    up.addEventListener("click", () => showOnlyChannel(v.uploader));
    metaEl.appendChild(up);
  }
  const meta = [];
  if (v.views != null) meta.push(`${fmtCount(v.views)} views`);
  if (v.rating != null) meta.push(`${Math.round(v.rating)}% 👍`);
  if (v.published) meta.push(v.published.slice(0, 10));
  for (const m of meta) metaEl.appendChild(Object.assign(document.createElement("span"), { textContent: m }));

  const chips = node.querySelector(".chips");
  for (const a of v.actors) chips.appendChild(chip(a, "actor", () => { els.actor.value = a; search(); }));
  for (const t of v.tags.slice(0, 6)) chips.appendChild(chip(t, "", () => addTag(t)));

  // Click the thumbnail to watch in the site's own embed player.
  thumb.addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return; // let open-in-new-tab work
    e.preventDefault();
    openPlayer(v);
  });
  // Hover preview, streamed straight from the site's CDN.
  if (v.preview) {
    let vid;
    thumb.addEventListener("mouseenter", () => {
      vid = Object.assign(document.createElement("video"), { src: v.preview, muted: true, loop: true, playsInline: true, autoplay: true });
      vid.setAttribute("referrerpolicy", "no-referrer");
      thumb.insertBefore(vid, thumb.querySelector(".dur"));
    });
    thumb.addEventListener("mouseleave", () => { vid?.remove(); vid = null; });
  }
  return node;
}

function chip(text, cls, onClick, title) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `chip ${cls}`.trim();
  b.textContent = text;
  b.title = title || (cls === "actor" ? "Search this performer" : "Add tag filter");
  b.addEventListener("click", onClick);
  return b;
}

function addTag(t) {
  const tags = splitList(els.tags.value);
  if (!tags.map(norm).includes(norm(t))) tags.push(t);
  els.tags.value = tags.join(", ");
  search();
}

function openPlayer(v) {
  els.playerTitle.textContent = v.title;
  els.playerOpen.href = v.url;
  els.playerIframe.src = v.embed_url;
  els.player.showModal();
}
function closePlayer() {
  els.player.close();
}

/* ---------- init ---------- */
function init() {
  try {
    if (localStorage.getItem("agg-age-ok") !== "1") $("agegate").hidden = false;
  } catch { $("agegate").hidden = false; }
  $("age-yes").addEventListener("click", () => {
    try { localStorage.setItem("agg-age-ok", "1"); } catch {}
    $("agegate").hidden = true;
  });

  const s = loadSettings();
  const enabled = new Set(s.sources || Object.keys(SOURCES));
  for (const [name, info] of Object.entries(SOURCES)) {
    const label = document.createElement("label");
    label.className = "source";
    label.dataset.source = name;
    label.innerHTML = `<input type="checkbox" value="${name}"><span class="dot" style="background:${info.color}"></span>${info.label}<span class="count"></span>`;
    label.querySelector("input").checked = enabled.has(name);
    els.sources.appendChild(label);
  }
  for (const [k, name] of s.blocked || []) state.blocked.set(k, name);
  els.lenMin.value = s.min ?? 5;
  els.lenMax.value = s.max ?? SLIDER_MAX;
  els.exclude.value = s.exclude ?? DEFAULT_EXCLUDE;
  els.strict.checked = !!s.strict;
  if (s.sort) els.sort.value = s.sort;
  if (s.depth) els.depth.value = s.depth;
  syncSlider();

  els.form.addEventListener("submit", (e) => { e.preventDefault(); saveSettings(); search(); });
  for (const r of [els.lenMin, els.lenMax]) {
    r.addEventListener("input", (e) => { syncSlider(e); render(); });
    r.addEventListener("change", () => { saveSettings(); if (rangeNeedsRefetch()) search(); });
  }
  for (const b of document.querySelectorAll("[data-preset]")) {
    b.addEventListener("click", () => {
      const [lo, hi] = b.dataset.preset.split(",");
      els.lenMin.value = lo; els.lenMax.value = hi;
      syncSlider(); saveSettings();
      rangeNeedsRefetch() ? search() : render();
    });
  }
  els.exclude.addEventListener("input", () => { saveSettings(); render(); });
  els.strict.addEventListener("change", () => { saveSettings(); render(); });
  els.sort.addEventListener("change", () => { saveSettings(); render(); });
  els.depth.addEventListener("change", saveSettings);
  els.sources.addEventListener("change", () => {
    saveSettings();
    const missing = state.fetched && enabledSources().some((n) => !state.fetched.sources.split(",").includes(n));
    missing ? search() : render();
  });
  els.more.addEventListener("click", () => search({ append: true }));
  els.channelFind.addEventListener("input", render);
  els.channelActive.addEventListener("click", () => { state.selected.clear(); render(); });
  $("player-close").addEventListener("click", closePlayer);
  els.player.addEventListener("close", () => { els.playerIframe.src = "about:blank"; });
  els.player.addEventListener("click", (e) => { if (e.target === els.player) closePlayer(); });
  $("toggle-filters").addEventListener("click", () => $("filters").classList.toggle("open"));
}

init();
