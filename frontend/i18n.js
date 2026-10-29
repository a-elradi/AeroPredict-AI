/* AeroPredict — page languages.
 *
 * Pages are written in English. For another language this loads /i18n/<lang>.json, a map from the
 * English text to the translation, and swaps every text node and label on the page, including text
 * that app.js or the server adds later. Keys may contain {0}, {1}… for numbers, codes and names,
 * e.g. "Forecast at {0}". Text the dictionary doesn't know stays in English.
 */
(function () {
  const LANGS = {
    en: { name: "English", locale: "en-GB" },
    ar: { name: "العربية", locale: "ar-u-nu-latn", dir: "rtl" },
    es: { name: "Español", locale: "es" },
    tr: { name: "Türkçe", locale: "tr" },
  };
  const ATTRS = ["placeholder", "aria-label", "title", "alt"];
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "CODE", "svg"]);
  const root = document.documentElement;

  const store = {
    get() { try { return localStorage.getItem("lang"); } catch { return null; } },
    set(v) { try { localStorage.setItem("lang", v); } catch { /* private mode: still works for this page */ } },
  };

  function pick() {
    const fromUrl = new URLSearchParams(location.search).get("lang");
    if (LANGS[fromUrl]) { store.set(fromUrl); return fromUrl; }
    const saved = store.get();
    if (LANGS[saved]) return saved;
    for (const l of navigator.languages || [navigator.language || ""]) {
      const code = (l || "").slice(0, 2).toLowerCase();
      if (LANGS[code]) return code;
    }
    return "en";
  }

  // ?i18n-collect: behave like a translated page with an empty dictionary and list what's missing
  const collect = new URLSearchParams(location.search).has("i18n-collect");
  if (collect) { LANGS.xx = { name: "xx", locale: "en-GB" }; window.__i18nMissing = new Set(); }

  let lang = collect ? "xx" : pick();
  let dict = {};
  let patterns = [];          // [{re, out}] built from keys containing {n}
  const cache = new Map();    // dictionary loads by language

  function setDocumentLang() {
    root.lang = lang;
    root.dir = LANGS[lang].dir || "ltr";
    if (lang === "ar" && !document.getElementById("arabicFont")) {
      const link = document.createElement("link");
      link.id = "arabicFont"; link.rel = "stylesheet";
      link.href = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600&display=swap";
      document.head.appendChild(link);
    }
  }
  setDocumentLang();
  if (lang !== "en") root.classList.add("i18n-loading");

  /* ── Lookup ── */
  const norm = s => s.replace(/\s+/g, " ").trim();
  const escape = s => s.replace(/[.*+?^$()|[\]\\]/g, "\\$&");

  function compile(d) {
    patterns = Object.keys(d).filter(k => /\{\d\}/.test(k))
      .sort((a, b) => b.replace(/\{\d\}/g, "").length - a.replace(/\{\d\}/g, "").length)
      .map(k => {
        const order = [];
        const re = new RegExp("^" + escape(k).replace(/\\?\{(\d)\\?\}/g, (_, n) => { order.push(+n); return "(.+?)"; }) + "$");
        return { re, order, out: d[k] };
      });
  }

  const SENTENCE = /(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?:;])\s+(?=[A-Z0-9(“"])/;

  function lookup(text, depth = 0) {
    const key = norm(text);
    if (!key || lang === "en") return null;
    if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
    for (const p of patterns) {
      const m = key.match(p.re);
      if (!m) continue;
      const vals = {};
      p.order.forEach((n, i) => { vals[n] = (depth < 3 && lookup(m[i + 1], depth + 1)) || m[i + 1]; });
      return p.out.replace(/\{(\d)\}/g, (_, n) => vals[n] ?? "");
    }
    // The same sentence without its full stop
    if (/[^.]\.$/.test(key)) {
      const bare = lookup(key.slice(0, -1), depth + 1);
      if (bare != null) return /[.!?。؟]$/.test(bare) ? bare : bare + ".";
    }
    if (depth === 0 && collect && /[A-Za-z]{2}/.test(key)) window.__i18nMissing.add(key);
    // Text built from pieces: "Level 2 · Wheelchair accessible", sentences one after another,
    // or a list such as "DXB (64% on time), BKK (76% on time)". Translate piece by piece.
    const splits = depth === 0 ? [[/ · /, " · "], [SENTENCE, " "]] : depth < 3 ? [[/, (?=[A-Z0-9])/, ", "]] : [];
    for (const [split, glue] of splits) {
      const parts = key.split(split);
      if (parts.length < 2) continue;
      const out = parts.map(s => lookup(s, 1) ?? (/ · /.test(s) ? lookup(s) : null));
      return out.some(x => x != null) ? out.map((x, i) => x ?? parts[i]).join(glue) : null;
    }
    return null;
  }

  function t(text) { return lookup(text) ?? text; }

  /* ── Applying to the page ── */
  const origText = new WeakMap();   // text node -> English
  const lastText = new WeakMap();   // text node -> what we last wrote
  const origAttr = new WeakMap();   // element -> {attr: English}
  let translating = false;

  function skipped(el) {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      if (SKIP.has(e.nodeName) || e.getAttribute?.("translate") === "no") return true;
    }
    return false;
  }

  function doText(node) {
    if (lastText.get(node) !== node.data) origText.set(node, node.data); // changed by the page: new English
    const en = origText.get(node);
    if (!en || !/[A-Za-z]/.test(en)) return;
    const tr = lookup(en);
    const lead = en.match(/^\s*/)[0], trail = en.match(/\s*$/)[0];
    const out = tr == null ? en : lead + tr + trail;
    if (node.data !== out) node.data = out;
    lastText.set(node, out);
  }

  function doAttrs(el) {
    let saved = origAttr.get(el);
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      if (!saved) { saved = {}; origAttr.set(el, saved); }
      const cur = el.getAttribute(a);
      if (!(a in saved) || (saved["_" + a] !== cur)) saved[a] = cur;
      const out = t(saved[a]);
      saved["_" + a] = out;
      if (cur !== out) el.setAttribute(a, out);
    }
  }

  /* A paragraph with links or bold words is translated as one sentence, with the inline elements
     marked <0>…</0>, <1>…</1> so they can move to where the language needs them. */
  const INLINE = new Set(["A", "STRONG", "EM", "B", "I", "SPAN", "SMALL", "ABBR", "CODE"]);
  const rich = new WeakMap();       // element -> {key, nodes, texts, produced}
  let firstPass = false;

  function richKey(el) {
    if (!el.firstElementChild) return null;
    let words = false, i = 0;
    const parts = [];
    for (const n of el.childNodes) {
      if (n.nodeType === 3) { parts.push(n.data); if (/[A-Za-z]{2}/.test(n.data)) words = true; }
      else if (n.nodeType === 1) {
        if (!INLINE.has(n.nodeName) || n.firstElementChild || n.getAttribute("translate") === "no") return null;
        // Numbers and arrows stay live (the page may update them), so they're left out of the key
        parts.push(`<${i}>${/[A-Za-z]{2}/.test(n.textContent) ? n.textContent : ""}</${i}>`); i++;
      }
    }
    return words ? norm(parts.join("")) : null;
  }

  const rewritten = (el, st) => !st.produced || el.childNodes.length !== st.produced.length ||
    st.produced.some((n, i) => el.childNodes[i] !== n);

  function doRich(el) {
    let st = rich.get(el);
    if (st && rewritten(el, st)) { rich.delete(el); st = null; } // the page replaced the content
    if (!st) {
      const key = richKey(el);
      if (!key) return false;
      if (collect) { if (firstPass) window.__i18nMissing.add(key); return firstPass; }
      if (lang === "en" || !Object.prototype.hasOwnProperty.call(dict, key)) return false;
      const nodes = [...el.childNodes];
      st = { key, nodes, texts: nodes.map(n => n.textContent), produced: null, changed: new Set() };
      rich.set(el, st);
    }
    const out = lang === "en" ? null : dict[st.key];
    let fresh;
    if (out == null) {
      st.nodes.forEach((n, i) => { if (n.nodeType === 3 || st.changed.has(n)) n.textContent = st.texts[i]; });
      fresh = st.nodes;
    } else {
      const inline = st.nodes.filter(n => n.nodeType === 1);
      fresh = [];
      const re = /<(\d+)>(.*?)<\/\1>/g;
      let last = 0, m;
      while ((m = re.exec(out))) {
        if (m.index > last) fresh.push(document.createTextNode(out.slice(last, m.index)));
        const node = inline[+m[1]];
        if (node) {
          if (m[2] !== "") { node.textContent = m[2]; st.changed.add(node); }
          fresh.push(node);
        }
        last = re.lastIndex;
      }
      if (last < out.length) fresh.push(document.createTextNode(out.slice(last)));
    }
    el.replaceChildren(...fresh);
    st.produced = [...el.childNodes];
    st.nodes.forEach(n => n.nodeType === 1 && doAttrs(n));
    return true;
  }

  function walk(el) {
    if (SKIP.has(el.nodeName) || el.getAttribute("translate") === "no") return;
    doAttrs(el);
    if (doRich(el)) return;
    for (const n of [...el.childNodes]) {
      if (n.nodeType === 3) doText(n);
      else if (n.nodeType === 1) walk(n);
    }
  }

  function apply(node) {
    if (!node || skipped(node.nodeType === 1 ? node : node.parentElement)) return;
    const parent = node.parentElement;
    if (parent && rich.has(parent)) {          // inside a sentence we rebuilt: only redo it if the page changed it
      if (rewritten(parent, rich.get(parent))) doRich(parent);
      return;
    }
    if (node.nodeType === 3) doText(node);
    else if (node.nodeType === 1) walk(node);
  }

  let titleEn = null, descEn = null;
  function applyAll() {
    translating = true;
    titleEn ??= document.title;
    document.title = t(titleEn);
    const desc = document.querySelector('meta[name="description"]');
    if (desc) { descEn ??= desc.content; desc.content = t(descEn); }
    walk(document.body);
    translating = false;
    document.dispatchEvent(new CustomEvent("languagechange", { detail: { lang } }));
  }

  const observer = new MutationObserver(records => {
    if (translating) return;
    translating = true;
    for (const r of records) {
      if (r.type === "childList") r.addedNodes.forEach(apply);
      else if (r.type === "characterData") apply(r.target);
      else if (r.type === "attributes" && ATTRS.includes(r.attributeName)) doAttrs(r.target);
    }
    translating = false;
  });

  async function load(l) {
    if (l === "en" || l === "xx") return {};
    if (!cache.has(l)) {
      cache.set(l, fetch(`/i18n/${l}.json`).then(r => r.ok ? r.json() : {}).catch(() => ({})));
    }
    return cache.get(l);
  }

  async function setLang(l) {
    if (!LANGS[l]) return;
    lang = l; if (!collect) store.set(l);
    dict = await load(l);
    compile(dict);
    setDocumentLang();
    applyAll();
    syncSwitchers();
  }

  /* ── Language menu (sits in the top bar) ── */
  const globe = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/>
    <path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg>`;
  const chevron = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`;
  const check = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`;

  function buildSwitcher() {
    const wrap = document.createElement("div");
    wrap.className = "lang-switch";
    wrap.setAttribute("translate", "no");
    wrap.innerHTML = `
      <button type="button" class="lang-btn" aria-haspopup="listbox" aria-expanded="false">
        ${globe}<span class="lang-current"></span>${chevron}
      </button>
      <ul class="lang-menu" role="listbox" hidden>
        ${Object.entries(LANGS).map(([code, l]) => `
          <li role="option" data-lang="${code}" tabindex="-1" lang="${code}" dir="${l.dir || "ltr"}">
            <span>${l.name}</span>${check}
          </li>`).join("")}
      </ul>`;
    const btn = wrap.querySelector(".lang-btn"), menu = wrap.querySelector(".lang-menu");
    const items = [...menu.querySelectorAll("[role=option]")];
    const close = (focus) => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); if (focus) btn.focus(); };
    const open = () => {
      menu.hidden = false; btn.setAttribute("aria-expanded", "true");
      (items.find(i => i.dataset.lang === lang) || items[0]).focus();
    };
    btn.addEventListener("click", () => (menu.hidden ? open() : close()));
    items.forEach(i => i.addEventListener("click", () => { close(true); setLang(i.dataset.lang); }));
    menu.addEventListener("keydown", e => {
      const i = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); document.activeElement.click(); }
      else if (e.key === "Escape" || e.key === "Tab") close(e.key === "Escape");
    });
    document.addEventListener("click", e => { if (!wrap.contains(e.target)) close(); });
    return wrap;
  }

  function syncSwitchers() {
    document.querySelectorAll(".lang-switch").forEach(w => {
      w.querySelector(".lang-current").textContent = LANGS[lang].name;
      w.querySelector(".lang-btn").setAttribute("aria-label", `Language: ${LANGS[lang].name}`);
      w.querySelectorAll("[role=option]").forEach(i => i.setAttribute("aria-selected", String(i.dataset.lang === lang)));
    });
  }

  function mountSwitcher() {
    const nav = document.querySelector(".navbar");
    if (!nav || nav.querySelector(".lang-switch")) return;
    const before = nav.querySelector(".nav-cta") || nav.querySelector(".nav-toggle");
    nav.insertBefore(buildSwitcher(), before);
    syncSwitchers();
  }

  window.i18n = {
    t, setLang, mountSwitcher, languages: LANGS,
    get lang() { return lang; },
    get locale() { return LANGS[lang].locale; },
  };

  async function start() {
    dict = await load(lang);
    compile(dict);
    mountSwitcher();
    firstPass = true;
    applyAll();
    firstPass = false;
    root.classList.remove("i18n-loading");
    observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  setTimeout(() => root.classList.remove("i18n-loading"), 2500); // never leave the page blank
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
