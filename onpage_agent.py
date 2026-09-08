"""
onpage_agent.py — 🟢 ON-PAGE OPTIMIZATION AGENT

Owns everything that improves the page and its content:
  - Content clarity / structure suggestions
  - H1/H2/H3 heading structure
  - Title tag
  - Meta description
  - Keywords / topics
  - Internal links (cross-page, keyword-driven)
  - Alt text for images missing it
  - JSON-LD / Schema (WebPage, FAQ, HowTo)
  - Open Graph tags
  - Twitter/X tags

Does NOT touch: robots.txt, sitemap, canonical, noindex, redirects,
hreflang, HTTPS, speed, Core Web Vitals — that's technical_agent.py.
"""
import json
import re
from pathlib import Path
from typing import Dict, List

from common import Row, Context, ask_openai, extract_json, get_site_name, safe_write

START_MARKER = "// ONPAGE-AUTO-FIX-START"
END_MARKER = "// ONPAGE-AUTO-FIX-END"


# ---------------------------
# LLM: title / meta / schema content / keyword & heading suggestions
# ---------------------------
def generate_onpage_suggestions(row: Row, site_base: str) -> Dict[str, object]:
    site_name = get_site_name(site_base)

    prompt = f"""
You are an SEO, GEO (Generative Engine Optimization), and AEO (Answer Engine
Optimization) expert doing on-page optimization only (no technical SEO).

CURRENT DATA:
- URL: {row.url}
- Current Title: {row.title}
- Current Meta Description: {row.meta_description}
- Current Heading Order: {row.heading_order} (H1 count: {row.h1_count})
- Word Count: {row.word_count}, Readability: {row.readability_score}, Grammar errors: {row.grammar_errors}
- Top Keywords: {row.top_keywords}
- Existing notes: {row.seo_suggestions}

Return ONLY valid JSON:
{{
  "new_title": "50-60 chars, primary keyword near the front",
  "new_meta_description": "150-160 chars, keyword + call to action",
  "heading_fixes": "One short paragraph describing how H1/H2/H3 should be restructured for this page",
  "content_clarity_notes": "One short paragraph on how to make the copy clearer/briefer/better structured",
  "keywords": ["8 relevant keywords/topics, ordered by importance"],
  "faq_schema": [
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}}
  ],
  "howto_steps": [
    {{"name": "Step 1", "text": "..."}},
    {{"name": "Step 2", "text": "..."}}
  ],
  "speakable_snippet": "One sentence that directly answers the likely user query"
}}
"""
    data = extract_json(ask_openai(prompt)) or {}

    data.setdefault("new_title", row.title or f"{site_name} - Professional Website")
    data.setdefault("new_meta_description", row.meta_description or f"Explore {site_name} for professional services.")
    data.setdefault("heading_fixes", "Ensure exactly one H1, then a logical H2 > H3 hierarchy matching content sections.")
    data.setdefault("content_clarity_notes", "Break long paragraphs into shorter ones, add subheadings, lead with the answer.")
    if not data.get("keywords"):
        raw = re.sub(r":\d+\.\d+%", "", row.top_keywords or "")
        data["keywords"] = [k.strip() for k in re.split(r"[,\|;]", raw) if k.strip()][:8] or \
            ["professional", "services", "website", "company", "business"]
    if not data.get("faq_schema") or len(data["faq_schema"]) < 2:
        data["faq_schema"] = [
            {"question": f"What is {site_name}?", "answer": f"{site_name} is a platform for professional services."},
            {"question": f"How does {site_name} work?", "answer": f"{site_name} provides solutions through an easy-to-use product."},
            {"question": f"Why choose {site_name}?", "answer": f"{site_name} is reliable, innovative, and secure."},
        ]
    if not data.get("howto_steps"):
        data["howto_steps"] = [{"name": "Get Started", "text": "Sign up and explore the platform."}]
    if not data.get("speakable_snippet"):
        data["speakable_snippet"] = f"Explore {site_name} for professional services and insights."
    return data


def generate_alt_text(row: Row, image_hint: str, site_base: str) -> str:
    prompt = (
        f"Write concise, descriptive, keyword-aware alt text (under 125 characters, no 'image of') "
        f"for an image on this page.\nPage URL: {row.url}\nPage topic keywords: {row.top_keywords}\n"
        f"Image context/filename: {image_hint}\nReturn ONLY the alt text, nothing else."
    )
    text = ask_openai(prompt, max_tokens=60)
    return text.strip().strip('"') or f"{get_site_name(site_base)} — {image_hint or 'page image'}"


