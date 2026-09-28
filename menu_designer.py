# -*- coding: utf-8 -*-
"""
Menu Designer module
--------------------
- Template based, live-editable menu design (admin only)
- PDF export (WeasyPrint)
- Publish: public read-only page at /menu (no login)
- QR code for the public menu page

Wired into app.py with:
    from menu_designer import create_blueprint
    app.register_blueprint(create_blueprint(get_db, admin_required))
"""

import base64
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "static", "fonts")

# ---------------------------------------------------------------------------
# Templates (themes) and options
# ---------------------------------------------------------------------------

THEMES = {
    "classic": dict(
        label="Classic Cream",
        bg="radial-gradient(circle at 20% 10%, #FBF4E4 0%, #F1E5CC 60%, #E8D9B8 100%)",
        text="#3B2F22", title="#2F4A34", cat="#7A5A2B", price="#7A5A2B",
        leader="#B9A582", font="classic", align="center"),
    "midnight": dict(
        label="Midnight Gold",
        bg="linear-gradient(160deg, #121212 0%, #1E1B16 60%, #0E0E0E 100%)",
        text="#EDE3CC", title="#D9A441", cat="#D9A441", price="#F1D189",
        leader="#5B5138", font="classic", align="center"),
    "kraft": dict(
        label="Rustic Kraft",
        bg="repeating-linear-gradient(0deg, rgba(0,0,0,0.035) 0 2px, transparent 2px 5px), linear-gradient(#CBA97F, #B98F62)",
        text="#2B1D10", title="#3A2410", cat="#5A3A1A", price="#3A2410",
        leader="#8B6B45", font="serif", align="left"),
    "fresh": dict(
        label="Fresh Green",
        bg="linear-gradient(180deg, #F3F8EE 0%, #DDEBD3 100%)",
        text="#22331F", title="#2E6B3A", cat="#2E6B3A", price="#B4541F",
        leader="#9DBB93", font="sans", align="left"),
    "modern": dict(
        label="Modern Mono",
        bg="#FFFFFF",
        text="#1A1A1A", title="#111111", cat="#E4572E", price="#111111",
        leader="#C9C9C9", font="sans", align="left"),
    "burgundy": dict(
        label="Burgundy",
        bg="linear-gradient(160deg, #5B1A22 0%, #3E1017 100%)",
        text="#F3E6D3", title="#E7C27D", cat="#E7C27D", price="#F3E6D3",
        leader="#8A5A58", font="serif", align="center"),
}

FONTS = {
    "classic": "'Palatino Linotype', Palatino, 'Book Antiqua', Georgia, 'DejaVu Serif', serif",
    "serif": "Georgia, 'Times New Roman', 'DejaVu Serif', serif",
    "sans": "'Segoe UI', Roboto, Helvetica, Arial, 'DejaVu Sans', sans-serif",
}
FONT_LABELS = {"classic": "Classic serif", "serif": "Serif", "sans": "Sans-serif"}

COLOR_KEYS = ("bg_color", "text", "title", "cat", "price", "leader", "overlay_color")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

MAX_UPLOAD = 8 * 1024 * 1024


def default_settings(theme="classic"):
    t = THEMES[theme]
    return {
        "theme": theme,
        "title": "Menu", "subtitle": "", "footer": "", "currency": "₺",
        "bg_mode": "theme", "bg_color": "#FFFFFF", "bg_image": 0,
        "overlay": 35, "overlay_color": "#000000",
        "text": t["text"], "title_color": t["title"], "cat": t["cat"],
        "price": t["price"], "leader": t["leader"],
        "font": t["font"], "align": t["align"],
        "title_size": 46, "cat_size": 26, "item_size": 17, "columns": 1,
    }


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def clean_settings(raw):
    """Validate everything: values end up inside CSS, so whitelist strictly."""
    raw = raw if isinstance(raw, dict) else {}
    theme = raw.get("theme") if raw.get("theme") in THEMES else "classic"
    s = default_settings(theme)

    for k in ("bg_color", "text", "cat", "price", "leader", "overlay_color"):
        if HEX.match(str(raw.get(k, ""))):
            s[k] = raw[k]
    if HEX.match(str(raw.get("title_color", ""))):
        s["title_color"] = raw["title_color"]

    for k, n in (("title", 80), ("subtitle", 120), ("footer", 200), ("currency", 4)):
        if k in raw:
            s[k] = str(raw.get(k) or "").replace("\n", " ").strip()[:n]

    if raw.get("font") in FONTS:
        s["font"] = raw["font"]
    if raw.get("align") in ("left", "center"):
        s["align"] = raw["align"]
    if raw.get("bg_mode") in ("theme", "color", "image"):
        s["bg_mode"] = raw["bg_mode"]

    s["title_size"] = _clamp(raw.get("title_size"), 24, 90, s["title_size"])
    s["cat_size"] = _clamp(raw.get("cat_size"), 16, 48, s["cat_size"])
    s["item_size"] = _clamp(raw.get("item_size"), 12, 30, s["item_size"])
    s["columns"] = _clamp(raw.get("columns"), 1, 2, 1)
    s["overlay"] = _clamp(raw.get("overlay"), 0, 90, s["overlay"])
    s["bg_image"] = _clamp(raw.get("bg_image"), 0, 10**9, 0)
    if s["bg_mode"] == "image" and not s["bg_image"]:
        s["bg_mode"] = "theme"
    return s


