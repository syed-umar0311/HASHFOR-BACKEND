"""
technical_agent.py — 🔵 TECHNICAL OPTIMIZATION AGENT

Owns everything that makes the site crawlable, renderable, and indexable:
  - robots.txt (incl. explicit AI-bot access rules — AEO Parameter 7)
  - sitemap.xml / sitemap.js (Next.js sitemap route)
  - llms.txt (AEO Parameter 10 — AI Discoverability, emerging convention)
  - Canonical tags
  - noindex handling
  - Redirects (next.config.js)
  - hreflang
  - A diagnostic report for things that can't be "fixed" by editing files
    alone (HTTPS, page speed, Core Web Vitals, mobile rendering, JS
    rendering, 404/5xx, orphan pages) — these need a live crawl/Lighthouse
    run or a link graph, so this agent flags them clearly instead of
    pretending to fix them blind.

Does NOT touch: title, meta description, headings, keywords, alt text,
internal links, JSON-LD content, OG/Twitter tags — that's onpage_agent.py.
"""
import re
from pathlib import Path
from typing import List

from common import Row, Context, run, get_site_name, safe_write

CANONICAL_START = "// TECHNICAL-CANONICAL-START"
CANONICAL_END = "// TECHNICAL-CANONICAL-END"

# Known AI/LLM crawlers as of early 2026. Listed explicitly (rather than
# relying on the blanket "User-agent: *" rule) because some site owners
# block these individually elsewhere, and being explicit here makes the
# intent auditable — this directly targets AEO Parameter 7 (AI Crawlability
# & Bot Access), which checks for named AI-bot permissions, not just a
# generic wildcard allow.
AI_BOTS = [
    "GPTBot", "ChatGPT-User", "OAI-SearchBot",   # OpenAI
    "ClaudeBot", "anthropic-ai", "Claude-Web",   # Anthropic
    "Google-Extended",                             # Google Gemini/AI training
    "PerplexityBot", "Perplexity-User",           # Perplexity
    "CCBot",                                       # Common Crawl (feeds many LLMs)
    "Amazonbot",                                   # Amazon/Alexa
    "Bytespider",                                  # ByteDance
    "Applebot-Extended",                           # Apple Intelligence
]


# ---------------------------
# Site-level: robots.txt (AEO Parameter 7 — AI Crawlability & Bot Access)
# ---------------------------
def write_robots_txt(ctx: Context):
    public_dir = ctx.repo_dir / ("public" if (ctx.repo_dir / "public").exists() else ".")
    target = public_dir / "robots.txt"
    site = ctx.site_base.rstrip("/")

    ai_bot_rules = "\n\n".join(f"User-agent: {bot}\nAllow: /" for bot in AI_BOTS)

    content = f"""User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/

# --- AI / LLM crawlers explicitly allowed (AEO: AI Crawlability & Bot Access) ---
{ai_bot_rules}

Sitemap: {site}/sitemap.xml
"""
    safe_write(target, target.read_text(encoding="utf-8") if target.exists() else "", content)


# ---------------------------
# Site-level: llms.txt (AEO Parameter 10 — AI Discoverability)
# An emerging convention (llmstxt.org): a plain-language index of the site's
# most important pages, written for an LLM to read directly rather than
# crawl and infer. Not yet a formal standard any model is required to honor,
# but costs nothing to provide and several AI crawlers already check for it.
# ---------------------------
def write_llms_txt(ctx: Context, rows: List[Row]):
    public_dir = ctx.repo_dir / ("public" if (ctx.repo_dir / "public").exists() else ".")
    target = public_dir / "llms.txt"
    site_name = get_site_name(ctx.site_base)

    lines = [f"# {site_name}", "", f"> {site_name} — key pages for AI systems and answer engines.", "", "## Pages"]
    for r in rows:
        title = r.title or r.url
        desc = (r.meta_description or "").strip()
        lines.append(f"- [{title}]({r.url}){': ' + desc if desc else ''}")
    content = "\n".join(lines) + "\n"
    safe_write(target, target.read_text(encoding="utf-8") if target.exists() else "", content)


