"""
wordpress_agent.py — pushes on-page fixes to a WordPress site via the REST API.

Two connection modes:
  - WordPress.com / Jetpack: OAuth token (scope "global") → public-api.wordpress.com/wp/v2/sites/<site>
  - Self-hosted: Application Password (Users → Profile → Application Passwords) → <site>/wp-json/wp/v2

Per page/post:
  - Image alt text for attached media     → media `alt_text`
  - JSON-LD (WebPage, Breadcrumb)          → Custom HTML block in the content, between
                                            <!-- hashfor:schema --> markers (idempotent;
                                            reverted if WordPress strips the <script>)
  - SEO title + meta description           → Rank Math fields when the plugin is active;
                                            otherwise the excerpt (only if empty) and a recommendation
"""
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

from cms_common import (
    CmsAuthError, Http, Log, PageResult, alt_text_for, assert_public_host, generate_fixes,
    make_row, recommendations_from, schema_block, strip_html, upsert_schema,
)

WPCOM_API = "https://public-api.wordpress.com"


class WordPressClient:
    def __init__(self, *, site_url: str, token: str = "", username: str = "", app_password: str = ""):
        if not site_url:
            raise CmsAuthError("WordPress site URL is missing.")
        self.site_url = site_url if site_url.startswith("http") else f"https://{site_url}"
        host = urlparse(self.site_url).hostname or ""
        if username and app_password:
            self.mode = "self-hosted"
            assert_public_host(self.site_url)
            root = self.site_url.rstrip("/") + "/wp-json"
            self.http = Http(f"{root}/wp/v2", auth=(username, app_password.replace(" ", "")), platform="WordPress")
            self.root = Http(root, auth=(username, app_password.replace(" ", "")), platform="WordPress")
        elif token:
            self.mode = "wordpress.com"
            headers = {"Authorization": f"Bearer {token}"}
            self.http = Http(f"{WPCOM_API}/wp/v2/sites/{quote(host)}", headers=headers, platform="WordPress")
            self.root = None  # plugin REST namespaces aren't proxied for wp.com sites
        else:
            raise CmsAuthError("WordPress is not connected (no OAuth token or application password).")
        self._rankmath: Optional[bool] = None

    def has_rankmath(self) -> bool:
        if self._rankmath is None:
            try:
                self._rankmath = bool(self.root) and "rankmath/v1" in (self.root.get("").get("namespaces") or [])
            except Exception:
                self._rankmath = False
        return self._rankmath

    # ---------- lookup ----------
    def find(self, url: str) -> Optional[Dict]:
        slug = urlparse(url).path.strip("/").split("/")[-1]
        if not slug:
            try:
                front = (self.http.get("settings") or {}).get("page_on_front")
            except Exception:
                front = None
            if front:
                return {"type": "pages", "data": self.http.get(f"pages/{front}", context="edit")}
            return None
        for t in ("pages", "posts"):
            items = self.http.get(t, slug=slug, context="edit")
            if items:
                return {"type": t, "data": items[0]}
        return None

    def discover(self, limit: int) -> List[str]:
        urls = [p["link"] for p in self.http.get("pages", per_page=max(1, limit - 2), _fields="link")]
        urls += [p["link"] for p in self.http.get("posts", per_page=2, _fields="link")]
        return urls[:limit]


def _raw(field) -> str:
    if isinstance(field, dict):
        return field.get("raw") if field.get("raw") is not None else field.get("rendered", "")
    return field or ""


def process_url(client: WordPressClient, url: str, site_base: str, dry_run: bool, inject_schema: bool, log: Log) -> PageResult:
    result = PageResult(url=url)
    res = client.find(url)
    if not res:
        result.status = "skipped"
        result.message = "No matching page or post (archives and the posts index can't be edited)."
        log(f"   ⏭️  {url}: {result.message}")
        return result

    t, d = res["type"], res["data"]
    result.resource = f"{t[:-1]}:{d['id']}"
    title = strip_html(_raw(d.get("title")))
    excerpt = strip_html(_raw(d.get("excerpt")))
    content = _raw(d.get("content"))
    row = make_row(url, title, excerpt, content)
    s = generate_fixes(row, site_base)
    result.recommendations = recommendations_from(s)
    new_title, new_desc = s["new_title"][:70], s["new_meta_description"][:320]

    # SEO title / description
    if client.has_rankmath():
        result.change("seo_title", title, new_title)
        result.change("meta_description", excerpt, new_desc)
        if not dry_run:
            client.root.post("rankmath/v1/updateMeta", {
                "objectType": "post", "objectID": d["id"],
                "meta": {"rank_math_title": new_title, "rank_math_description": new_desc},
            })
    else:
        result.recommendations["seo_title"] = new_title
        result.recommendations["meta_description"] = new_desc
        if not excerpt and t == "posts":
            result.change("meta_description", "", new_desc)  # excerpt is the default description for most themes/SEO plugins
            if not dry_run:
                client.http.post(f"{t}/{d['id']}", {"excerpt": new_desc})

    # Image alt text
    for m in client.http.get("media", parent=d["id"], per_page=20, context="edit"):
        if m.get("media_type") != "image" or (m.get("alt_text") or "").strip():
            continue
        hint = (m.get("source_url") or "").split("/")[-1]
        alt = alt_text_for(row, hint, site_base)
        result.change("image_alt", "", alt)
        if not dry_run:
            client.http.post(f"media/{m['id']}", {"alt_text": alt})

    # JSON-LD
    if inject_schema:
        block = f"<!-- wp:html -->\n{schema_block(row, s, site_base)}\n<!-- /wp:html -->"
        new_content = upsert_schema(content, block)
        if new_content != content:
            if dry_run:
                result.change("json_ld", "", "WebPage + BreadcrumbList JSON-LD")
            else:
                saved = client.http.post(f"{t}/{d['id']}", {"content": new_content})
                if "application/ld+json" in _raw(saved.get("content")):
                    result.change("json_ld", "", "WebPage + BreadcrumbList JSON-LD")
                else:
                    # This account can't publish <script> (no unfiltered_html) — put the content back.
                    client.http.post(f"{t}/{d['id']}", {"content": content})
                    result.recommendations["json_ld"] = (
                        "WordPress stripped the structured-data script for this account. Add it with an "
                        "administrator account or an SEO plugin's schema settings."
                    )

    result.status = ("planned" if dry_run else "updated") if result.changes else "unchanged"
    log(f"   ✅ {url}: {len(result.changes)} change(s) {'planned' if dry_run else 'applied'}")
    return result


def run_wordpress_agent(*, site_url: str, site_base: str, urls: List[str], token: str = "", username: str = "",
                        app_password: str = "", max_items: int = 8, dry_run: bool = False,
                        inject_schema: bool = True, log: Log) -> List[PageResult]:
    client = WordPressClient(site_url=site_url, token=token, username=username, app_password=app_password)
    log(f"📝 WORDPRESS AGENT — {client.site_url} via {client.mode} ({'dry run' if dry_run else 'live'})")
    targets = urls[:max_items] if urls else client.discover(max_items)
    results = []
    for i, url in enumerate(targets, 1):
        log(f"[{i}/{len(targets)}] {url}")
        try:
            results.append(process_url(client, url, site_base, dry_run, inject_schema, log))
        except CmsAuthError:
            raise
        except Exception as e:
            log(f"   ❌ {url}: {e}")
            results.append(PageResult(url=url, status="failed", message=str(e)[:300]))
    log("📝 WORDPRESS AGENT — done")
    return results
