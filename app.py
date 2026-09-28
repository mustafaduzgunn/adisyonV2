# -*- coding: utf-8 -*-
"""
Restoran Adisyon Uygulamasi
----------------------------
Basit, tek adres (single-origin) üzerinde çalışan; yönetici ve garson
rollerine sahip, mobil uyumlu masa siparişi yönetim uygulaması.

Çalıştırma:
    pip install -r requirements.txt
    python app.py

Varsayılan yönetici hesabı: kullanıcı adı "admin", şifre "admin123"
(ilk girişten sonra mutlaka değiştirin).
"""

import csv
import io
import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect, url_for,
    session, jsonify, flash, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

from menu_designer import create_blueprint as create_menu_designer_bp

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "adisyon.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lutfen-bu-anahtari-degistirin")

# Türkçe karakterlerin (ş, ğ, ı, İ ...) PDF çıktısında doğru görünmesi için
# DejaVu Sans gömülür; font dosyaları bulunamazsa sessizce standart fonta düşer.
FONT_DIR = os.path.join(BASE_DIR, "static", "fonts")
PDF_FONT = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"
try:
    pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(FONT_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")))
    PDF_FONT = "DejaVu"
    PDF_FONT_BOLD = "DejaVu-Bold"
except Exception:
    pass


# ---------------------------------------------------------------------------
# Veritabanı yardımcıları
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL;")   # eş zamanlı okuma/yazım için
        g.db.execute("PRAGMA foreign_keys=ON;")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    first_run = not os.path.exists(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys=ON;")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','waiter')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS restaurant_tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id INTEGER NOT NULL REFERENCES restaurant_tables(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            opened_by TEXT,
            closed_by TEXT,
            payment_type TEXT NOT NULL DEFAULT 'cash' CHECK(payment_type IN ('cash','credit')),
            credit_account_id INTEGER REFERENCES credit_accounts(id)
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
            item_name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            note TEXT DEFAULT '',
            added_by TEXT,
            created_at TEXT NOT NULL,
            cancelled INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS credit_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credit_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            credit_account_id INTEGER NOT NULL REFERENCES credit_accounts(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            created_by TEXT
        );
        """
    )
    db.commit()

    if first_run:
        # ilk kurulum: örnek masa, örnek menü ve varsayılan admin
        db.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            ("admin", generate_password_hash("admin123"), "admin", datetime.now().isoformat()),
        )
        for i in range(1, 11):
            db.execute("INSERT INTO restaurant_tables (name) VALUES (?)", (f"Masa {i}",))

        cur = db.execute("INSERT INTO categories (name, sort_order) VALUES (?,?)", ("İçecekler", 1))
        cat_drinks = cur.lastrowid
        cur = db.execute("INSERT INTO categories (name, sort_order) VALUES (?,?)", ("Ana Yemekler", 2))
        cat_mains = cur.lastrowid

        sample_items = [
            (cat_drinks, "Çay", 15),
            (cat_drinks, "Ayran", 25),
            (cat_drinks, "Kola", 40),
            (cat_mains, "Adana Kebap", 220),
            (cat_mains, "Izgara Köfte", 200),
        ]
        for cat_id, name, price in sample_items:
            db.execute(
                "INSERT INTO menu_items (category_id, name, price, active) VALUES (?,?,?,1)",
                (cat_id, name, price),
            )
        db.commit()
    db.close()

    # daha önce kurulmuş bir veritabanı varsa (kazanç/veresiye özelliklerinden
    # önce oluşturulmuş), eksik kolon ve tabloları buradan tamamlar
    migrate_db()


def migrate_db():
    """Eski şemalı veritabanlarına yeni kolon/tabloları ekler.
    Var olan verileri bozmaz; sadece eksik olanları tamamlar."""
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys=OFF;")  # ALTER TABLE sırasında kısıtlama sorunu çıkmasın

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS credit_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credit_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            credit_account_id INTEGER NOT NULL REFERENCES credit_accounts(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            created_by TEXT
        );
        """
    )

    order_cols = [r[1] for r in db.execute("PRAGMA table_info(orders)").fetchall()]
    if "payment_type" not in order_cols:
        db.execute("ALTER TABLE orders ADD COLUMN payment_type TEXT NOT NULL DEFAULT 'cash'")
    if "credit_account_id" not in order_cols:
        db.execute("ALTER TABLE orders ADD COLUMN credit_account_id INTEGER")

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Kimlik doğrulama yardımcıları
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            flash("Bu işlem için yönetici yetkisi gerekiyor.", "error")
            return redirect(url_for("tables_view"))
        return view(*args, **kwargs)
    return wrapped


app.register_blueprint(create_menu_designer_bp(get_db, admin_required))


@app.context_processor
def inject_user():
    return {
        "current_username": session.get("username"),
        "current_role": session.get("role"),
    }


# ---------------------------------------------------------------------------
# Giriş / Çıkış
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session.permanent = True
            next_url = request.args.get("next") or url_for("tables_view")
            return redirect(next_url)
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("tables_view")) if session.get("user_id") else redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Masalar (garson + yönetici ortak ekranı)
# ---------------------------------------------------------------------------