# ---------------------------
# Site-level: sitemap
# For App Router Next.js, prefer the native sitemap.ts/js route.
# Also emit a static sitemap.xml fallback in /public for non-Next or edge cases.
# ---------------------------
def write_sitemap_js(ctx: Context, urls: List[str]):
    if not ctx.is_app_router:
        return
    base_dir = ctx.repo_dir / ("src/app" if (ctx.repo_dir / "src/app").exists() else "app")

    # Always emit plain sitemap.js — no TypeScript syntax. A .js file works
    # identically whether the project is JS or TS (Next.js only requires the
    # *type annotations* to live in .ts; a type-free file is valid either way).
    # Also remove any old sitemap.ts from a previous run so Next.js doesn't
    # see two conflicting sitemap routes in the same directory.
    old_ts = base_dir / "sitemap.ts"
    if old_ts.exists():
        old_ts.unlink()
        print(f"   🗑️  Removed stale {old_ts} (replaced by sitemap.js)")

    target = base_dir / "sitemap.js"
    site = ctx.site_base.rstrip("/")
    entries = ",\n  ".join(
        f'{{ url: "{u}", lastModified: new Date(), changeFrequency: "weekly", priority: 0.7 }}' for u in urls
    )
    content = f"""// Auto-generated by technical_agent.py — regenerate rather than hand-edit.
// Deliberately plain JavaScript (no TS types) so this works whether the
// project is JS or TS — Next.js only needs the file extension to be .js/.ts,
// it doesn't require type annotations inside it.
export default function sitemap() {{
  return [
  {entries}
  ];
}}
"""
    safe_write(target, target.read_text(encoding="utf-8") if target.exists() else "", content)


def write_sitemap_xml(ctx: Context, urls: List[str]):
    public_dir = ctx.repo_dir / ("public" if (ctx.repo_dir / "public").exists() else ".")
    target = public_dir / "sitemap.xml"
    body = "\n".join(f"  <url><loc>{u}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>" for u in urls)
    content = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>\n'
    safe_write(target, target.read_text(encoding="utf-8") if target.exists() else "", content)


# ---------------------------
# Page-level: canonical + noindex + hreflang
# ---------------------------
def build_canonical_block(row: Row, locales: List[str]) -> str:
    canonical_val = row.canonical_tag
    if not canonical_val or canonical_val.lower() == "false":
        canonical_val = row.url
    robots_val = (row.robots_meta or "").strip().lower() or "index, follow"
    if "noindex" in robots_val:
        robots_val = "noindex, nofollow"

    lang_block = ""
    if len(locales) > 1:
        lang_entries = ",\n      ".join(f'"{loc}": "{row.url}"' for loc in locales)
        lang_block = f"""
    languages: {{
      {lang_entries}
    }},"""

    return f"""{CANONICAL_START}
// 🔵 TECHNICAL: canonical, robots/noindex, hreflang
export const technicalMetadata = {{
  alternates: {{
    canonical: "{canonical_val}",{lang_block}
  }},
  robots: "{robots_val}",
}};
{CANONICAL_END}
"""


def apply_page_level(file: Path, row: Row, locales: List[str]) -> bool:
    """Returns True if this page is a client component ("use client") — AEO
    Parameter 8 (AI Rendering & Content Accessibility) cares about this,
    since content in a client component may not be present in the initial
    server-rendered HTML that non-JS-executing crawlers see."""
    if not file.exists():
        print(f"   ❌ [technical] Skipping — file does not exist: {file}")
        return False
    original = file.read_text(encoding="utf-8")
    is_client = original.lstrip().startswith(('"use client"', "'use client'"))
    if is_client:
        print(f"   ℹ️  Client component, skipping metadata-level technical fix: {file}")
        return True
    block = build_canonical_block(row, locales)
    pattern = re.compile(rf"{re.escape(CANONICAL_START)}.*?{re.escape(CANONICAL_END)}", re.DOTALL)
    content = pattern.sub(block, original) if pattern.search(original) else original + "\n" + block
    content = content.replace("\r\n", "\n")
    safe_write(file, original, content)
    return False


# ---------------------------
# Redirects: ensure next.config.js has a redirects() stub if URLs look moved
# (best-effort scaffold — real redirect mapping needs old->new URL pairs)
# ---------------------------
def ensure_redirects_scaffold(ctx: Context):
    for name in ["next.config.js", "next.config.mjs", "next.config.ts"]:
        cfg = ctx.repo_dir / name
        if cfg.exists():
            content = cfg.read_text(encoding="utf-8")
            if "async redirects()" not in content and "redirects()" not in content:
                print(f"   ℹ️  {name} has no redirects() — add one manually when you have old→new URL pairs.")
            return
    print("   ℹ️  No next.config.js found — skipping redirects scaffold.")