def bg_css(s, img_url=None):
    if s["bg_mode"] == "image" and img_url:
        h = s["overlay_color"].lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = s["overlay"] / 100
        return (f"linear-gradient(rgba({r},{g},{b},{a}),rgba({r},{g},{b},{a})), "
                f"url({img_url}) center / cover no-repeat")
    if s["bg_mode"] == "color":
        return s["bg_color"]
    return THEMES[s["theme"]]["bg"]


def sheet_style(s, bg):
    return (
        f"--m-bg:{bg};--m-text:{s['text']};--m-title:{s['title_color']};"
        f"--m-cat:{s['cat']};--m-price:{s['price']};--m-leader:{s['leader']};"
        f"--m-font:{FONTS[s['font']]};--m-title-size:{s['title_size']}px;"
        f"--m-cat-size:{s['cat_size']}px;--m-item-size:{s['item_size']}px;"
        f"--m-align:{s['align']};--m-cols:{s['columns']};"
    )


def fmt_price(p):
    p = float(p)
    return str(int(p)) if p == int(p) else f"{p:.2f}".replace(".", ",")


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

def create_blueprint(get_db, admin_required):
    bp = Blueprint("menu_designer", __name__)
    state = {"ready": False}

    def db():
        d = get_db()
        if not state["ready"]:
            d.executescript(
                """
                CREATE TABLE IF NOT EXISTS menu_design (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    draft_json TEXT NOT NULL,
                    published_json TEXT,
                    published_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS menu_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mime TEXT NOT NULL,
                    data BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            d.commit()
            state["ready"] = True
        return d

    def get_design(d):
        row = d.execute("SELECT * FROM menu_design WHERE id=1").fetchone()
        if not row:
            d.execute(
                "INSERT INTO menu_design (id, draft_json, updated_at) VALUES (1,?,?)",
                (json.dumps(default_settings()), datetime.now().isoformat()),
            )
            d.commit()
            row = d.execute("SELECT * FROM menu_design WHERE id=1").fetchone()
        return row

    def live_menu(d):
        """Active items only; empty categories are left out."""
        cats = d.execute("SELECT id, name FROM categories ORDER BY sort_order, name").fetchall()
        items = d.execute(
            "SELECT id, category_id, name, price FROM menu_items WHERE active=1 ORDER BY id"
        ).fetchall()
        by_cat = {}
        for it in items:
            by_cat.setdefault(it["category_id"], []).append(
                {"id": it["id"], "name": it["name"], "price": it["price"],
                 "price_txt": fmt_price(it["price"])}
            )
        return [{"id": c["id"], "name": c["name"], "items": by_cat[c["id"]]}
                for c in cats if c["id"] in by_cat]

    def cleanup_images(d, keep):
        keep = {int(k) for k in keep if k}
        for r in d.execute("SELECT id FROM menu_images").fetchall():
            if r["id"] not in keep:
                d.execute("DELETE FROM menu_images WHERE id=?", (r["id"],))
        d.commit()

    def image_data_uri(d, img_id):
        r = d.execute("SELECT mime, data FROM menu_images WHERE id=?", (img_id,)).fetchone()
        if not r:
            return None
        return f"data:{r['mime']};base64," + base64.b64encode(r["data"]).decode()

    def image_exists(d, img_id):
        return bool(img_id) and d.execute(
            "SELECT 1 FROM menu_images WHERE id=?", (img_id,)).fetchone() is not None

    # ------------------------------------------------------------- public

    @bp.route("/menu")
    def public_menu():
        d = db()
        row = get_design(d)
        resp_kwargs = {"snap": None}
        if row["published_json"]:
            snap = json.loads(row["published_json"])
            s = clean_settings(snap.get("settings"))
            img = f"/menu/img/{s['bg_image']}" if s["bg_mode"] == "image" else None
            resp_kwargs = {
                "snap": True, "s": s, "categories": snap.get("categories", []),
                "style": sheet_style(s, bg_css(s, img)),
            }
        html = render_template("menu_public.html", edit=False, **resp_kwargs)
        resp = Response(html)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @bp.route("/menu/img/<int:img_id>")
    def menu_image(img_id):
        r = db().execute("SELECT mime, data FROM menu_images WHERE id=?", (img_id,)).fetchone()
        if not r:
            return Response("Not found", status=404)
        resp = Response(r["data"], mimetype=r["mime"])
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    # ------------------------------------------------------------- editor

    @bp.route("/admin/menu-design")
    @admin_required
    def menu_designer_page():
        d = db()
        row = get_design(d)
        s = clean_settings(json.loads(row["draft_json"]))
        img = f"/menu/img/{s['bg_image']}" if s["bg_image"] else None
        return render_template(
            "menu_designer.html",
            s=s, edit=True, categories=live_menu(d),
            style=sheet_style(s, bg_css(s, img)),
            themes=THEMES, fonts=FONT_LABELS,
            published_at=row["published_at"],
            default_addr=request.host_url.rstrip("/"),
        )

    @bp.route("/admin/menu-design/api/draft", methods=["POST"])
    @admin_required
    def api_save_draft():
        d = db()
        get_design(d)
        s = clean_settings(request.get_json(silent=True))
        d.execute("UPDATE menu_design SET draft_json=?, updated_at=? WHERE id=1",
                  (json.dumps(s), datetime.now().isoformat()))
        d.commit()
        return jsonify(ok=True)

    @bp.route("/admin/menu-design/api/bg", methods=["POST"])
    @admin_required
    def api_upload_bg():
        f = request.files.get("file")
        if not f:
            return jsonify(ok=False, error="No file."), 400
        data = f.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return jsonify(ok=False, error="File is larger than 8 MB."), 413
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.load()
            img = img.convert("RGB")
            img.thumbnail((1600, 2263))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82, optimize=True)
        except Exception:
            return jsonify(ok=False, error="Not a valid image (use JPG, PNG or WebP)."), 400

        d = db()
        cur = d.execute("INSERT INTO menu_images (mime, data, created_at) VALUES (?,?,?)",
                        ("image/jpeg", out.getvalue(), datetime.now().isoformat()))
        d.commit()
        new_id = cur.lastrowid

        keep = [new_id]
        row = get_design(d)
        if row["published_json"]:
            keep.append(clean_settings(json.loads(row["published_json"])["settings"])["bg_image"])
        cleanup_images(d, keep)
        return jsonify(ok=True, id=new_id)

    @bp.route("/admin/menu-design/api/category/<int:cat_id>", methods=["POST"])
    @admin_required
    def api_rename_category(cat_id):
        name = str((request.get_json(silent=True) or {}).get("name", "")).strip()[:80]
        if not name:
            return jsonify(ok=False, error="Name cannot be empty."), 400
        d = db()
        d.execute("UPDATE categories SET name=? WHERE id=?", (name, cat_id))
        d.commit()
        return jsonify(ok=True, name=name)

    @bp.route("/admin/menu-design/api/category/<int:cat_id>/move", methods=["POST"])
    @admin_required
    def api_move_category(cat_id):
        step = -1 if (request.get_json(silent=True) or {}).get("dir") == -1 else 1
        d = db()
        ids = [r["id"] for r in d.execute(
            "SELECT id FROM categories ORDER BY sort_order, name").fetchall()]
        if cat_id in ids:
            i = ids.index(cat_id)
            j = i + step
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                for pos, cid in enumerate(ids):
                    d.execute("UPDATE categories SET sort_order=? WHERE id=?", (pos, cid))
                d.commit()
        return jsonify(ok=True)

    @bp.route("/admin/menu-design/api/item/<int:item_id>", methods=["POST"])
    @admin_required
    def api_update_item(item_id):
        data = request.get_json(silent=True) or {}
        d = db()
        row = d.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return jsonify(ok=False, error="Item not found."), 404
        name, price = row["name"], row["price"]
        if "name" in data:
            name = str(data["name"]).strip()[:120]
            if not name:
                return jsonify(ok=False, error="Name cannot be empty."), 400
        if "price" in data:
            try:
                price = float(str(data["price"]).replace(",", ".").strip())
                if price < 0:
                    raise ValueError
            except ValueError:
                return jsonify(ok=False, error="Invalid price."), 400
        d.execute("UPDATE menu_items SET name=?, price=? WHERE id=?", (name, price, item_id))
        d.commit()
        return jsonify(ok=True, name=name, price_txt=fmt_price(price))

    # ------------------------------------------------------------ publish

    @bp.route("/admin/menu-design/publish", methods=["POST"])
    @admin_required
    def api_publish():
        d = db()
        row = get_design(d)
        s = clean_settings(json.loads(row["draft_json"]))
        if s["bg_mode"] == "image" and not image_exists(d, s["bg_image"]):
            s["bg_mode"] = "theme"
        now = datetime.now().isoformat(timespec="seconds")
        snap = {"settings": s, "categories": live_menu(d)}
        d.execute("UPDATE menu_design SET published_json=?, published_at=? WHERE id=1",
                  (json.dumps(snap), now))
        d.commit()
        keep = [s["bg_image"]] + [
            clean_settings(json.loads(row["draft_json"]))["bg_image"]]
        cleanup_images(d, keep)
        return jsonify(ok=True, published_at=now)

    @bp.route("/admin/menu-design/unpublish", methods=["POST"])
    @admin_required
    def api_unpublish():
        d = db()
        get_design(d)
        d.execute("UPDATE menu_design SET published_json=NULL, published_at=NULL WHERE id=1")
        d.commit()
        return jsonify(ok=True)

    # ---------------------------------------------------------------- PDF

    @bp.route("/admin/menu-design/pdf")
    @admin_required
    def export_pdf():
        try:
            from weasyprint import HTML
        except Exception as e:  # missing package or missing system libs
            return Response(
                "PDF engine (WeasyPrint) is not available on this server: "
                f"{e}\nSee README.md > 'PDF engine setup'.",
                status=500, mimetype="text/plain")

        d = db()
        row = get_design(d)
        s = clean_settings(json.loads(row["draft_json"]))
        img = image_data_uri(d, s["bg_image"]) if s["bg_mode"] == "image" else None
        bg = bg_css(s, img)

        css = Path(BASE_DIR, "static", "menu.css").read_text(encoding="utf-8")
        css = css.replace("/static/fonts/", Path(FONT_DIR).as_uri() + "/")

        html = render_template(
            "menu_pdf.html", s=s, edit=False, categories=live_menu(d),
            style=sheet_style(s, "none"), page_bg=bg, css=css,
        )
        pdf = HTML(string=html, base_url=BASE_DIR).write_pdf()
        filename = f"menu_{datetime.now().strftime('%Y%m%d')}.pdf"
        return Response(pdf, mimetype="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    # ----------------------------------------------------------------- QR

    def normalize_addr(addr):
        addr = (addr or "").strip().rstrip("/")
        if not addr:
            return ""
        if not re.match(r"^https?://", addr, re.I):
            host = addr.split("/")[0]
            local = (":" in host or host.startswith("localhost")
                     or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host))
            addr = ("http://" if local else "https://") + addr
        return addr + "/menu"

    @bp.route("/admin/menu-design/qr.png")
    @admin_required
    def qr_png():
        url = normalize_addr(request.args.get("addr"))
        if not url or len(url) > 300:
            return Response("Enter a valid address.", status=400)
        try:
            import qrcode
        except Exception:
            return Response("qrcode package is not installed.", status=500)
        img = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=12, border=4)
        img.add_data(url)
        img.make(fit=True)
        buf = io.BytesIO()
        img.make_image(fill_color="black", back_color="white").save(buf, "PNG")
        headers = {"X-QR-URL": url}
        if request.args.get("download"):
            headers["Content-Disposition"] = "attachment; filename=menu_qr.png"
        return Response(buf.getvalue(), mimetype="image/png", headers=headers)

    return bp