@app.route("/tables")
@login_required
def tables_view():
    db = get_db()
    rows = db.execute(
        """
        SELECT t.id, t.name, o.id AS order_id,
               COALESCE(SUM(oi.unit_price * oi.quantity), 0) AS total
        FROM restaurant_tables t
        LEFT JOIN orders o ON o.table_id = t.id AND o.status = 'open'
        LEFT JOIN order_items oi ON oi.order_id = o.id AND oi.cancelled = 0
        GROUP BY t.id
        ORDER BY t.id
        """
    ).fetchall()
    return render_template("tables.html", tables=rows)


@app.route("/api/tables")
@login_required
def api_tables():
    """Masa grid ekranının otomatik yenilenmesi (polling) için JSON."""
    db = get_db()
    rows = db.execute(
        """
        SELECT t.id, t.name, o.id AS order_id,
               COALESCE(SUM(oi.unit_price * oi.quantity), 0) AS total
        FROM restaurant_tables t
        LEFT JOIN orders o ON o.table_id = t.id AND o.status = 'open'
        LEFT JOIN order_items oi ON oi.order_id = o.id AND oi.cancelled = 0
        GROUP BY t.id
        ORDER BY t.id
        """
    ).fetchall()
    return jsonify([
        {"id": r["id"], "name": r["name"], "occupied": r["order_id"] is not None, "total": r["total"]}
        for r in rows
    ])


@app.route("/table/<int:table_id>")
@login_required
def table_detail(table_id):
    db = get_db()
    table = db.execute("SELECT * FROM restaurant_tables WHERE id=?", (table_id,)).fetchone()
    if not table:
        flash("Masa bulunamadı.", "error")
        return redirect(url_for("tables_view"))
    categories = db.execute("SELECT * FROM categories ORDER BY sort_order, name").fetchall()
    items = db.execute("SELECT * FROM menu_items WHERE active=1 ORDER BY name").fetchall()
    items_by_cat = {}
    for it in items:
        items_by_cat.setdefault(it["category_id"], []).append(it)

    credit_accounts = []
    if session.get("role") == "admin":
        credit_accounts = db.execute("SELECT * FROM credit_accounts ORDER BY name").fetchall()

    return render_template(
        "table_order.html", table=table, categories=categories, items_by_cat=items_by_cat,
        credit_accounts=credit_accounts,
    )


@app.route("/api/table/<int:table_id>/order")
@login_required
def api_table_order(table_id):
    """Bu masanın açık siparişini döner; garsonlar arası eş zamanlı görünüm için."""
    db = get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE table_id=? AND status='open'", (table_id,)
    ).fetchone()
    if not order:
        return jsonify({"open": False, "items": [], "total": 0})
    items = db.execute(
        """SELECT id, item_name, unit_price, quantity, note, added_by
           FROM order_items WHERE order_id=? AND cancelled=0 ORDER BY id""",
        (order["id"],),
    ).fetchall()
    total = sum(i["unit_price"] * i["quantity"] for i in items)
    return jsonify({
        "open": True,
        "order_id": order["id"],
        "opened_by": order["opened_by"],
        "items": [dict(i) for i in items],
        "total": total,
    })


