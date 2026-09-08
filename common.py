"""
common.py — shared plumbing for all optimization agents.

Nothing in here does any "optimizing" itself. It's the stuff every
agent needs: repo cloning, stack detection, URL->file mapping,
CSV parsing, an OpenAI call wrapper, and safe file writes.
"""
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import openai  # OpenAI 0.28.1 style client, matches your existing script

# ---------------------------
# Environment Configuration
# ---------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("REPO_URL")
BRANCH = os.getenv("BRANCH", "main")
SITE_BASE = os.getenv("SITE_BASE", "")
CSV_PATH = os.getenv("CSV_PATH", "seo_report.csv")
GIT_USERNAME = os.getenv("GIT_USERNAME", "SEO-Auto-Fix-Bot")
GIT_EMAIL = os.getenv("GIT_EMAIL", "seo-bot@example.com")


def require_env():
    if not OPENAI_API_KEY:
        raise SystemExit("❌ OPENAI_API_KEY environment variable is required")
    if not GITHUB_TOKEN or not REPO_URL:
        raise SystemExit("❌ GITHUB_TOKEN and REPO_URL environment variables are required")
    openai.api_key = OPENAI_API_KEY


def authed_repo_url() -> str:
    url = REPO_URL
    if url.startswith("https://") and "@" not in url and GITHUB_TOKEN:
        url = url.replace("https://", f"https://{GITHUB_TOKEN}:x-oauth-basic@")
    return url


# ---------------------------
# Small helpers
# ---------------------------
def get_site_name(site_base: str) -> str:
    try:
        parsed = urlparse(site_base)
        domain = parsed.netloc or parsed.path
        domain = domain.replace("www.", "")
        site_name = domain.split(".")[0]
        return site_name.title() if site_name else "Website"
    except Exception:
        return "Website"


def get_domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path
        return domain.replace("www.", "")
    except Exception:
        return "website.com"


