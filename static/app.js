"use strict";

const SOURCES = {
  // ink = badge text colour that stays readable on that background
  pornhub:   { label: "Pornhub",   color: "#ff9900", ink: "#000" },
  xvideos:   { label: "XVideos",   color: "#d9262b", ink: "#fff" },
  spankbang: { label: "SpankBang", color: "#3b82f6", ink: "#fff" },
  tube8:     { label: "Tube8",     color: "#10b981", ink: "#000" },
  youporn:   { label: "YouPorn",   color: "#ec4899", ink: "#fff" },
  redtube:   { label: "RedTube",   color: "#a855f7", ink: "#fff" },
  eporner:   { label: "Eporner",   color: "#22d3ee", ink: "#000" },
  xnxx:      { label: "XNXX",      color: "#facc15", ink: "#000" },
  xhamster:  { label: "xHamster",  color: "#a3e635", ink: "#000" },
  youjizz:   { label: "YouJizz",   color: "#94a3b8", ink: "#000" },
  porntrex:  { label: "PornTrex",  color: "#b45309", ink: "#fff" },
  hqporner:  { label: "HQPorner",  color: "#e2e8f0", ink: "#000" },
};
// Sources that existed before settings stored the *disabled* list (see init).
const LEGACY_SOURCES = ["pornhub", "xvideos", "spankbang"];
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
  adv: $("adv"), advToggle: $("adv-toggle"), quickLen: $("quick-len"),
  sheet: $("filters"), backdrop: $("sheet-backdrop"), sheetDone: $("sheet-done"), autoplay: $("autoplay"),
  mCount: $("m-count"), mFilterCount: $("m-filter-count"),
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
  exhausted: false,    // last "load more" found nothing new: stop infinite scroll
};

// Set by the inline script in index.html before first paint.
const isMobile = () => document.documentElement.classList.contains("mobile");
const canHover = matchMedia("(hover: hover)");

/* ---------- persistence ---------- */
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
}
function saveSettings() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      min: +els.lenMin.value, max: +els.lenMax.value, exclude: els.exclude.value,
      strict: els.strict.checked, sort: els.sort.value, depth: els.depth.value,
      sourcesOff: Object.keys(SOURCES).filter((n) => !enabledSources().includes(n)),
      blocked: [...state.blocked], autoplay: els.autoplay.checked,
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
    state.page = 0; state.videos = []; state.sourceStatus = {}; state.selected.clear(); state.exhausted = false;
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
    const before = state.videos.length;
    for (const v of data.videos) if (!seen.has(v.source + v.id)) state.videos.push(v);
    state.exhausted = append && state.videos.length === before;
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
    else {
      c.textContent = "error"; c.classList.add("err"); c.title = st.error;
      // Touch screens can't hover for the title: tap shows it (without toggling the checkbox).
      c.onclick = (e) => { e.preventDefault(); alert(st.error); };
    }
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
  closeSheet();
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
    (errs.length ? ` · <span style="color:var(--accent)">${errs.join(", ")} failed (${isMobile() ? "see Filters → Sources" : "hover source for details"})</span>` : "");

  const frag = document.createDocumentFragment();
  for (const v of list) frag.appendChild(card(v));
  els.grid.replaceChildren(frag);
  if (!list.length) els.grid.innerHTML = `<p class="hint">Nothing matches these filters. Try widening the length range or loading more.</p>`;
  els.more.hidden = state.videos.length === 0;
  if (state.exhausted) els.more.textContent = "No more results";

  els.mCount.textContent = `${list.length} shown`;
  els.sheetDone.textContent = `Show ${list.length} result${list.length === 1 ? "" : "s"}`;
  renderFilterBadge();
  observeAutoplay();
}

function card(v) {
  const node = els.cardTpl.content.firstElementChild.cloneNode(true);
  const thumb = node.querySelector(".thumb"), img = node.querySelector("img");
  thumb.href = v.url;
  img.src = v.thumb;
  img.addEventListener("error", () => {
    if (v.thumb_alt && img.src !== v.thumb_alt) img.src = v.thumb_alt;
    else img.style.visibility = "hidden"; // plain dark tile instead of broken-image alt text
  });
  img.alt = v.title;
  node.querySelector(".dur").textContent = (v.duration_exact ? "" : "~") + fmtDur(v.duration);
  const src = node.querySelector(".src");
  src.textContent = SOURCES[v.source].label;
  src.style.background = SOURCES[v.source].color;
  src.style.color = SOURCES[v.source].ink;

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
  // Hover preview, streamed straight from the site's CDN. Touch screens have
  // no hover; they get the optional scroll auto-play instead.
  if (v.preview) {
    node.dataset.preview = v.preview;
    thumb.addEventListener("mouseenter", () => { if (canHover.matches) startPreview(node); });
    thumb.addEventListener("mouseleave", () => stopPreview(node));
  }
  return node;
}

