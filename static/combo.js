"use strict";

// Performer and channel boxes double as dropdowns (like Pornhub's search
// suggestions): focus shows everyone by popularity and scrolls through the
// whole directory; typing narrows it. Picking an entry fills the box and runs
// the search. Data: /api/suggest (built server-side from the sites' lists).
// Performers can be starred; favorites (window.AggFavs, app.js) head the list.
(() => {
  const PAGE = 30;
  const fmt = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  const favs = window.AggFavs;
  const key = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]/g, "");

  function initials(name) {
    return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  }

  function starButton(name, thumb) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "combo-star";
    b.tabIndex = -1; // the list is driven from the text box; the box's own ☆ is keyboard reachable
    b.dataset.name = name;
    b.dataset.thumb = thumb || "";
    syncStar(b);
    return b;
  }
  function syncStar(b) {
    const on = favs.has(b.dataset.name);
    b.textContent = on ? "★" : "☆";
    b.setAttribute("aria-pressed", String(on));
    b.title = `${on ? "Remove" : "Add"} ${b.dataset.name || "this performer"} ${on ? "from" : "to"} favorites`;
    b.setAttribute("aria-label", b.title);
  }

  function setup(input, kind) {
    const starrable = kind === "performer" && !!favs;
    const wrap = document.createElement("div");
    wrap.className = `combo combo-${kind}`;
    input.replaceWith(wrap);
    wrap.appendChild(input);
    const list = document.createElement("ul");
    list.className = "combo-list";
    list.id = `${input.id}-suggest`;
    list.setAttribute("role", "listbox");
    list.hidden = true;
    wrap.appendChild(list);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-expanded", "false");

    // ☆ inside the box: favorite whatever name is typed there.
    let boxStar = null;
    if (starrable) {
      boxStar = starButton("", "");
      boxStar.classList.add("combo-boxstar");
      boxStar.tabIndex = 0;
      wrap.classList.add("has-star");
      wrap.appendChild(boxStar);
      const syncBox = () => {
        boxStar.dataset.name = input.value.trim();
        boxStar.hidden = !boxStar.dataset.name;
        syncStar(boxStar);
      };
      input.addEventListener("input", syncBox);
      window.addEventListener("favchange", syncBox);
      input.form?.addEventListener("submit", syncBox);
      boxStar.addEventListener("mousedown", (e) => e.preventDefault());
      boxStar.addEventListener("click", () => favs.toggle(input.value));
      syncBox();
    }

    // rows = favorites matching the query, then the directory's matches.
    let favRows = [], items = [], total = 0, active = -1, query = null, seq = 0, loading = false, timer = 0;
    const rows = () => favRows.concat(items);

    const open = () => { list.hidden = false; input.setAttribute("aria-expanded", "true"); };
    const close = () => {
      list.hidden = true; input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant"); active = -1;
    };

    function matchingFavs(q) {
      if (!starrable) return [];
      const k = key(q);
      return favs.list.filter((f) => key(f.name).includes(k)).map((f) => ({ ...f, videos: 0, sites: {}, fav: true }));
    }

    async function load(q, append) {
      const mine = ++seq;
      loading = true;
      if (!append) {
        favRows = matchingFavs(q); items = []; total = 0; active = -1; query = q;
        if (favRows.length) render(false, "Loading…"); else renderStatus("Loading…");
        open();
      }
      try {
        const res = await fetch("/api/suggest?" + new URLSearchParams({ kind, q, offset: items.length, limit: PAGE }));
        if (res.status === 401) { close(); window.AggAuth?.requireLogin(); return; }
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        if (mine !== seq) return; // typing moved on
        if (!data.ready) { render(false, `The ${kind} list is still being built. Try again in a minute.`); return; }
        items = items.concat(data.items);
        total = data.total;
        render(append);
      } catch {
        if (mine === seq) render(false, "Couldn't load suggestions.");
      } finally {
        if (mine === seq) loading = false;
      }
    }

    function renderStatus(text) {
      list.replaceChildren(status(text));
    }
    const status = (text) => Object.assign(document.createElement("li"), { className: "combo-status", textContent: text });
    const head = (text) => Object.assign(document.createElement("li"), { className: "combo-head", textContent: text });

    function row(item, i) {
      const li = document.createElement("li");
      li.className = "combo-item";
      li.id = `${list.id}-${i}`;
      li.setAttribute("role", "option");
      li.dataset.i = i;

      const pic = document.createElement("span");
      pic.className = "combo-pic";
      pic.textContent = initials(item.name);
      if (item.thumb) {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "";
        img.referrerPolicy = "no-referrer";
        img.src = item.thumb;
        img.addEventListener("error", () => img.remove());
        pic.appendChild(img);
      }

      const text = document.createElement("span");
      text.className = "combo-text";
      const name = document.createElement("span");
      name.className = "combo-name";
      // Bold the typed part, like the sites' own suggestion lists.
      const at = query ? item.name.toLowerCase().indexOf(query.toLowerCase()) : -1;
      if (at >= 0) {
        name.append(item.name.slice(0, at), Object.assign(document.createElement("b"), { textContent: item.name.slice(at, at + query.length) }), item.name.slice(at + query.length));
      } else {
        name.textContent = item.name;
      }
      const meta = document.createElement("span");
      meta.className = "combo-meta";
      if (item.fav) meta.append("Favorite");
      if (item.videos) meta.append(`${fmt.format(item.videos)} videos`);
      for (const src of Object.keys(item.sites)) {
        const info = SOURCES[src];
        if (!info) continue;
        const dot = document.createElement("span");
        dot.className = "combo-site";
        dot.style.background = info.color;
        dot.style.color = info.ink;
        dot.textContent = info.label;
        meta.append(dot);
      }
      text.append(name, meta);
      li.append(pic, text);
      if (starrable) li.append(starButton(item.name, item.thumb));
      return li;
    }

    function render(append, note = "") {
      if (append) {
        const start = list.querySelectorAll(".combo-item").length;
        list.append(...rows().slice(start).map((it, k) => row(it, start + k)));
        return;
      }
      const out = [];
      if (favRows.length) {
        out.push(head(query ? "Favorites" : `Your favorites (${favRows.length})`));
        favRows.forEach((it, k) => out.push(row(it, k)));
      }
      if (note) {
        out.push(status(note));
      } else if (items.length) {
        out.push(head(query
          ? `${total.toLocaleString()} ${kind}${total === 1 ? "" : "s"} matching`
          : `All ${total.toLocaleString()} ${kind}s, most popular first`));
        items.forEach((it, k) => out.push(row(it, favRows.length + k)));
      } else {
        out.push(status(query ? `No ${kind}s match. Press Enter to search for "${input.value.trim()}" anyway.` : `No ${kind}s yet.`));
      }
      list.replaceChildren(...out);
      list.scrollTop = 0;
    }

    function highlight(i) {
      const els = list.querySelectorAll(".combo-item");
      if (!els.length) return;
      active = Math.max(-1, Math.min(i, els.length - 1));
      els.forEach((r, k) => r.setAttribute("aria-selected", String(k === active)));
      if (active >= 0) {
        els[active].scrollIntoView({ block: "nearest" });
        input.setAttribute("aria-activedescendant", els[active].id);
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function pick(i) {
      const item = rows()[i];
      if (!item) return;
      input.value = item.name;
      close();
      input.form?.requestSubmit();
    }

    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(() => load(input.value.trim(), false), 150);
    };

    input.addEventListener("focus", () => load(input.value.trim(), false));
    input.addEventListener("input", refresh);
    input.addEventListener("blur", () => setTimeout(close, 150));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (list.hidden) { load(input.value.trim(), false); return; }
        highlight(active + 1);
        if (active >= rows().length - 5 && items.length < total && !loading) load(query, true);
      } else if (e.key === "ArrowUp") {
        e.preventDefault(); highlight(active - 1);
      } else if (e.key === "Enter" && !list.hidden && active >= 0) {
        e.preventDefault(); pick(active);
      } else if (e.key === "Escape" && !list.hidden) {
        e.preventDefault(); close();
      } else if (e.key === "Enter" || e.key === "Tab") {
        close();
      }
    });
    // Keep focus in the box while clicking or scrolling the list.
    list.addEventListener("mousedown", (e) => e.preventDefault());
    list.addEventListener("click", (e) => {
      const star = e.target.closest(".combo-star");
      if (star) { favs.toggle(star.dataset.name, star.dataset.thumb); return; }
      const li = e.target.closest(".combo-item");
      if (li) pick(Number(li.dataset.i));
    });
    list.addEventListener("scroll", () => {
      if (!loading && items.length < total && list.scrollTop + list.clientHeight > list.scrollHeight - 200) load(query, true);
    });
    // Starring elsewhere (sidebar, cards, the box) updates the open list's stars;
    // rows stay put so nothing jumps under the pointer.
    if (starrable) window.addEventListener("favchange", () => list.querySelectorAll(".combo-star").forEach(syncStar));
    // Clearing the box with the search field's ✕ shows the full list again.
    input.addEventListener("search", () => { if (!input.value && document.activeElement === input) load("", false); });
  }

  setup(document.getElementById("actor"), "performer");
  setup(document.getElementById("channel"), "channel");
})();
