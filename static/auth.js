"use strict";
// Accounts UI: sign in / register, second step, passkeys and security keys,
// Google/Facebook/Discord (only when the server has them configured), and the
// Account panel (Profile + Security). Shown only when the server has accounts on.
// DOM is built with h() and textContent - user data never goes through innerHTML.

(() => {
  const SETTINGS_KEY = "agg-settings-v1";
  const PROVIDER_ICONS = { google: "G", facebook: "f", discord: "D" };
  let cfg = { enabled: false }, me = null, syncTimer = null;

  /* ---------- tiny helpers ---------- */
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }
  async function api(method, path, body) {
    const res = await fetch(path, {
      method, credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const err = new Error(typeof d === "string" ? d : d?.message || `Something went wrong (${res.status}).`);
      err.status = res.status; err.reauth = !!d?.reauth;
      throw err;
    }
    return data;
  }
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never";
  function toast(msg, bad = false) {
    document.querySelectorAll(".toast").forEach((old) => old.remove()); // one at a time
    const t = h("div", { class: "toast" + (bad ? " bad" : ""), role: "status", text: msg });
    document.body.append(t);
    setTimeout(() => t.classList.add("show"));
    setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, 4200);
  }
  function busy(btn, on) { if (btn) { btn.disabled = on; btn.classList.toggle("busy", on); } }

  /* ---------- WebAuthn (JSON <-> ArrayBuffer) ---------- */
  const b64u = {
    dec: (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0)).buffer,
    enc: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
  };
  const webauthnOK = () => !!window.PublicKeyCredential;
  async function createCredential(o) {
    const pk = { ...o, challenge: b64u.dec(o.challenge), user: { ...o.user, id: b64u.dec(o.user.id) },
      excludeCredentials: (o.excludeCredentials || []).map((c) => ({ ...c, id: b64u.dec(c.id) })) };
    const c = await navigator.credentials.create({ publicKey: pk });
    return { id: c.id, rawId: b64u.enc(c.rawId), type: c.type, authenticatorAttachment: c.authenticatorAttachment,
      clientExtensionResults: c.getClientExtensionResults?.() || {},
      response: { clientDataJSON: b64u.enc(c.response.clientDataJSON), attestationObject: b64u.enc(c.response.attestationObject),
        transports: c.response.getTransports?.() || [] } };
  }
  async function getCredential(o, mediation) {
    const pk = { ...o, challenge: b64u.dec(o.challenge), allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: b64u.dec(c.id) })) };
    const c = await navigator.credentials.get({ publicKey: pk, ...(mediation ? { mediation } : {}) });
    return { id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults?.() || {},
      response: { clientDataJSON: b64u.enc(c.response.clientDataJSON), authenticatorData: b64u.enc(c.response.authenticatorData),
        signature: b64u.enc(c.response.signature), userHandle: c.response.userHandle ? b64u.enc(c.response.userHandle) : null } };
  }
  function webauthnError(e) {
    if (e?.name === "NotAllowedError") return "Cancelled, or the key wasn't recognised.";
    if (e?.name === "InvalidStateError") return "That key is already registered.";
    return e?.message || "The passkey or key didn't work.";
  }

  /* ---------- modal shell ---------- */
  function modal(title, { wide = false, onclose } = {}) {
    const body = h("div", { class: "modal-body" });
    const dlg = h("dialog", { class: "modal" + (wide ? " wide" : ""), "aria-label": title },
      h("div", { class: "modal-head" }, h("h2", { text: title }),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Close", text: "✕", onclick: () => dlg.close() })),
      body);
    dlg.addEventListener("close", () => { dlg.remove(); onclose?.(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    document.body.append(dlg);
    dlg.showModal();
    return { dlg, body, setTitle: (t) => (dlg.querySelector(".modal-head h2").textContent = t) };
  }
  const errLine = () => h("p", { class: "form-error", role: "alert", hidden: true });
  function showErr(el, msg) { el.textContent = msg || ""; el.hidden = !msg; }
  const field = (label, input) => h("label", { class: "field" }, h("span", { text: label }), input);

  /* ---------- header account button ---------- */
  function renderAccountButton() {
    const slot = document.getElementById("account-slot");
    if (!slot) return;
    slot.replaceChildren();
    if (!cfg.enabled) return;
    if (me) {
      const initial = (me.name || me.email || "?").trim()[0]?.toUpperCase() || "?";
      slot.append(h("button", { type: "button", class: "account-btn", title: "Account", "aria-label": "Account", onclick: () => openAccount() },
        h("span", { class: "avatar", text: initial }), h("span", { class: "account-name", text: me.name || me.email || "Account" })));
    } else {
      slot.append(h("button", { type: "button", class: "btn signin-btn", text: "Sign in", onclick: () => openSignIn() }));
    }
  }

  /* ---------- sign in / register ---------- */
  function providerButtons(mode = "login") {
    if (!cfg.providers?.length) return null;
    return h("div", { class: "providers" }, cfg.providers.map((p) =>
      h("a", { class: `btn provider ${p.name}`, href: `/api/auth/oauth/${p.name}/start?mode=${mode}` },
        h("span", { class: "p-icon", "aria-hidden": "true", text: PROVIDER_ICONS[p.name] || "•" }),
        `Continue with ${p.label}`)));
  }

  function openSignIn(view = "signin", opts = {}) {
    const m = modal("Sign in");
    const show = (v, o) => { m.body.replaceChildren(); views[v](o || {}); };
    const views = {
      signin() {
        m.setTitle("Sign in");
        const email = h("input", { type: "email", autocomplete: "username webauthn", required: true, maxlength: 254 });
        const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
        const err = errLine();
        const submit = h("button", { type: "submit", class: "primary wide", text: "Sign in" });
        const form = h("form", { class: "auth-form", onsubmit: async (e) => {
          e.preventDefault(); showErr(err); busy(submit, true);
          try {
            const r = await api("POST", "/api/auth/login", { email: email.value, password: pw.value });
            r.second_step ? show("second", { methods: r.second_step }) : done(m, "Signed in.");
          } catch (x) { showErr(err, x.message); } finally { busy(submit, false); }
        } }, field("Email", email), field("Password", pw), err, submit);

        const keyBtns = webauthnOK() ? h("div", { class: "alt-signin" },
          h("button", { type: "button", class: "btn wide", onclick: (e) => passkeySignIn(e.currentTarget, err, m) },
            h("span", { "aria-hidden": "true", text: "🔑 " }), "Sign in with a passkey"),
          h("button", { type: "button", class: "btn wide", onclick: (e) => {
            if (!email.value) { showErr(err, "Enter your email first, then use your security key."); email.focus(); return; }
            keySignIn(e.currentTarget, err, m, email.value);
          } }, h("span", { "aria-hidden": "true", text: "🔐 " }), "Use a security key")) : null;

        m.body.append(form,
          (keyBtns || cfg.providers?.length) ? h("div", { class: "or", text: "or" }) : null,
          keyBtns, providerButtons("login"),
          cfg.registration ? h("p", { class: "switch" }, "New here? ",
            h("button", { type: "button", class: "link-btn", text: "Create an account", onclick: () => show("register") })) : null);
        email.focus();
      },

      register() {
        m.setTitle("Create an account");
        const name = h("input", { type: "text", autocomplete: "nickname", maxlength: 80, placeholder: "Optional" });
        const email = h("input", { type: "email", autocomplete: "email", required: true, maxlength: 254 });
        const pw = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 });
        const pw2 = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 });
        const err = errLine();
        const submit = h("button", { type: "submit", class: "primary wide", text: "Create account" });
        m.body.append(
          h("form", { class: "auth-form", onsubmit: async (e) => {
            e.preventDefault(); showErr(err);
            if (pw.value !== pw2.value) return showErr(err, "The passwords don't match.");
            busy(submit, true);
            try {
              await api("POST", "/api/auth/register", { name: name.value, email: email.value, password: pw.value });
              await done(m, "Account created. Add a passkey or two-step sign-in under Security.");
            } catch (x) { showErr(err, x.message); } finally { busy(submit, false); }
          } }, field("Name", name), field("Email", email), field("Password (10+ characters)", pw), field("Repeat password", pw2), err, submit),
          cfg.providers?.length ? h("div", { class: "or", text: "or" }) : null,
          providerButtons("login"),
          h("p", { class: "switch" }, "Already have an account? ",
            h("button", { type: "button", class: "link-btn", text: "Sign in", onclick: () => show("signin") })));
        email.focus();
      },

      second({ methods = [] }) {
        m.setTitle("Two-step sign-in");
        const err = errLine();
        const code = h("input", { type: "text", inputmode: "text", autocomplete: "one-time-code", required: true, maxlength: 20, class: "code-input" });
        const submit = h("button", { type: "submit", class: "primary wide", text: "Verify" });
        const hasCode = methods.includes("totp") || methods.includes("backup");
        m.body.append(
          h("p", { class: "lead", text: methods.includes("totp")
            ? "Enter the 6-digit code from your authenticator app, or one of your backup codes."
            : methods.includes("key") ? "Use your security key or passkey to finish signing in." : "Enter one of your backup codes." }),
          hasCode ? h("form", { class: "auth-form", onsubmit: async (e) => {
            e.preventDefault(); showErr(err); busy(submit, true);
            try { await api("POST", "/api/auth/second/code", { code: code.value }); await done(m, "Signed in."); }
            catch (x) { showErr(err, x.message); } finally { busy(submit, false); }
          } }, field(methods.includes("totp") ? "Code" : "Backup code", code), err, submit) : err,
          methods.includes("key") && webauthnOK() ? h("button", { type: "button", class: "btn wide", onclick: async (e) => {
            const b = e.currentTarget; showErr(err); busy(b, true);
            try {
              const o = await api("POST", "/api/auth/second/key/options");
              const credential = await getCredential(o);
              await api("POST", "/api/auth/second/key/verify", { credential });
              await done(m, "Signed in.");
            } catch (x) { showErr(err, x.status ? x.message : webauthnError(x)); } finally { busy(b, false); }
          } }, h("span", { "aria-hidden": "true", text: "🔐 " }), "Use security key or passkey") : null);
        if (hasCode) code.focus();
      },
    };
    show(view, opts);
    return m;
  }

  async function passkeySignIn(btn, err, m) {
    showErr(err); busy(btn, true);
    try {
      const o = await api("POST", "/api/auth/passkey/options");
      const credential = await getCredential(o);
      await api("POST", "/api/auth/passkey/verify", { credential });
      await done(m, "Signed in with your passkey.");
    } catch (x) { showErr(err, x.status ? x.message : webauthnError(x)); } finally { busy(btn, false); }
  }
  async function keySignIn(btn, err, m, email) {
    showErr(err); busy(btn, true);
    try {
      const o = await api("POST", "/api/auth/key/options", { email });
      const credential = await getCredential(o);
      await api("POST", "/api/auth/passkey/verify", { credential });
      await done(m, "Signed in with your security key.");
    } catch (x) { showErr(err, x.status ? x.message : webauthnError(x)); } finally { busy(btn, false); }
  }

  async function done(m, msg) {
    m?.dlg.close();
    await refreshMe();
    await pullSettings();
    toast(msg);
  }

  /* ---------- settings sync ---------- */
  async function pullSettings() {
    if (!me) return;
    try {
      const { settings } = await api("GET", "/api/me/settings");
      if (settings) {
        const local = localStorage.getItem(SETTINGS_KEY);
        if (local !== JSON.stringify(settings)) {
          localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
          location.reload(); // simplest way to apply every saved filter consistently
        }
      } else {
        pushSettings();
      }
    } catch { /* offline or storage blocked: keep local settings */ }
  }
  function pushSettings() {
    if (!me) return;
    let s;
    try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); } catch { s = null; }
    if (s) api("PUT", "/api/me/settings", { settings: s }).catch(() => {});
  }

  /* ---------- confirm it's you ---------- */
  function reauth() {
    return new Promise(async (resolve) => {
      let sec;
      try { sec = await api("GET", "/api/me/security"); } catch { sec = {}; }
      let ok = false;
      const m = modal("Confirm it's you", { onclose: () => resolve(ok) });
      const err = errLine();
      const finish = () => { ok = true; m.dlg.close(); };
      const parts = [h("p", { class: "lead", text: "For your security, confirm it's you before changing this." })];
      if (sec.has_password) {
        const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
        const b = h("button", { type: "submit", class: "primary", text: "Confirm" });
        parts.push(h("form", { class: "auth-form", onsubmit: async (e) => {
          e.preventDefault(); showErr(err); busy(b, true);
          try { await api("POST", "/api/me/reauth", { password: pw.value }); finish(); }
          catch (x) { showErr(err, x.message); } finally { busy(b, false); }
        } }, field("Password", pw), b));
        setTimeout(() => pw.focus());
      }
      if (sec.totp_enabled) {
        const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, class: "code-input" });
        const b = h("button", { type: "submit", class: sec.has_password ? "btn" : "primary", text: "Confirm with code" });
        parts.push(h("form", { class: "auth-form", onsubmit: async (e) => {
          e.preventDefault(); showErr(err); busy(b, true);
          try { await api("POST", "/api/me/reauth", { code: code.value }); finish(); }
          catch (x) { showErr(err, x.message); } finally { busy(b, false); }
        } }, field("Authenticator code", code), b));
      }
      if (sec.credentials?.length && webauthnOK()) {
        parts.push(h("button", { type: "button", class: "btn wide", onclick: async (e) => {
          const b = e.currentTarget; showErr(err); busy(b, true);
          try {
            const o = await api("POST", "/api/me/reauth/key/options");
            await api("POST", "/api/me/reauth/key/verify", { credential: await getCredential(o) });
            finish();
          } catch (x) { showErr(err, x.status ? x.message : webauthnError(x)); } finally { busy(b, false); }
        } }, h("span", { "aria-hidden": "true", text: "🔑 " }), "Use a passkey or security key"));
      }
      for (const p of (sec.providers || []).filter((p) => p.connected)) {
        parts.push(h("a", { class: `btn provider ${p.name} wide`, href: `/api/auth/oauth/${p.name}/start?mode=reauth` },
          h("span", { class: "p-icon", "aria-hidden": "true", text: PROVIDER_ICONS[p.name] || "•" }), `Confirm with ${p.label}`));
      }
      if (parts.length === 1) parts.push(h("p", { text: "Sign out and sign in again, then retry within 15 minutes." }));
      m.body.append(...parts, err);
    });
  }
  // Runs a sensitive action; if the server asks, confirms identity and retries once.
  async function guarded(fn) {
    try { return await fn(); }
    catch (e) {
      if (!e.reauth) throw e;
      if (!(await reauth())) throw new Error("Not confirmed.");
      return await fn();
    }
  }

  /* ---------- account panel ---------- */
  function openAccount(tab = "profile") {
    const m = modal("Your account", { wide: true });
    const tabs = h("div", { class: "tabs", role: "tablist" });
    const pane = h("div", { class: "tab-pane" });
    const tabDefs = [["profile", "Profile"], ["security", "Security"]];
    const select = (t) => {
      for (const b of tabs.children) b.setAttribute("aria-selected", b.dataset.tab === t);
      pane.replaceChildren(h("p", { class: "hint", text: "Loading…" }));
      (t === "profile" ? renderProfile : renderSecurity)(pane, () => select(t)).catch((e) => pane.replaceChildren(h("p", { class: "form-error", text: e.message })));
    };
    for (const [id, label] of tabDefs) tabs.append(h("button", { type: "button", role: "tab", "data-tab": id, text: label, onclick: () => select(id) }));
    m.body.append(tabs, pane);
    select(tab);
  }

  async function renderProfile(pane, refresh) {
    await refreshMe();
    if (!me) return pane.replaceChildren(h("p", { text: "You're signed out." }));
    const err = errLine();
    const name = h("input", { type: "text", value: me.name || "", maxlength: 80 });
    const email = h("input", { type: "email", value: me.email || "", maxlength: 254 });
    const save = h("button", { type: "submit", class: "primary", text: "Save" });
    pane.replaceChildren(
      h("form", { class: "auth-form", onsubmit: async (e) => {
        e.preventDefault(); showErr(err); busy(save, true);
        try {
          const body = { name: name.value };
          if (email.value.trim().toLowerCase() !== (me.email || "")) body.email = email.value;
          await guarded(() => api("PATCH", "/api/me", body));
          await refreshMe(); toast("Profile saved."); refresh();
        } catch (x) { showErr(err, x.message); } finally { busy(save, false); }
      } }, field("Name", name), field("Email", email), err, save),
      h("p", { class: "hint", text: `Member since ${fmtDate(me.created_at)}. Your email, name and saved filters are stored encrypted.` }),
      h("div", { class: "row-actions" },
        h("button", { type: "button", class: "btn", text: "Sign out", onclick: async () => {
          await api("POST", "/api/auth/logout").catch(() => {}); me = null; renderAccountButton();
          document.querySelector("dialog.modal")?.close(); toast("Signed out.");
        } })),
      h("section", { class: "danger" },
        h("h3", { text: "Delete account" }),
        h("p", { class: "hint", text: "Permanently deletes your account, keys, sign-in history and saved settings. This can't be undone." }),
        h("button", { type: "button", class: "btn danger-btn", text: "Delete my account", onclick: async () => {
          if (!confirm("Delete your account permanently?")) return;
          try { await guarded(() => api("DELETE", "/api/me")); me = null; renderAccountButton(); document.querySelector("dialog.modal")?.close(); toast("Account deleted."); }
          catch (x) { toast(x.message, true); }
        } })));
  }

  function showBackupCodes(codes, title = "Save your backup codes") {
    const m = modal(title);
    const text = codes.join("\n");
    m.body.append(
      h("p", { class: "lead", text: "Each code works once if you lose your phone or security key. Store them somewhere safe — they won't be shown again." }),
      h("ol", { class: "codes" }, codes.map((c) => h("li", { text: c }))),
      h("div", { class: "row-actions" },
        h("button", { type: "button", class: "btn", text: "Copy", onclick: () => navigator.clipboard?.writeText(text).then(() => toast("Copied.")) }),
        h("button", { type: "button", class: "btn", text: "Download", onclick: () => {
          const a = h("a", { href: URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" })), download: "aggregator-backup-codes.txt" });
          a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        } }),
        h("button", { type: "button", class: "primary", text: "I've saved them", onclick: () => m.dlg.close() })));
  }

  async function addKey(kind, refresh) {
    if (!webauthnOK()) return toast("This browser doesn't support passkeys or security keys.", true);
    const label = kind === "passkey" ? "passkey" : "security key";
    const name = prompt(`Name this ${label} (e.g. "iPhone", "YubiKey 5"):`, kind === "passkey" ? "Passkey" : "Security key");
    if (name === null) return;
    try {
      const o = await guarded(() => api("POST", "/api/me/webauthn/options", { kind }));
      const credential = await createCredential(o);
      const r = await api("POST", "/api/me/webauthn/verify", { credential, name });
      toast(kind === "passkey" ? "Passkey added. You can now sign in with it." : "Security key added. It's now required after your password.");
      if (r.backup_codes) showBackupCodes(r.backup_codes);
      refresh();
    } catch (x) { toast(x.status ? x.message : webauthnError(x), true); }
  }

  async function renderSecurity(pane, refresh) {
    const s = await api("GET", "/api/me/security");
    const sec = (title, desc, ...kids) => h("section", { class: "sec-card" }, h("h3", { text: title }), desc ? h("p", { class: "hint", text: desc }) : null, ...kids);
    const passkeys = s.credentials.filter((c) => c.kind === "passkey");
    const keys = s.credentials.filter((c) => c.kind === "security_key");
    const credList = (list, empty) => list.length ? h("ul", { class: "item-list" }, list.map((c) => h("li", {},
      h("div", { class: "item-main" }, h("strong", { text: c.name }),
        h("span", { class: "hint", text: `Added ${fmtDate(c.created_at)} · last used ${fmtDate(c.last_used_at)}${c.backed_up ? " · synced" : ""}` })),
      h("div", { class: "item-actions" },
        h("button", { type: "button", class: "link-btn", text: "Rename", onclick: async () => {
          const n = prompt("New name:", c.name); if (!n) return;
          try { await api("PATCH", `/api/me/webauthn/${c.id}`, { name: n }); refresh(); } catch (x) { toast(x.message, true); }
        } }),
        h("button", { type: "button", class: "link-btn danger-link", text: "Remove", onclick: async () => {
          if (!confirm(`Remove "${c.name}"?`)) return;
          try { await guarded(() => api("DELETE", `/api/me/webauthn/${c.id}`)); toast("Removed."); refresh(); } catch (x) { toast(x.message, true); }
        } }))))) : h("p", { class: "hint", text: empty });

    // password
    const pwInput = h("input", { type: "password", autocomplete: "new-password", minlength: 10, placeholder: "New password (10+ characters)" });
    const pwErr = errLine();
    const pwBtn = h("button", { type: "submit", class: "btn", text: s.has_password ? "Change password" : "Set password" });
    const pwCard = sec("Password", s.has_password ? "Changing it signs out your other devices." : "You sign in without a password. You can add one as a backup.",
      h("form", { class: "inline-form", onsubmit: async (e) => {
        e.preventDefault(); showErr(pwErr); busy(pwBtn, true);
        try { await guarded(() => api("POST", "/api/me/password", { password: pwInput.value })); pwInput.value = ""; toast("Password saved."); refresh(); }
        catch (x) { showErr(pwErr, x.message); } finally { busy(pwBtn, false); }
      } }, pwInput, pwBtn), pwErr);

    // authenticator app
    const totpCard = sec("Authenticator app (two-step sign-in)",
      s.totp_enabled ? "On. After your password you'll be asked for a 6-digit code." : "Adds a 6-digit code from an app like Google Authenticator, Authy, 1Password or Bitwarden.",
      s.totp_enabled
        ? h("div", { class: "row-actions" },
            h("span", { class: "badge-on", text: "On" }),
            h("button", { type: "button", class: "btn", text: "Turn off", onclick: async () => {
              if (!confirm("Turn off the authenticator app?")) return;
              try { await guarded(() => api("POST", "/api/me/totp/disable")); toast("Authenticator app turned off."); refresh(); } catch (x) { toast(x.message, true); }
            } }))
        : h("button", { type: "button", class: "primary", text: "Set up", onclick: () => setupTotp(refresh) }));

    const codesCard = (s.totp_enabled || keys.length) ? sec("Backup codes",
      `${s.backup_codes_left} unused. Use one if you lose your phone or security key.`,
      h("button", { type: "button", class: "btn", text: "Create new codes", onclick: async () => {
        if (!confirm("Create new backup codes? Your old ones stop working.")) return;
        try { const r = await guarded(() => api("POST", "/api/me/backup-codes")); showBackupCodes(r.backup_codes, "Your new backup codes"); refresh(); }
        catch (x) { toast(x.message, true); }
      } })) : null;

    const pkCard = sec("Passkeys", "Sign in with your fingerprint, face or device PIN — no password needed. Synced passkeys work on your other devices too.",
      credList(passkeys, "No passkeys yet."),
      h("button", { type: "button", class: "btn", text: "Add a passkey", onclick: () => addKey("passkey", refresh) }));
    const keyCard = sec("Security keys", "A USB or NFC key (e.g. YubiKey). Once added it's required after your password, and with its PIN it can sign you in on its own.",
      credList(keys, "No security keys yet."),
      h("button", { type: "button", class: "btn", text: "Add a security key", onclick: () => addKey("security_key", refresh) }));

    const provCard = s.providers.length ? sec("Connected accounts", "Sign in with these instead of a password.",
      h("ul", { class: "item-list" }, s.providers.map((p) => h("li", {},
        h("div", { class: "item-main" }, h("strong", { text: p.label }), h("span", { class: "hint", text: p.connected ? (p.email || "Connected") : "Not connected" })),
        h("div", { class: "item-actions" }, p.connected
          ? h("button", { type: "button", class: "link-btn danger-link", text: "Disconnect", onclick: async () => {
              if (!confirm(`Disconnect ${p.label}?`)) return;
              try { await guarded(() => api("DELETE", `/api/me/identities/${p.name}`)); toast(`${p.label} disconnected.`); refresh(); } catch (x) { toast(x.message, true); }
            } })
          : h("a", { class: "link-btn", href: `/api/auth/oauth/${p.name}/start?mode=link`, text: "Connect" })))))) : null;

    const sessCard = sec("Signed-in devices", null,
      h("ul", { class: "item-list" }, s.sessions.map((x) => h("li", {},
        h("div", { class: "item-main" }, h("strong", { text: x.device || "Unknown device" }, x.current ? h("span", { class: "badge-on", text: "This device" }) : null),
          h("span", { class: "hint", text: `Last active ${fmtDate(x.last_seen_at)} · ${x.ip || "unknown IP"} · signed in ${fmtDate(x.created_at)}` })),
        h("div", { class: "item-actions" }, x.current ? null : h("button", { type: "button", class: "link-btn danger-link", text: "Sign out", onclick: async () => {
          try { await api("DELETE", `/api/me/sessions/${x.id}`); refresh(); } catch (e) { toast(e.message, true); }
        } }))))),
      s.sessions.length > 1 ? h("button", { type: "button", class: "btn", text: "Sign out all other devices", onclick: async () => {
        try { await api("POST", "/api/me/sessions/revoke-others"); toast("Other devices signed out."); refresh(); } catch (e) { toast(e.message, true); }
      } }) : null);

    const evCard = sec("Recent security activity", null,
      s.events.length ? h("ul", { class: "item-list compact" }, s.events.map((e) => h("li", {},
        h("div", { class: "item-main" }, h("strong", { text: e.event }), h("span", { class: "hint", text: `${fmtDate(e.at)}${e.detail ? " · " + e.detail : ""}` }))))) : h("p", { class: "hint", text: "Nothing yet." }));

    pane.replaceChildren(pkCard, keyCard, totpCard, codesCard, pwCard, provCard, sessCard, evCard);
  }

  async function setupTotp(refresh) {
    let r;
    try { r = await guarded(() => api("POST", "/api/me/totp/setup")); } catch (x) { return toast(x.message, true); }
    const m = modal("Set up your authenticator app");
    const qr = h("div", { class: "qr", "aria-label": "QR code" });
    qr.innerHTML = r.qr_svg; // SVG generated by our server (segno), no user input
    const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, required: true, class: "code-input" });
    const err = errLine();
    const b = h("button", { type: "submit", class: "primary wide", text: "Turn on" });
    m.body.append(
      h("ol", { class: "steps" },
        h("li", { text: "Open your authenticator app and scan this code." }),
        h("li", {}, "Can't scan? Enter this key: ", h("code", { class: "secret", text: r.secret })),
        h("li", { text: "Type the 6-digit code it shows." })),
      qr,
      h("form", { class: "auth-form", onsubmit: async (e) => {
        e.preventDefault(); showErr(err); busy(b, true);
        try {
          const res = await api("POST", "/api/me/totp/enable", { code: code.value });
          m.dlg.close(); toast("Two-step sign-in is on."); showBackupCodes(res.backup_codes); refresh();
        } catch (x) { showErr(err, x.message); } finally { busy(b, false); }
      } }, field("Code", code), err, b));
    code.focus();
  }

  /* ---------- state ---------- */
  async function refreshMe() {
    try { me = (await api("GET", "/api/me")).user; } catch { me = null; }
    renderAccountButton();
    return me;
  }

  // Results of OAuth redirects come back in the URL fragment.
  function handleFragment() {
    const p = new URLSearchParams(location.hash.slice(1));
    const result = p.get("auth");
    if (!result) return;
    history.replaceState(null, "", location.pathname + location.search);
    const msg = p.get("msg");
    if (result === "ok") { pullSettings(); toast("Signed in."); }
    else if (result === "linked") { toast(msg || "Account connected."); openAccount("security"); }
    else if (result === "reauth") { toast("Confirmed. Try your change again."); openAccount("security"); }
    else if (result === "second") openSignIn("second", { methods: (p.get("methods") || "").split(",") });
    else toast(msg || "Sign-in didn't complete.", true);
  }

  window.AggAuth = {
    settingsChanged() { if (!me) return; clearTimeout(syncTimer); syncTimer = setTimeout(pushSettings, 1500); },
    requireLogin() { if (cfg.enabled && !me && !document.querySelector("dialog.modal")) openSignIn(); },
  };

  (async () => {
    try { cfg = await api("GET", "/api/auth/config"); } catch { cfg = { enabled: false }; }
    if (!cfg.enabled) return;
    await refreshMe();
    handleFragment();
    if (cfg.require_login && !me) openSignIn();
  })();
})();