function startPreview(node) {
  if (node.querySelector("video") || !node.dataset.preview) return;
  const thumb = node.querySelector(".thumb");
  const vid = Object.assign(document.createElement("video"), { src: node.dataset.preview, muted: true, loop: true, playsInline: true, autoplay: true });
  vid.setAttribute("referrerpolicy", "no-referrer");
  vid.setAttribute("playsinline", "");
  thumb.insertBefore(vid, thumb.querySelector(".dur"));
}
function stopPreview(node) {
  node.querySelector(".thumb video")?.remove();
}

/* ---------- mobile: auto-play the preview of the card in view ---------- */
let autoplayObserver = null;
const visibleCards = new Set();
function observeAutoplay() {
  autoplayObserver?.disconnect();
  visibleCards.clear();
  for (const n of els.grid.querySelectorAll(".card video")) n.remove();
  if (!isMobile() || !els.autoplay.checked) return;
  autoplayObserver = new IntersectionObserver((entries) => {
    for (const e of entries) e.isIntersecting ? visibleCards.add(e.target) : (visibleCards.delete(e.target), stopPreview(e.target));
    // Play one at a time: the top-most card that is mostly on screen.
    const top = [...visibleCards].sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0];
    for (const c of visibleCards) c === top ? startPreview(c) : stopPreview(c);
  }, { threshold: 0.75 });
  for (const c of els.grid.querySelectorAll(".card[data-preview]")) autoplayObserver.observe(c);
}

/* ---------- mobile: filter sheet, quick chips, bottom bar ---------- */
function openSheet() {
  if (els.sheet.classList.contains("open")) return;
  els.backdrop.hidden = false;
  requestAnimationFrame(() => els.backdrop.classList.add("show"));
  els.sheet.classList.add("open");
  document.documentElement.classList.add("sheet-open");
  history.pushState({ overlay: "sheet" }, "");
}
function closeSheet({ fromHistory = false } = {}) {
  if (!els.sheet.classList.contains("open")) return;
  els.sheet.classList.remove("open");
  els.backdrop.classList.remove("show");
  document.documentElement.classList.remove("sheet-open");
  setTimeout(() => { if (!els.sheet.classList.contains("open")) els.backdrop.hidden = true; }, 250);
  if (!fromHistory && history.state?.overlay === "sheet") history.back();
}

function renderQuickLen() {
  const lo = +els.lenMin.value, hi = +els.lenMax.value;
  for (const b of els.quickLen.querySelectorAll("button")) {
    const [a, z] = b.dataset.preset.split(",").map(Number);
    b.classList.toggle("active", a === lo && z === hi);
  }
}

function renderFilterBadge() {
  let n = 0;
  if (+els.lenMin.value > 0 || +els.lenMax.value < SLIDER_MAX) n++;
  if (els.strict.checked) n++;
  if (state.selected.size) n++;
  if (enabledSources().length < Object.keys(SOURCES).length) n++;
  if (els.sort.value !== "mixed") n++;
  els.mFilterCount.hidden = n === 0;
  els.mFilterCount.textContent = n;
  renderQuickLen();
}

function resetFilters() {
  els.lenMin.value = 5; els.lenMax.value = SLIDER_MAX;
  els.strict.checked = false;
  els.sort.value = "mixed";
  state.selected.clear();
  for (const i of els.sources.querySelectorAll("input")) i.checked = true;
  syncSlider(); saveSettings();
  const missing = state.fetched && enabledSources().some((n) => !state.fetched.sources.split(",").includes(n));
  missing || rangeNeedsRefetch() ? search() : render();
}