# ---------------------------
# Internal links: simple keyword-overlap heuristic across all rows
# ---------------------------
def build_internal_link_suggestions(rows: List[Row]) -> Dict[str, List[str]]:
    def kw_set(r: Row):
        raw = re.sub(r":\d+\.\d+%", "", r.top_keywords or "")
        return {k.strip().lower() for k in re.split(r"[,\|;]", raw) if k.strip()}

    kws = {r.url: kw_set(r) for r in rows}
    suggestions: Dict[str, List[str]] = {}
    for r in rows:
        overlaps = []
        for other in rows:
            if other.url == r.url:
                continue
            shared = kws[r.url] & kws[other.url]
            if shared:
                overlaps.append((len(shared), other.url))
        overlaps.sort(reverse=True)
        suggestions[r.url] = [url for _, url in overlaps[:3]]
    return suggestions


# ---------------------------
# Schema + JSX injection
# ---------------------------
def build_schemas(row: Row, s: Dict, site_base: str) -> List[str]:
    site_name = get_site_name(site_base)
    schemas = [{
        "@context": "https://schema.org",
        "@type": "WebPage",
        "url": row.url,
        "name": s["new_title"],
        "description": s["new_meta_description"],
        "publisher": {"@type": "Organization", "name": site_name, "url": site_base},
        "speakable": {
            "@type": "SpeakableSpecification",
            "xpath": ["/html/head/title", "/html/head/meta[@name='description']/@content"],
        },
    }]
    if s.get("faq_schema"):
        schemas.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q["question"],
                 "acceptedAnswer": {"@type": "Answer", "text": q["answer"]}}
                for q in s["faq_schema"]
            ],
        })
    if s.get("howto_steps"):
        schemas.append({
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": s["new_title"],
            "step": [{"@type": "HowToStep", "name": st["name"], "text": st["text"]} for st in s["howto_steps"]],
        })
    return [json.dumps(sc, ensure_ascii=False, separators=(",", ":")) for sc in schemas]


def build_script_jsx(row: Row, s: Dict, site_base: str) -> str:
    tags = []
    for schema_json in build_schemas(row, s, site_base):
        escaped = schema_json.replace("`", "\\`").replace("${", "\\${")
        tags.append(f'<script type="application/ld+json" dangerouslySetInnerHTML={{{{ __html: `{escaped}` }}}} />')
    return "\n".join(tags)


_LDJSON_TAG_RE = re.compile(
    # Matches our own generated tags exactly as written: TWO braces around
    # __html (the JSX expression brace + the object-literal brace), e.g.
    #   dangerouslySetInnerHTML={{ __html: `...` }}
    # The previous version of this regex only accounted for one brace and
    # therefore never matched anything — old tags piled up on every run.
    r'\s*<script\s+type="application/ld\+json"\s+dangerouslySetInnerHTML=\{\{\s*__html:\s*`.*?`\s*\}\}\s*/>',
    re.DOTALL | re.IGNORECASE,
)


def _find_balanced_return_span(content: str):
    """Find the return(...) whose JSX payload is the page's actual root render.

    Rather than assuming the first/last `return (` in the file is the right
    one, this scans every `return (` and picks the one with the LARGEST
    balanced-paren span — in practice that's always the component's main JSX
    return, not an early guard clause like `if (x) return null;` or a helper
    function above it. Returns (return_start, jsx_start, jsx_end) or None.
    """
    best = None
    for m in re.finditer(r"return\s*\(", content):
        jsx_start = m.end()
        depth = 1
        i = jsx_start
        while i < len(content) and depth > 0:
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
            i += 1
        if depth != 0:
            continue  # unbalanced — skip rather than risk corrupting the file
        jsx_end = i - 1
        span = jsx_end - jsx_start
        if best is None or span > best[3]:
            best = (m.start(), jsx_start, jsx_end, span)
    return None if best is None else best[:3]