def run(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout)
    if result.stderr.strip():
        print(result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result


def safe_write(file: Path, original: str, updated: Optional[str]):
    if not updated:
        print(f"⚠️  LLM/fix failed, keeping original: {file}")
        return
    if original and len(updated.splitlines()) < len(original.splitlines()) * 0.5:
        print(f"⚠️  Suspiciously short output for {file}, keeping original")
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(updated, encoding="utf-8")
    print(f"✅ File written: {file}")


def ask_openai(prompt: str, system: str = "You are an SEO, GEO, and AEO expert.", max_tokens: int = 800) -> str:
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("OpenAI error:", e)
        return ""


def extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from an LLM response."""
    if not text:
        return None
    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"\s*```", "", cleaned)
    cleaned = re.sub(r",\s*}", "}", cleaned)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    try:
        return json.loads(cleaned)
    except Exception as e:
        print(f"❌ JSON parsing failed: {e}")
        return None


# ---------------------------
# Data structures
# ---------------------------
@dataclass
class Row:
    url: str
    title: str = ""
    meta_description: str = ""
    h1_count: int = 0
    heading_order: str = ""
    missing_alt_tags: int = 0
    total_images: int = 0
    canonical_tag: str = ""
    robots_meta: str = ""
    viewport_present: str = ""
    schema_types: str = ""
    opengraph_tags: int = 0
    twitter_tags: int = 0
    word_count: int = 0
    readability_score: float = 0.0
    grammar_errors: int = 0
    text_to_html_ratio: float = 0.0
    top_keywords: str = ""
    seo_score: int = 0
    seo_suggestions: str = ""


@dataclass
class Context:
    site_base: str
    repo_dir: Path
    stack: str
    url_to_file: Dict[str, Path] = field(default_factory=dict)
    is_app_router: bool = False


# ---------------------------
# CSV parsing
# ---------------------------
def parse_csv(path: str) -> List[Row]:
    rows: List[Row] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                Row(
                    url=r["url"],
                    title=r.get("title", ""),
                    meta_description=r.get("meta_description", ""),
                    h1_count=int(r.get("h1_count", 0) or 0),
                    heading_order=r.get("heading_order", ""),
                    missing_alt_tags=int(r.get("missing_alt_tags", 0) or 0),
                    total_images=int(r.get("total_images", 0) or 0),
                    canonical_tag=r.get("canonical_tag", ""),
                    robots_meta=r.get("robots_meta", ""),
                    viewport_present=r.get("viewport_present", ""),
                    schema_types=r.get("schema_types", ""),
                    opengraph_tags=int(r.get("opengraph_tags", 0) or 0),
                    twitter_tags=int(r.get("twitter_tags", 0) or 0),
                    word_count=int(r.get("word_count", 0) or 0),
                    readability_score=float(r.get("readability_score", 0) or 0.0),
                    grammar_errors=int(r.get("grammar_errors", 0) or 0),
                    text_to_html_ratio=float(r.get("text_to_html_ratio", 0) or 0.0),
                    top_keywords=r.get("top_keywords", ""),
                    seo_score=int(r.get("seo_score", 0) or 0),
                    seo_suggestions=r.get("seo_suggestions", ""),
                )
            )
    return rows


# ---------------------------
# Repo + stack detection
# ---------------------------
def clone_repo(workdir: Path):
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    run(["git", "clone", "-b", BRANCH, authed_repo_url(), str(workdir)])


def detect_stack(repo_dir: Path) -> Tuple[str, bool]:
    if (repo_dir / "package.json").exists():
        content = (repo_dir / "package.json").read_text()
        if "next" in content:
            if (repo_dir / "app").exists() or (repo_dir / "src/app").exists():
                return "next", True
            return "next", False
    if (repo_dir / "_config.yml").exists():
        return "jekyll", False
    return "static", False


def base_dir_for(ctx: Context) -> str:
    if ctx.is_app_router:
        return "src/app" if (ctx.repo_dir / "src/app").exists() else "app"
    return "pages"


def resolve_page_path(base_dir: str, url: str, repo_dir: Path, site_base: str) -> Path:
    site_base_clean = site_base.split("://", 1)[1] if site_base.startswith(("http://", "https://")) else site_base
    url_clean = url.split("://", 1)[1] if url.startswith(("http://", "https://")) else url

    if url_clean.startswith(site_base_clean):
        path = url_clean[len(site_base_clean):].strip("/")
    else:
        path = url_clean.split("/", 1)[1] if "/" in url_clean else ""

    if not path:
        for c in ["page.tsx", "page.js", "page.jsx"]:
            candidate = repo_dir / base_dir / c
            if candidate.exists():
                return candidate
        return repo_dir / base_dir / "page.js"

    page_folder = repo_dir / base_dir / path
    for ext in ["page.tsx", "page.js", "page.jsx", "index.tsx", "index.js", "index.jsx"]:
        candidate = page_folder / ext
        if candidate.exists():
            return candidate
    return page_folder / "page.js"


def map_urls_to_files(rows: List[Row], ctx: Context) -> Dict[str, Path]:
    mapping = {}
    if ctx.stack != "next":
        return mapping
    base_dir = base_dir_for(ctx)
    for row in rows:
        mapping[row.url] = resolve_page_path(base_dir, row.url, ctx.repo_dir, ctx.site_base)
    return mapping


def commit_and_push(ctx: Context, message: str):
    run(["git", "config", "user.name", GIT_USERNAME], cwd=ctx.repo_dir)
    run(["git", "config", "user.email", GIT_EMAIL], cwd=ctx.repo_dir)
    run(["git", "add", "."], cwd=ctx.repo_dir)
    try:
        run(["git", "commit", "-m", message], cwd=ctx.repo_dir)
        print(f"✅ Committed: {message}")
    except subprocess.CalledProcessError:
        print("ℹ️  No changes to commit for this step.")
    run(["git", "push", "origin", BRANCH], cwd=ctx.repo_dir)


def new_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="seo-agent-"))
