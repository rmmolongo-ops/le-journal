"""Migre les articles Markdown existants vers Supabase."""
import os, re, uuid
from pathlib import Path
from dotenv import load_dotenv
import frontmatter
from supabase import create_client

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
ARTICLES_DIR = Path(__file__).parent / "articles"

for path in ARTICLES_DIR.glob("*.md"):
    post = frontmatter.load(str(path))
    slug = path.stem
    payload = {
        "slug": slug,
        "title": post.get("title", slug),
        "content": post.content,
        "author": post.get("author", ""),
        "category": post.get("category", ""),
        "thumbnail_url": "",
        "published": bool(post.get("published", False)),
    }
    res = sb.table("articles").upsert(payload, on_conflict="slug").execute()
    print(f"OK: {slug}")

print("Migration terminee.")
