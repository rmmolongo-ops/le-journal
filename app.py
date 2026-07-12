import os
import uuid
import re
from functools import wraps
from pathlib import Path

import httpx
import markdown
from dotenv import load_dotenv
from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)

load_dotenv()

BASE_DIR = Path(__file__).parent

app = Flask(__name__,
            template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# ── Supabase REST helpers ──────────────────────────────────────────────────────

def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def _rest(path):
    return f"{SUPABASE_URL}/rest/v1/{path}"

def _storage(path):
    return f"{SUPABASE_URL}/storage/v1/{path}"


def db_select(table, filters=None, columns="*", order=None):
    params = {"select": columns}
    if filters:
        params.update(filters)
    if order:
        params["order"] = order
    r = httpx.get(_rest(table), headers=_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def db_insert(table, data):
    r = httpx.post(_rest(table), headers=_headers(), json=data, timeout=10)
    r.raise_for_status()
    return r.json()


def db_update(table, data, filters):
    params = {}
    params.update(filters)
    r = httpx.patch(_rest(table), headers={**_headers(), "Prefer": "return=representation"},
                    json=data, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def db_delete(table, filters):
    r = httpx.delete(_rest(table), headers=_headers(), params=filters, timeout=10)
    r.raise_for_status()


def storage_upload(bucket, filename, data, content_type):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
    }
    r = httpx.post(_storage(f"object/{bucket}/{filename}"),
                   headers=headers, content=data, timeout=30)
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{filename}"


# ── app helpers ───────────────────────────────────────────────────────────────

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def slugify(text):
    text = text.lower().strip()
    for src, dst in [("àáâãäå","a"),("èéêë","e"),("ìíîï","i"),("òóôõö","o"),("ùúûü","u"),("ç","c")]:
        for ch in src:
            text = text.replace(ch, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or uuid.uuid4().hex[:8]


def render_article(post):
    post["html"] = markdown.markdown(post.get("content", ""), extensions=["extra", "nl2br"])
    post["excerpt"] = re.sub(r"<[^>]+>", "", post["html"])[:200].strip() + "…"
    return post


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def upload_image(file):
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    data = file.read()
    return storage_upload("images", filename, data, file.content_type or "image/jpeg")


# ── public routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    rows = db_select("articles", {"published": "eq.true"}, order="created_at.desc")
    articles = [render_article(a) for a in rows]
    return render_template("index.html",
                           featured=articles[0] if articles else None,
                           articles=articles[1:])


@app.route("/article/<slug>")
def article(slug):
    rows = db_select("articles", {"slug": f"eq.{slug}"})
    if not rows:
        abort(404)
    post = render_article(rows[0])
    if not post.get("published") and not session.get("admin"):
        abort(404)
    return render_template("article.html", post=post)


@app.route("/categorie/<category>")
def category(category):
    rows = db_select("articles",
                     {"published": "eq.true", "category": f"ilike.{category}"},
                     order="created_at.desc")
    articles = [render_article(a) for a in rows]
    return render_template("category.html", articles=articles, category=category)


# ── admin routes ──────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Mot de passe incorrect.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@require_admin
def admin_dashboard():
    rows = db_select("articles", columns="id,slug,title,author,category,published,created_at",
                     order="created_at.desc")
    return render_template("admin/dashboard.html", articles=rows)


@app.route("/admin/new", methods=["GET", "POST"])
@require_admin
def admin_new():
    if request.method == "POST":
        return _save_article_form(None)
    return render_template("admin/editor.html", post=None, slug=None)


@app.route("/admin/edit/<slug>", methods=["GET", "POST"])
@require_admin
def admin_edit(slug):
    if request.method == "POST":
        return _save_article_form(slug)
    rows = db_select("articles", {"slug": f"eq.{slug}"})
    if not rows:
        abort(404)
    return render_template("admin/editor.html", post=rows[0], slug=slug)


@app.route("/admin/delete/<slug>", methods=["POST"])
@require_admin
def admin_delete(slug):
    db_delete("articles", {"slug": f"eq.{slug}"})
    flash("Article supprimé.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/toggle/<slug>", methods=["POST"])
@require_admin
def admin_toggle(slug):
    rows = db_select("articles", {"slug": f"eq.{slug}"}, columns="published")
    if not rows:
        abort(404)
    new_status = not rows[0]["published"]
    db_update("articles", {"published": new_status}, {"slug": f"eq.{slug}"})
    flash(f"Article {'publié' if new_status else 'dépublié'}.", "success")
    return redirect(url_for("admin_dashboard"))


def _save_article_form(existing_slug):
    title     = request.form.get("title", "").strip()
    content   = request.form.get("content", "").strip()
    category  = request.form.get("category", "").strip()
    author    = request.form.get("author", "").strip()
    published = "published" in request.form

    if not title:
        flash("Le titre est obligatoire.", "error")
        return redirect(request.url)

    thumbnail_url = request.form.get("existing_thumbnail", "")
    file = request.files.get("thumbnail")
    if file and file.filename and allowed_file(file.filename):
        thumbnail_url = upload_image(file)

    slug = existing_slug or slugify(title)
    payload = {
        "slug": slug, "title": title, "content": content,
        "author": author, "category": category,
        "thumbnail_url": thumbnail_url, "published": published,
    }

    if existing_slug:
        db_update("articles", payload, {"slug": f"eq.{slug}"})
    else:
        db_insert("articles", payload)

    flash("Article sauvegardé.", "success")
    return redirect(url_for("admin_dashboard"))


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