// Infinite scroll on mobile: fetch the next batch when the end of the list shows.
const moreObserver = new IntersectionObserver((entries) => {
  if (!entries[0].isIntersecting || !isMobile()) return;
  if (state.loading || state.exhausted || !state.videos.length) return;
  search({ append: true });
}, { rootMargin: "600px 0px" });

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
  if (!v.embed_url) { window.open(v.url, "_blank", "noopener,noreferrer"); return; } // site has no embed player
  els.playerIframe.src = v.embed_url;
  for (const n of els.grid.querySelectorAll(".card video")) n.remove(); // no previews behind the player
  els.player.showModal();
  if (isMobile()) history.pushState({ overlay: "player" }, "");
}
function closePlayer() {
  if (els.player.open) els.player.close();
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
  // Remember what was switched *off*, so newly added sites start switched on.
  // Older saves stored the enabled list; convert those once.
  const off = new Set(s.sourcesOff || (s.sources ? LEGACY_SOURCES.filter((n) => !s.sources.includes(n)) : []));
  const enabled = new Set(Object.keys(SOURCES).filter((n) => !off.has(n)));
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
  els.autoplay.checked = !!s.autoplay;
  syncSlider();

  // Swipeable length chips (mobile) reuse the sidebar presets.
  for (const b of document.querySelectorAll(".presets [data-preset]")) {
    const c = b.cloneNode(true);
    els.quickLen.appendChild(c);
  }
  renderQuickLen();

  els.form.addEventListener("submit", (e) => {
    e.preventDefault(); saveSettings(); search();
    if (isMobile()) document.activeElement?.blur(); // drop the on-screen keyboard
  });
  els.advToggle.addEventListener("click", () => {
    const open = els.adv.classList.toggle("open");
    els.advToggle.setAttribute("aria-expanded", open);
    if (open) els.actor.focus();
  });
  if (els.actor.value || els.channel.value || els.tags.value) els.advToggle.click();
  for (const r of [els.lenMin, els.lenMax]) {
    r.addEventListener("input", (e) => { syncSlider(e); render(); renderQuickLen(); });
    r.addEventListener("change", () => { saveSettings(); if (rangeNeedsRefetch()) search(); });
  }
  for (const b of document.querySelectorAll("[data-preset]")) {
    b.addEventListener("click", () => {
      const [lo, hi] = b.dataset.preset.split(",");
      els.lenMin.value = lo; els.lenMax.value = hi;
      syncSlider(); saveSettings(); renderQuickLen();
      rangeNeedsRefetch() ? search() : render();
    });
  }
  els.autoplay.addEventListener("change", () => { saveSettings(); observeAutoplay(); });
  $("m-filters").addEventListener("click", openSheet);
  $("m-top").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  els.sheetDone.addEventListener("click", () => { closeSheet(); window.scrollTo({ top: 0 }); });
  els.backdrop.addEventListener("click", () => closeSheet());
  $("sheet-reset").addEventListener("click", resetFilters);
  // The phone's Back button closes the sheet / player instead of leaving the page.
  window.addEventListener("popstate", () => { closePlayer(); closeSheet({ fromHistory: true }); });
  window.addEventListener("layoutchange", () => { if (!isMobile()) closeSheet(); observeAutoplay(); });
  moreObserver.observe(els.more.parentElement);
  els.exclude.addEventListener("input", () => { saveSettings(); render(); });
  els.strict.addEventListener("change", () => { saveSettings(); render(); });
  els.sort.addEventListener("change", () => { saveSettings(); render(); });
  els.depth.addEventListener("change", saveSettings);
  for (const [id, on] of [["sources-all", true], ["sources-none", false]]) {
    $(id).addEventListener("click", () => {
      for (const i of els.sources.querySelectorAll("input")) i.checked = on;
      els.sources.dispatchEvent(new Event("change"));
    });
  }
  els.sources.addEventListener("change", () => {
    saveSettings();
    const missing = state.fetched && enabledSources().some((n) => !state.fetched.sources.split(",").includes(n));
    missing ? search() : render();
  });
  els.more.addEventListener("click", () => search({ append: true }));
  els.channelFind.addEventListener("input", render);
  els.channelActive.addEventListener("click", () => { state.selected.clear(); render(); });
  $("player-close").addEventListener("click", closePlayer);
  els.player.addEventListener("close", () => {
    els.playerIframe.src = "about:blank";
    // Closed by ✕, Esc or backdrop: drop the history entry openPlayer pushed.
    // (When Back closed it, popstate already moved past that entry.)
    if (history.state?.overlay === "player") history.back();
  });
  els.player.addEventListener("click", (e) => { if (e.target === els.player) closePlayer(); });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

init();