# ---------------------------
# Diagnostic-only report for things this agent can't blind-fix
# ---------------------------
def write_technical_report(ctx: Context, rows: List[Row], client_component_urls: List[str] = None):
    client_component_urls = client_component_urls or []
    orphans = [r.url for r in rows if r.internal_links == 0]

    lines = [
        "# Technical SEO Report (auto-generated)",
        "",
        "## AI Crawlability & Bot Access (AEO Parameter 7)",
        f"robots.txt now explicitly allows: {', '.join(AI_BOTS)}.",
        "Verify none of these are blocked elsewhere (CDN/WAF rules, Cloudflare bot-fight mode, etc.) —",
        "robots.txt only controls what your own server declares, not upstream network-level blocks.",
        "",
        "## AI Rendering & Content Accessibility (AEO Parameter 8)",
    ]
    if client_component_urls:
        lines.append("These pages are client components (`\"use client\"`) — their main content may not")
        lines.append("be present in the initial server-rendered HTML that some AI crawlers read without")
        lines.append("executing JavaScript. Consider moving primary content to a server component, or")
        lines.append("verify with 'view source' that the important text is actually there pre-hydration:")
        lines += [f"- {u}" for u in client_component_urls]
    else:
        lines.append("No client components detected among the mapped pages — content should be present")
        lines.append("in the initial server-rendered HTML.")

    lines += [
        "",
        "## AI Discoverability (AEO Parameter 10)",
        "llms.txt and sitemap.xml/sitemap.js are generated. Orphan-page check (pages with",
        "zero detected internal links pointing to them, from your CSV's `internal_links` column):",
    ]
    if orphans:
        lines += [f"- ⚠️ {u} — no internal links found; add links to it from other pages" for u in orphans]
    else:
        lines.append("None found — every page has at least one internal link (or the CSV didn't report this column).")

    lines += [
        "",
        "## HTTPS / mobile viewport / canonical (from CSV)",
        "",
        "| URL | HTTPS | Notes |",
        "|---|---|---|",
    ]
    for r in rows:
        https_ok = "✅" if r.url.startswith("https://") else "❌ not HTTPS"
        notes = []
        if r.viewport_present and r.viewport_present.lower() == "false":
            notes.append("missing viewport meta (mobile)")
        if r.canonical_tag and r.canonical_tag.lower() == "false":
            notes.append("no canonical tag")
        lines.append(f"| {r.url} | {https_ok} | {'; '.join(notes) or '—'} |")

    lines += [
        "",
        "## Still to check manually / with a live tool",
        "- Core Web Vitals (LCP, CLS, INP) — run PageSpeed Insights per URL",
        "- Page speed — run Lighthouse in CI",
        "- 404 / 5xx status codes — run a live crawler (e.g. Screaming Frog) post-deploy",
        "- JS rendering — verify with 'view source' vs rendered DOM (App Router SSR helps here)",
        "- Mobile technical SEO — confirm responsive breakpoints render correctly",
    ]
    target = ctx.repo_dir / "TECHNICAL_SEO_REPORT.md"
    safe_write(target, "", "\n".join(lines) + "\n")


def run_technical_agent(rows: List[Row], ctx: Context, locales: List[str] = None):
    locales = locales or []
    print("\n🔵 TECHNICAL AGENT — starting")
    urls = [r.url for r in rows]
    write_robots_txt(ctx)
    write_sitemap_xml(ctx, urls)
    write_sitemap_js(ctx, urls)
    write_llms_txt(ctx, rows)
    ensure_redirects_scaffold(ctx)
    client_component_urls = []
    for i, row in enumerate(rows, 1):
        target = ctx.url_to_file.get(row.url)
        if not target:
            print(f"   ❌ No file mapped for {row.url}")
            continue
        print(f"[{i}/{len(rows)}] 🔵 Technical: {row.url}")
        is_client = apply_page_level(target, row, locales)
        if is_client:
            client_component_urls.append(row.url)
    write_technical_report(ctx, rows, client_component_urls)
    print("🔵 TECHNICAL AGENT — done")