def inject_scripts(content: str, script_jsx: str) -> str:
    """Insert JSON-LD <script> tags as children of the page's single JSX root.

    Always wraps everything in one Fragment, regardless of whether the
    existing root was a Fragment, a single tag, or a self-closing component —
    that's the only structure that's valid for every case, since a component
    can only return one root element. Idempotent: running this twice does
    not add a second layer of Fragment or duplicate script tags.
    """
    span = _find_balanced_return_span(content)
    if not span:
        print("   ⚠️  Could not safely locate this component's JSX return — skipping script injection.")
        return content
    return_start, jsx_start, jsx_end = span

    original_jsx = _LDJSON_TAG_RE.sub("", content[jsx_start:jsx_end]).strip()

    if original_jsx.startswith("<>") and original_jsx.endswith("</>"):
        inner = original_jsx[2:-3].strip()
        new_jsx = f"<>\n{script_jsx}\n{inner}\n</>"
    else:
        new_jsx = f"<>\n{script_jsx}\n{original_jsx}\n</>"

    return content[:return_start] + f"return (\n  {new_jsx}\n)" + content[jsx_end + 1:]


def annotate_missing_alt(content: str) -> str:
    """Flag <img> tags with no alt attribute so a human/reviewer can see what still needs one.
    (Actual alt text is generated per-image via generate_alt_text when a filename/hint is known.)"""
    def add_marker(m):
        tag = m.group(0)
        if "alt=" in tag:
            return tag
        return tag[:-1].rstrip() + " /* TODO(onpage-agent): missing alt text */>"
    return re.sub(r"<img\b[^>]*?/?>", add_marker, content)


def build_onpage_block(s: Dict, row: Row, site_base: str, internal_links: List[str]) -> str:
    def esc(v: str) -> str:
        return (v or "").replace('"', '\\"').replace("\n", " ").strip()

    title, desc = esc(s["new_title"]), esc(s["new_meta_description"])
    keywords_js = ",\n    ".join(f'"{esc(k)}"' for k in s["keywords"])
    og_image = f"{row.url.rstrip('/')}/opengraph-image.jpg"
    tw_image = f"{row.url.rstrip('/')}/twitter-image.jpg"
    site_name = get_site_name(site_base)
    links_comment = ", ".join(internal_links) if internal_links else "none found"

    return f"""{START_MARKER}
// 🟢 ON-PAGE: title, meta description, keywords, Open Graph, Twitter tags
// Suggested internal links for this page: {links_comment}
// Heading structure notes: {esc(s['heading_fixes'])}
// Content clarity notes: {esc(s['content_clarity_notes'])}
export const onPageMetadata = {{
  title: "{title}",
  description: "{desc}",
  keywords: [
    {keywords_js}
  ],
  openGraph: {{
    title: "{title}",
    description: "{desc}",
    url: "{esc(row.url)}",
    siteName: "{site_name}",
    images: [{{ url: "{og_image}", width: 1200, height: 630, alt: "{title}" }}],
    type: "website"
  }},
  twitter: {{
    card: "summary_large_image",
    title: "{title}",
    description: "{desc}",
    images: ["{tw_image}"]
  }}
}};
{END_MARKER}
"""


def apply(file: Path, row: Row, s: Dict, site_base: str, internal_links: List[str]):
    if not file.exists():
        print(f"   ❌ [on-page] Skipping — file does not exist: {file}")
        return
    original = file.read_text(encoding="utf-8")
    content = original
    is_client = original.lstrip().startswith(('"use client"', "'use client'"))

    if not is_client:
        block = build_onpage_block(s, row, site_base, internal_links)
        pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)
        content = pattern.sub(block, content) if pattern.search(content) else block + "\n" + content

    script_jsx = build_script_jsx(row, s, site_base)
    content = inject_scripts(content, script_jsx)
    content = annotate_missing_alt(content)
    content = content.replace("\r\n", "\n")
    safe_write(file, original, content)


def run_onpage_agent(rows: List[Row], ctx: Context):
    print("\n🟢 ON-PAGE AGENT — starting")
    internal_link_map = build_internal_link_suggestions(rows)
    for i, row in enumerate(rows, 1):
        target = ctx.url_to_file.get(row.url)
        if not target:
            print(f"   ❌ No file mapped for {row.url}")
            continue
        print(f"[{i}/{len(rows)}] 🟢 On-page: {row.url}")
        suggestions = generate_onpage_suggestions(row, ctx.site_base)
        apply(target, row, suggestions, ctx.site_base, internal_link_map.get(row.url, []))
    print("🟢 ON-PAGE AGENT — done")
