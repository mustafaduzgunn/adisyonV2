(function () {
  const M = window.MD, S = M.settings, U = M.urls;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const sheet = $("#menuSheet");
  const FONTS = {
    classic: "'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, 'DejaVu Serif', serif",
    serif: "Georgia, 'Times New Roman', 'DejaVu Serif', serif",
    sans: "'Segoe UI', Roboto, Helvetica, Arial, 'DejaVu Sans', sans-serif"
  };

  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.style.display = "block";
    setTimeout(() => (t.style.display = "none"), 3000);
  }
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || "Request failed");
    return j;
  }

  // ---- live style ----
  function bgCss() {
    if (S.bg_mode === "image" && S.bg_image) {
      const h = S.overlay_color.slice(1), a = S.overlay / 100;
      const rgba = `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
      return `linear-gradient(${rgba},${rgba}), url(/menu/img/${S.bg_image}) center / cover no-repeat`;
    }
    if (S.bg_mode === "color") return S.bg_color;
    return M.themes[S.theme].bg;
  }
  function applyStyle() {
    const v = {
      "--m-bg": bgCss(), "--m-text": S.text, "--m-title": S.title_color, "--m-cat": S.cat,
      "--m-price": S.price, "--m-leader": S.leader, "--m-font": FONTS[S.font],
      "--m-title-size": S.title_size + "px", "--m-cat-size": S.cat_size + "px",
      "--m-item-size": S.item_size + "px", "--m-align": S.align, "--m-cols": S.columns
    };
    for (const k in v) sheet.style.setProperty(k, v[k]);
    sheet.dataset.theme = S.theme;
    $$(".mi-cur").forEach(e => (e.innerHTML = "&nbsp;" + S.currency.replace(/[<>&]/g, "")));
    if (!S.currency) $$(".mi-cur").forEach(e => (e.textContent = ""));
  }

  // ---- sync controls ----
  function syncControls() {
    $$("[data-key]").forEach(el => { el.value = S[el.dataset.key]; });
    $("#ovVal").textContent = S.overlay;
    $$(".md-theme").forEach(e => e.classList.toggle("active", e.dataset.theme === S.theme));
  }

  // ---- autosave ----
  let timer = null, dirty = false;
  function setSaveStatus(t) { $("#saveStatus").textContent = t; }
  function scheduleSave() { dirty = true; setSaveStatus("Saving…"); clearTimeout(timer); timer = setTimeout(save, 500); }
  async function save() {
    clearTimeout(timer);
    if (!dirty) return;
    dirty = false;
    try { await post(U.draft, S); setSaveStatus("All changes saved."); }
    catch (e) { dirty = true; setSaveStatus("Save failed."); toast(e.message); }
  }
  function changed() { applyStyle(); scheduleSave(); }

  $$("[data-key]").forEach(el => {
    const ev = el.tagName === "SELECT" || el.type === "color" || el.type === "range" ? "input" : "input";
    el.addEventListener(ev, () => {
      const k = el.dataset.key;
      S[k] = (["columns", "overlay", "title_size", "cat_size", "item_size"].includes(k)) ? parseInt(el.value, 10) : el.value;
      if (k === "overlay") $("#ovVal").textContent = S.overlay;
      if (k === "title" || k === "subtitle" || k === "footer") syncInline(k);
      if (k === "bg_color") S.bg_mode = "color", syncControls();
      changed();
    });
  });

  // ---- templates ----
  $$(".md-theme").forEach(el => el.addEventListener("click", () => {
    const t = M.themes[el.dataset.theme];
    Object.assign(S, { theme: el.dataset.theme, bg_mode: "theme", text: t.text, title_color: t.title,
      cat: t.cat, price: t.price, leader: t.leader, font: t.font, align: t.align });
    syncControls(); changed();
  }));

  // ---- background upload ----
  $("#bgFile").addEventListener("change", async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await fetch(U.bg, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || "Upload failed");
      S.bg_image = j.id; S.bg_mode = "image"; syncControls(); changed();
    } catch (err) { toast(err.message); }
    e.target.value = "";
  });

  // ---- inline text editing ----
  const inlineMap = { title: "#mTitle", subtitle: "#mSubtitle", footer: "#mFooter" };
  function syncInline(k) { const el = $(inlineMap[k]); if (el && el.textContent !== S[k]) el.textContent = S[k]; }
  Object.keys(inlineMap).forEach(k => {
    const el = $(inlineMap[k]); if (!el) return;
    el.addEventListener("input", () => {
      S[k] = el.textContent.replace(/\n/g, " ").trim();
      const inp = $(`[data-key=${k}]`); if (inp) inp.value = S[k];
      scheduleSave();
    });
  });

  sheet.addEventListener("keydown", e => {
    if (e.key === "Enter" && e.target.isContentEditable) { e.preventDefault(); e.target.blur(); }
  });
  sheet.addEventListener("paste", e => {
    if (!e.target.isContentEditable) return;
    e.preventDefault();
    const t = (e.clipboardData || window.clipboardData).getData("text").replace(/\s+/g, " ");
    document.execCommand("insertText", false, t);
  });

  // category / item edits go straight to the database
  const original = new WeakMap();
  sheet.addEventListener("focusin", e => { if (e.target.isContentEditable) original.set(e.target, e.target.textContent.trim()); });
  sheet.addEventListener("focusout", async e => {
    const el = e.target;
    if (!el.isContentEditable || !(el.dataset.cat || el.dataset.item)) return;
    const before = original.get(el), now = el.textContent.trim();
    if (before === now) return;
    try {
      if (el.dataset.cat) {
        const j = await post(U.category + el.dataset.cat, { name: now });
        el.textContent = j.name;
      } else {
        const body = {}; body[el.dataset.field] = now;
        const j = await post(U.item + el.dataset.item, body);
        el.textContent = el.dataset.field === "price" ? j.price_txt : j.name;
      }
    } catch (err) { el.textContent = before; toast(err.message); }
  });

  $$(".cat-move").forEach(b => b.addEventListener("click", async () => {
    try {
      await save();
      await post(U.category + b.dataset.cat + "/move", { dir: parseInt(b.dataset.dir, 10) });
      location.reload();
    } catch (err) { toast(err.message); }
  }));

  // ---- publish / PDF ----
  function pubStatus() {
    $("#pubStatus").textContent = M.publishedAt
      ? "Published: " + M.publishedAt.replace("T", " ") : "Not published yet.";
  }
  $("#btnPublish").addEventListener("click", async () => {
    try { await save(); const j = await post(U.publish); M.publishedAt = j.published_at; pubStatus(); toast2("Published."); }
    catch (e) { toast(e.message); }
  });
  $("#btnUnpublish").addEventListener("click", async () => {
    if (!confirm("Take the public menu offline?")) return;
    try { await post(U.unpublish); M.publishedAt = null; pubStatus(); }
    catch (e) { toast(e.message); }
  });
  $("#btnPdf").addEventListener("click", async () => { await save(); location.href = U.pdf; });
  function toast2(m) { const t = $("#toast"); t.style.background = "#4C8C6B"; toast(m); setTimeout(() => (t.style.background = ""), 3100); }

  // ---- QR ----
  $("#btnQr").addEventListener("click", async () => {
    const addr = $("#qrAddr").value.trim();
    if (!addr) return toast("Enter the program address.");
    const url = U.qr + "?addr=" + encodeURIComponent(addr);
    const r = await fetch(url);
    if (!r.ok) return toast(await r.text());
    $("#qrUrl").textContent = "Encodes: " + r.headers.get("X-QR-URL");
    $("#pubLink").textContent = r.headers.get("X-QR-URL");
    const img = $("#qrImg"); img.src = url + "&t=" + Date.now(); img.style.display = "block";
    const a = $("#qrDownload"); a.href = url + "&download=1"; a.style.display = "inline-block";
  });

  syncControls(); applyStyle(); pubStatus();
})();
