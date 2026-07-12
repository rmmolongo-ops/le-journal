import os
import uuid
import re
from functools import wraps
from pathlib import Path

import markdown
from dotenv import load_dotenv
from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from supabase import create_client

load_dotenv()

BASE_DIR = Path(__file__).parent

app = Flask(__name__,
            template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# Lazy Supabase client — évite les erreurs à l'import si les vars sont absentes
_sb = None

def get_sb():
    global _sb
    if _sb is None:
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _sb


# ── helpers ──────────────────────────────────────────────────────────────────

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
    sb = get_sb()
    sb.storage.from_("images").upload(
        path=filename,
        file=data,
        file_options={"content-type": file.content_type or "image/jpeg"}
    )
    return sb.storage.from_("images").get_public_url(filename)


# ── public routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sb = get_sb()
    res = sb.table("articles").select("*").eq("published", True)\
            .order("created_at", desc=True).execute()
    articles = [render_article(a) for a in (res.data or [])]
    featured = articles[0] if articles else None
    rest = articles[1:]
    return render_template("index.html", featured=featured, articles=rest)


@app.route("/article/<slug>")
def article(slug):
    sb = get_sb()
    res = sb.table("articles").select("*").eq("slug", slug).execute()
    if not res.data:
        abort(404)
    post = render_article(res.data[0])
    if not post.get("published") and not session.get("admin"):
        abort(404)
    return render_template("article.html", post=post)


@app.route("/categorie/<category>")
def category(category):
    sb = get_sb()
    res = sb.table("articles").select("*").eq("published", True)\
            .ilike("category", category).order("created_at", desc=True).execute()
    articles = [render_article(a) for a in (res.data or [])]
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
    sb = get_sb()
    res = sb.table("articles").select("id,slug,title,author,category,published,created_at")\
            .order("created_at", desc=True).execute()
    articles = res.data or []
    return render_template("admin/dashboard.html", articles=articles)


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
    sb = get_sb()
    res = sb.table("articles").select("*").eq("slug", slug).execute()
    if not res.data:
        abort(404)
    return render_template("admin/editor.html", post=res.data[0], slug=slug)


@app.route("/admin/delete/<slug>", methods=["POST"])
@require_admin
def admin_delete(slug):
    get_sb().table("articles").delete().eq("slug", slug).execute()
    flash("Article supprimé.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/toggle/<slug>", methods=["POST"])
@require_admin
def admin_toggle(slug):
    sb = get_sb()
    res = sb.table("articles").select("published").eq("slug", slug).execute()
    if not res.data:
        abort(404)
    new_status = not res.data[0]["published"]
    sb.table("articles").update({"published": new_status}).eq("slug", slug).execute()
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
    sb = get_sb()

    payload = {
        "slug": slug, "title": title, "content": content,
        "author": author, "category": category,
        "thumbnail_url": thumbnail_url, "published": published,
    }

    if existing_slug:
        sb.table("articles").update(payload).eq("slug", slug).execute()
    else:
        sb.table("articles").insert(payload).execute()

    flash("Article sauvegardé.", "success")
    return redirect(url_for("admin_dashboard"))


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