@app.route("/api/table/<int:table_id>/add_item", methods=["POST"])
@login_required
def api_add_item(table_id):
    data = request.get_json(force=True)
    menu_item_id = data.get("menu_item_id")
    quantity = int(data.get("quantity", 1))
    note = (data.get("note") or "").strip()

    if quantity < 1:
        return jsonify({"error": "Adet en az 1 olmalı."}), 400

    db = get_db()
    item = db.execute("SELECT * FROM menu_items WHERE id=? AND active=1", (menu_item_id,)).fetchone()
    if not item:
        return jsonify({"error": "Ürün bulunamadı."}), 404

    order = db.execute(
        "SELECT * FROM orders WHERE table_id=? AND status='open'", (table_id,)
    ).fetchone()
    if not order:
        cur = db.execute(
            "INSERT INTO orders (table_id, status, opened_at, opened_by) VALUES (?,?,?,?)",
            (table_id, "open", datetime.now().isoformat(), session.get("username")),
        )
        order_id = cur.lastrowid
    else:
        order_id = order["id"]

    db.execute(
        """INSERT INTO order_items
           (order_id, menu_item_id, item_name, unit_price, quantity, note, added_by, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (order_id, item["id"], item["name"], item["price"], quantity, note,
         session.get("username"), datetime.now().isoformat()),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/order_item/<int:item_id>/cancel", methods=["POST"])
@login_required
def api_cancel_item(item_id):
    """Yanlışlıkla eklenen bir kalemi iptal eder (satır bazlı, sipariş geçmişi bozulmaz)."""
    db = get_db()
    db.execute("UPDATE order_items SET cancelled=1 WHERE id=?", (item_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/table/<int:table_id>/close", methods=["POST"])
@admin_required
def close_table(table_id):
    db = get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE table_id=? AND status='open'", (table_id,)
    ).fetchone()
    if order:
        db.execute(
            "UPDATE orders SET status='closed', closed_at=?, closed_by=? WHERE id=?",
            (datetime.now().isoformat(), session.get("username"), order["id"]),
        )
        db.commit()
        flash("Masa hesabı kapatıldı.", "success")
    return redirect(url_for("tables_view"))


@app.route("/table/<int:table_id>/close_credit", methods=["POST"])
@admin_required
def close_table_credit(table_id):
    """Masa hesabını nakit yerine bir veresiye hesabına yazarak kapatır."""
    db = get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE table_id=? AND status='open'", (table_id,)
    ).fetchone()
    if not order:
        flash("Bu masada açık hesap yok.", "error")
        return redirect(url_for("table_detail", table_id=table_id))

    account_id = request.form.get("credit_account_id") or None
    new_name = request.form.get("new_account_name", "").strip()
    new_phone = request.form.get("new_account_phone", "").strip()

    if new_name:
        cur = db.execute(
            "INSERT INTO credit_accounts (name, phone, balance, created_at) VALUES (?,?,0,?)",
            (new_name, new_phone, datetime.now().isoformat()),
        )
        account_id = cur.lastrowid

    if not account_id:
        flash("Veresiye için mevcut bir hesap seçin ya da yeni hesap adı girin.", "error")
        return redirect(url_for("table_detail", table_id=table_id))

    total_row = db.execute(
        "SELECT COALESCE(SUM(unit_price*quantity),0) AS t FROM order_items WHERE order_id=? AND cancelled=0",
        (order["id"],),
    ).fetchone()
    total = total_row["t"]

    db.execute(
        """UPDATE orders SET status='closed', closed_at=?, closed_by=?,
                  payment_type='credit', credit_account_id=? WHERE id=?""",
        (datetime.now().isoformat(), session.get("username"), account_id, order["id"]),
    )
    db.execute("UPDATE credit_accounts SET balance = balance + ? WHERE id=?", (total, account_id))
    db.commit()
    flash("Masa hesabı veresiyeye yazıldı.", "success")
    return redirect(url_for("tables_view"))


# ---------------------------------------------------------------------------
# Yönetici: Kazanç durumu
# ---------------------------------------------------------------------------

@app.route("/admin/earnings")
@admin_required
def admin_earnings():
    db = get_db()
    rows = db.execute(
        """
        SELECT
          substr(o.closed_at,1,10) AS day,
          SUM(CASE WHEN o.payment_type='credit' THEN 0 ELSE ot.total END) AS cash_total,
          SUM(CASE WHEN o.payment_type='credit' THEN ot.total ELSE 0 END) AS credit_total,
          SUM(ot.total) AS day_total
        FROM orders o
        JOIN (
          SELECT order_id, COALESCE(SUM(unit_price*quantity),0) AS total
          FROM order_items WHERE cancelled=0 GROUP BY order_id
        ) ot ON ot.order_id = o.id
        WHERE o.status='closed'
        GROUP BY day
        ORDER BY day DESC
        LIMIT 30
        """
    ).fetchall()
    daily = [dict(r) for r in rows]

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    month_start = now.strftime("%Y-%m-01")

    today_total = sum(d["day_total"] for d in daily if d["day"] == today_str)
    week_total = sum(d["day_total"] for d in daily if d["day"] >= week_start)
    month_total = sum(d["day_total"] for d in daily if d["day"] >= month_start)

    chart_days = list(reversed(daily))  # grafik için eskiden yeniye sırala
    max_total = max([d["day_total"] for d in chart_days], default=0) or 1

    return render_template(
        "admin_earnings.html",
        today_total=today_total, week_total=week_total, month_total=month_total,
        chart_days=chart_days, max_total=max_total,
    )


# ---------------------------------------------------------------------------
# Yönetici: Veresiye hesapları
# ---------------------------------------------------------------------------

@app.route("/admin/credit-accounts")
@admin_required
def admin_credit_accounts():
    db = get_db()
    accounts = db.execute(
        "SELECT * FROM credit_accounts ORDER BY balance DESC, name"
    ).fetchall()
    total_debt = sum(a["balance"] for a in accounts)
    return render_template("admin_credit_accounts.html", accounts=accounts, total_debt=total_debt)


@app.route("/admin/credit-accounts/add", methods=["POST"])
@admin_required
def add_credit_account():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    if name:
        db = get_db()
        db.execute(
            "INSERT INTO credit_accounts (name, phone, balance, created_at) VALUES (?,?,0,?)",
            (name, phone, datetime.now().isoformat()),
        )
        db.commit()
        flash("Veresiye hesabı oluşturuldu.", "success")
    return redirect(url_for("admin_credit_accounts"))


@app.route("/admin/credit-accounts/<int:account_id>/payment", methods=["POST"])
@admin_required
def add_credit_payment(account_id):
    amount_raw = request.form.get("amount", "0").replace(",", ".")
    note = request.form.get("note", "").strip()
    try:
        amount = float(amount_raw)
    except ValueError:
        amount = 0
    if amount > 0:
        db = get_db()
        db.execute(
            """INSERT INTO credit_payments (credit_account_id, amount, note, created_at, created_by)
               VALUES (?,?,?,?,?)""",
            (account_id, amount, note, datetime.now().isoformat(), session.get("username")),
        )
        db.execute("UPDATE credit_accounts SET balance = balance - ? WHERE id=?", (amount, account_id))
        db.commit()
        flash("Ödeme kaydedildi.", "success")
    return redirect(url_for("admin_credit_accounts"))


@app.route("/admin/credit-accounts/<int:account_id>/delete", methods=["POST"])
@admin_required
def delete_credit_account(account_id):
    db = get_db()
    row = db.execute("SELECT balance FROM credit_accounts WHERE id=?", (account_id,)).fetchone()
    if row and row["balance"] > 0:
        flash("Bakiyesi olan bir hesap silinemez. Önce ödemeyi alın.", "error")
    else:
        db.execute("DELETE FROM credit_accounts WHERE id=?", (account_id,))
        db.commit()
        flash("Veresiye hesabı silindi.", "success")
    return redirect(url_for("admin_credit_accounts"))


# ---------------------------------------------------------------------------
# Yönetici: Menü dışa aktarma (CSV / PDF)
# ---------------------------------------------------------------------------

def _menu_export_rows(db):
    return db.execute(
        """
        SELECT c.name AS category, m.name AS item, m.price AS price, m.active AS active
        FROM menu_items m
        JOIN categories c ON c.id = m.category_id
        ORDER BY c.sort_order, c.name, m.name
        """
    ).fetchall()


@app.route("/admin/menu/export.csv")
@admin_required
def export_menu_csv():
    db = get_db()
    rows = _menu_export_rows(db)

    output = io.StringIO()
    # Excel (TR) varsayılan olarak ";" ayraçlı CSV bekler, "," değil.
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Kategori", "Ürün", "Fiyat", "Durum"])
    for r in rows:
        writer.writerow([
            r["category"], r["item"], f"{r['price']:.2f}",
            "Aktif" if r["active"] else "Pasif",
        ])

    # Excel'in UTF-8'i doğru tanıması için başa BOM ekleniyor (Türkçe karakterler için).
    csv_data = "\ufeff" + output.getvalue()
    filename = f"menu_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/admin/menu/export.pdf")
@admin_required
def export_menu_pdf():
    db = get_db()
    categories = db.execute("SELECT * FROM categories ORDER BY sort_order, name").fetchall()
    items = db.execute("SELECT * FROM menu_items ORDER BY name").fetchall()
    items_by_cat = {}
    for it in items:
        items_by_cat.setdefault(it["category_id"], []).append(it)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )

    title_style = ParagraphStyle(
        "TitleTR", fontName=PDF_FONT_BOLD, fontSize=18, leading=22, spaceAfter=4,
    )
    date_style = ParagraphStyle(
        "DateTR", fontName=PDF_FONT, fontSize=9, textColor=colors.grey, spaceAfter=14,
    )
    cat_style = ParagraphStyle(
        "CatTR", fontName=PDF_FONT_BOLD, fontSize=13, spaceBefore=14, spaceAfter=6,
    )

    story = [
        Paragraph("Ürün Listesi", title_style),
        Paragraph(datetime.now().strftime("%d.%m.%Y %H:%M"), date_style),
    ]

    any_items = False
    for cat in categories:
        cat_items = items_by_cat.get(cat["id"], [])
        if not cat_items:
            continue
        any_items = True
        story.append(Paragraph(cat["name"], cat_style))

        table_data = [["Ürün", "Fiyat", "Durum"]]
        for it in cat_items:
            table_data.append([
                it["name"], f"{it['price']:.2f} TL",
                "Aktif" if it["active"] else "Pasif",
            ])

        table = Table(table_data, colWidths=[100 * mm, 35 * mm, 30 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_BOLD),
            ("FONTNAME", (0, 1), (-1, -1), PDF_FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#242B25")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F1EA")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CFCABF")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)

    if not any_items:
        story.append(Paragraph("Henüz ürün eklenmemiş.", getSampleStyleSheet()["Normal"]))

    doc.build(story)
    buf.seek(0)
    filename = f"menu_{datetime.now().strftime('%Y%m%d')}.pdf"
    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Yönetici: Menü yönetimi
# ---------------------------------------------------------------------------

@app.route("/admin/menu")
@admin_required
def admin_menu():
    db = get_db()
    categories = db.execute("SELECT * FROM categories ORDER BY sort_order, name").fetchall()
    items = db.execute(
        "SELECT * FROM menu_items ORDER BY category_id, name"
    ).fetchall()
    items_by_cat = {}
    for it in items:
        items_by_cat.setdefault(it["category_id"], []).append(it)
    return render_template("menu_admin.html", categories=categories, items_by_cat=items_by_cat)


@app.route("/admin/category/add", methods=["POST"])
@admin_required
def add_category():
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        max_order = db.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM categories").fetchone()["m"]
        db.execute("INSERT INTO categories (name, sort_order) VALUES (?,?)", (name, max_order + 1))
        db.commit()
        flash("Kategori eklendi.", "success")
    return redirect(url_for("admin_menu"))


@app.route("/admin/category/<int:cat_id>/delete", methods=["POST"])
@admin_required
def delete_category(cat_id):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    db.commit()
    flash("Kategori ve altındaki ürünler silindi.", "success")
    return redirect(url_for("admin_menu"))


@app.route("/admin/item/add", methods=["POST"])
@admin_required
def add_item():
    category_id = request.form.get("category_id")
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "0").replace(",", ".")
    try:
        price_val = float(price)
    except ValueError:
        price_val = 0
    if name and category_id:
        db = get_db()
        db.execute(
            "INSERT INTO menu_items (category_id, name, price, active) VALUES (?,?,?,1)",
            (category_id, name, price_val),
        )
        db.commit()
        flash("Ürün eklendi.", "success")
    return redirect(url_for("admin_menu"))


@app.route("/admin/item/<int:item_id>/update", methods=["POST"])
@admin_required
def update_item(item_id):
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "0").replace(",", ".")
    active = 1 if request.form.get("active") == "on" else 0
    try:
        price_val = float(price)
    except ValueError:
        price_val = 0
    db = get_db()
    db.execute(
        "UPDATE menu_items SET name=?, price=?, active=? WHERE id=?",
        (name, price_val, active, item_id),
    )
    db.commit()
    flash("Ürün güncellendi.", "success")
    return redirect(url_for("admin_menu"))


@app.route("/admin/item/<int:item_id>/delete", methods=["POST"])
@admin_required
def delete_item(item_id):
    db = get_db()
    db.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    db.commit()
    flash("Ürün silindi.", "success")
    return redirect(url_for("admin_menu"))


# ---------------------------------------------------------------------------
# Yönetici: Masa sayısı yönetimi
# ---------------------------------------------------------------------------

@app.route("/admin/tables")
@admin_required
def admin_tables():
    db = get_db()
    tables = db.execute("SELECT * FROM restaurant_tables ORDER BY id").fetchall()
    return render_template("tables_admin.html", tables=tables)


@app.route("/admin/tables/add", methods=["POST"])
@admin_required
def add_table():
    name = request.form.get("name", "").strip()
    db = get_db()
    if not name:
        next_no = db.execute("SELECT COUNT(*) AS c FROM restaurant_tables").fetchone()["c"] + 1
        name = f"Masa {next_no}"
    db.execute("INSERT INTO restaurant_tables (name) VALUES (?)", (name,))
    db.commit()
    flash("Masa eklendi.", "success")
    return redirect(url_for("admin_tables"))


@app.route("/admin/tables/<int:table_id>/rename", methods=["POST"])
@admin_required
def rename_table(table_id):
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("UPDATE restaurant_tables SET name=? WHERE id=?", (name, table_id))
        db.commit()
    return redirect(url_for("admin_tables"))


@app.route("/admin/tables/<int:table_id>/delete", methods=["POST"])
@admin_required
def delete_table(table_id):
    db = get_db()
    open_order = db.execute(
        "SELECT id FROM orders WHERE table_id=? AND status='open'", (table_id,)
    ).fetchone()
    if open_order:
        flash("Açık hesabı olan bir masa silinemez. Önce hesabı kapatın.", "error")
    else:
        db.execute("DELETE FROM restaurant_tables WHERE id=?", (table_id,))
        db.commit()
        flash("Masa silindi.", "success")
    return redirect(url_for("admin_tables"))


# ---------------------------------------------------------------------------
# Yönetici: Garson hesapları
# ---------------------------------------------------------------------------

@app.route("/admin/waiters")
@admin_required
def admin_waiters():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY role DESC, username").fetchall()
    return render_template("waiters_admin.html", users=users)


@app.route("/admin/waiters/add", methods=["POST"])
@admin_required
def add_waiter():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "waiter")
    if role not in ("admin", "waiter"):
        role = "waiter"
    if not username or not password:
        flash("Kullanıcı adı ve şifre zorunludur.", "error")
        return redirect(url_for("admin_waiters"))
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, generate_password_hash(password), role, datetime.now().isoformat()),
        )
        db.commit()
        flash("Hesap oluşturuldu.", "success")
    except sqlite3.IntegrityError:
        flash("Bu kullanıcı adı zaten kullanılıyor.", "error")
    return redirect(url_for("admin_waiters"))


@app.route("/admin/waiters/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_waiter(user_id):
    if user_id == session.get("user_id"):
        flash("Kendi hesabınızı silemezsiniz.", "error")
        return redirect(url_for("admin_waiters"))
    db = get_db()
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    flash("Hesap silindi.", "success")
    return redirect(url_for("admin_waiters"))


@app.route("/admin/waiters/<int:user_id>/password", methods=["POST"])
@admin_required
def change_user_password(user_id):
    """Yönetici, kendi şifresini veya herhangi bir garson/yönetici hesabının
    şifresini buradan değiştirebilir."""
    new_password = request.form.get("password", "")
    if len(new_password) < 4:
        flash("Şifre en az 4 karakter olmalı.", "error")
        return redirect(url_for("admin_waiters"))

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("Kullanıcı bulunamadı.", "error")
        return redirect(url_for("admin_waiters"))

    db.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(new_password), user_id),
    )
    db.commit()
    flash(f"{user['username']} için şifre güncellendi.", "success")
    return redirect(url_for("admin_waiters"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
