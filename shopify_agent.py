"""
shopify_agent.py — pushes on-page fixes to a Shopify store via the Admin REST API.

Per page (product / collection / page / blog article):
  - SEO title + meta description   → metafields global.title_tag / global.description_tag
  - Image alt text (products)      → product image `alt`
  - JSON-LD (WebPage, Breadcrumb)  → appended to the description inside
                                     <!-- hashfor:schema --> markers (idempotent)
Needs the OAuth scopes the app already requests:
  read_products, write_products, read_content, write_content
"""
import os
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from cms_common import (
    CmsAuthError, Http, Log, PageResult, alt_text_for, generate_fixes, make_row,
    recommendations_from, schema_block, upsert_schema,
)

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-10")
_STORE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
_STABLE_RE = re.compile(r"^\d{4}-\d{2}$")
_VERSION_QUERY = "{ publicApiVersions { handle displayName supported } }"


def normalize_store(store: str) -> str:
    s = (store or "").strip().lower()
    s = re.sub(r"^https?://", "", s).strip("/")
    if not _STORE_RE.match(s):
        raise CmsAuthError(f"Invalid Shopify store domain: {store!r} (expected something.myshopify.com)")
    return s


class ShopifyClient:
    def __init__(self, store: str, token: str):
        if not token:
            raise CmsAuthError("Shopify is not connected (missing access token).")
        self.store = normalize_store(store)
        self.http = Http(
            f"https://{self.store}/admin/api/{API_VERSION}",
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            platform="Shopify",
        )

    # ---------- API version ----------
    def check_api_version(self) -> Dict:
        """Compare SHOPIFY_API_VERSION with the versions Shopify currently supports.

        Shopify serves its oldest supported version when a requested one is no longer
        supported, and reports the version it actually used in X-Shopify-API-Version,
        so both the supported list and the served header are checked.
        """
        data = self.http.post("graphql.json", {"query": _VERSION_QUERY})
        served = (self.http.last_headers or {}).get("X-Shopify-API-Version", "")
        versions = (data.get("data") or {}).get("publicApiVersions") or []
        supported = [v["handle"] for v in versions if v.get("supported")]
        stable = sorted(
            v["handle"] for v in versions
            if v.get("supported") and _STABLE_RE.match(v.get("handle", ""))
            and "candidate" not in (v.get("displayName") or "").lower()
        )
        latest = stable[-1] if stable else ""
        ok = API_VERSION in supported and (not served or served == API_VERSION)

        if not ok:
            status = "unsupported"
            message = (f"SHOPIFY_API_VERSION={API_VERSION} is no longer supported"
                       f"{f'; Shopify answered with {served} instead' if served and served != API_VERSION else ''}. "
                       f"Set SHOPIFY_API_VERSION={latest or 'a supported version'} on the agent server.")
        elif len(stable) > 1 and stable[0] == API_VERSION:
            status = "oldest"
            message = (f"SHOPIFY_API_VERSION={API_VERSION} is the oldest supported version and will be retired soon. "
                       f"Update to {latest}.")
        elif latest and API_VERSION != latest:
            status = "outdated"
            message = f"SHOPIFY_API_VERSION={API_VERSION} is supported; the newest stable version is {latest}."
        else:
            status = "current"
            message = f"SHOPIFY_API_VERSION={API_VERSION} is the newest stable version."
        return {"configured": API_VERSION, "served": served, "status": status, "ok": ok,
                "latest_stable": latest, "supported_versions": stable, "message": message}

    # ---------- lookup ----------
    def find(self, url: str) -> Optional[Dict]:
        """Map a storefront URL to the Shopify resource behind it."""
        path = urlparse(url).path.rstrip("/")
        m = re.search(r"/products/([^/?#]+)$", path)
        if m:
            items = self.http.get("products.json", handle=m.group(1), fields="id,title,handle,body_html,images").get("products", [])
            return items and {"kind": "product", "data": items[0]}
        m = re.search(r"/collections/([^/?#]+)$", path)
        if m:
            for kind in ("custom_collections", "smart_collections"):
                items = self.http.get(f"{kind}.json", handle=m.group(1)).get(kind, [])
                if items:
                    return {"kind": kind[:-1], "data": items[0]}
            return None
        m = re.search(r"/pages/([^/?#]+)$", path)
        if m:
            items = self.http.get("pages.json", handle=m.group(1)).get("pages", [])
            return items and {"kind": "page", "data": items[0]}
        m = re.search(r"/blogs/([^/?#]+)/([^/?#]+)$", path)
        if m:
            blogs = self.http.get("blogs.json", handle=m.group(1)).get("blogs", [])
            if blogs:
                arts = self.http.get(f"blogs/{blogs[0]['id']}/articles.json", handle=m.group(2)).get("articles", [])
                if arts:
                    return {"kind": "article", "data": arts[0], "blog_id": blogs[0]["id"]}
        return None

    def discover(self, site_base: str, limit: int) -> List[str]:
        """No URL list supplied: take the store's first products and pages."""
        base = site_base.rstrip("/")
        urls = [f"{base}/products/{p['handle']}" for p in
                self.http.get("products.json", limit=max(1, limit - 2), fields="handle").get("products", [])]
        urls += [f"{base}/pages/{p['handle']}" for p in
                 self.http.get("pages.json", limit=2, fields="handle").get("pages", [])]
        return urls[:limit]

    # ---------- paths ----------
    @staticmethod
    def update_path(res: Dict):
        d, kind = res["data"], res["kind"]
        if kind == "article":
            return f"blogs/{res['blog_id']}/articles/{d['id']}.json", "article"
        plural = {"product": "products", "page": "pages",
                  "custom_collection": "custom_collections", "smart_collection": "smart_collections"}[kind]
        return f"{plural}/{d['id']}.json", kind

    @staticmethod
    def metafield_owner(res: Dict) -> str:
        d, kind = res["data"], res["kind"]
        if kind == "article":
            return f"blogs/{res['blog_id']}/articles/{d['id']}/metafields.json"
        owner = {"product": "products", "page": "pages",
                 "custom_collection": "collections", "smart_collection": "collections"}[kind]
        return f"{owner}/{d['id']}/metafields.json"

    # ---------- metafields ----------
    def seo_fields(self, res: Dict) -> Dict[str, Dict]:
        items = self.http.get(self.metafield_owner(res), namespace="global").get("metafields", [])
        return {m["key"]: m for m in items if m.get("key") in ("title_tag", "description_tag")}

    def set_metafield(self, res: Dict, existing: Optional[Dict], key: str, value: str):
        if existing:
            self.http.put(f"metafields/{existing['id']}.json", {"metafield": {"id": existing["id"], "value": value}})
        else:
            self.http.post(self.metafield_owner(res), {"metafield": {
                "namespace": "global", "key": key, "value": value, "type": "single_line_text_field"}})


def process_url(client: ShopifyClient, url: str, site_base: str, dry_run: bool, inject_schema: bool, log: Log) -> PageResult:
    result = PageResult(url=url)
    res = client.find(url)
    if not res:
        result.status = "skipped"
        result.message = ("Homepage SEO is set in Shopify admin → Online Store → Preferences."
                          if urlparse(url).path.strip("/") == "" else "No matching product, collection, page or article.")
        log(f"   ⏭️  {url}: {result.message}")
        return result

    data, kind = res["data"], res["kind"]
    result.resource = f"{kind}:{data['id']}"
    seo = client.seo_fields(res)
    cur_title = (seo.get("title_tag") or {}).get("value") or data.get("title", "")
    cur_desc = (seo.get("description_tag") or {}).get("value") or ""
    row = make_row(url, cur_title, cur_desc, data.get("body_html", ""))
    s = generate_fixes(row, site_base)
    result.recommendations = recommendations_from(s)

    new_title, new_desc = s["new_title"][:70], s["new_meta_description"][:320]
    if new_title and new_title != cur_title:
        result.change("seo_title", cur_title, new_title)
        if not dry_run:
            client.set_metafield(res, seo.get("title_tag"), "title_tag", new_title)
    if new_desc and new_desc != cur_desc:
        result.change("meta_description", cur_desc, new_desc)
        if not dry_run:
            client.set_metafield(res, seo.get("description_tag"), "description_tag", new_desc)

    if kind == "product":
        for img in data.get("images", []) or []:
            if (img.get("alt") or "").strip():
                continue
            hint = (img.get("src") or "").split("/")[-1].split("?")[0]
            alt = alt_text_for(row, hint, site_base)
            result.change("image_alt", "", alt)
            if not dry_run:
                client.http.put(f"products/{data['id']}/images/{img['id']}.json", {"image": {"id": img["id"], "alt": alt}})

    if inject_schema and "body_html" in data:
        new_body = upsert_schema(data.get("body_html") or "", schema_block(row, s, site_base))
        if new_body != (data.get("body_html") or ""):
            result.change("json_ld", "", "WebPage + BreadcrumbList JSON-LD")
            if not dry_run:
                path, key = client.update_path(res)
                client.http.put(path, {key: {"id": data["id"], "body_html": new_body}})

    result.status = ("planned" if dry_run else "updated") if result.changes else "unchanged"
    log(f"   ✅ {url}: {len(result.changes)} change(s) {'planned' if dry_run else 'applied'}")
    return result


def run_shopify_agent(store: str, token: str, site_base: str, urls: List[str], *,
                      max_items: int = 8, dry_run: bool = False, inject_schema: bool = True, log: Log,
                      info: Optional[Dict] = None) -> List[PageResult]:
    client = ShopifyClient(store, token)
    log(f"🛍️  SHOPIFY AGENT — {client.store} ({'dry run' if dry_run else 'live'})")
    try:
        version = client.check_api_version()
    except CmsAuthError:
        raise
    except Exception as e:  # the check must never block a run
        version = {"configured": API_VERSION, "status": "unknown", "ok": None, "message": f"Version check failed: {e}"}
    log(f"   {'⚠️ ' if version.get('ok') is False else 'ℹ️ '} API version: {version['message']}")
    if info is not None:
        info["shopify_api_version"] = version
    targets = urls[:max_items] if urls else client.discover(site_base, max_items)
    results = []
    for i, url in enumerate(targets, 1):
        log(f"[{i}/{len(targets)}] {url}")
        try:
            results.append(process_url(client, url, site_base, dry_run, inject_schema, log))
        except CmsAuthError:
            raise
        except Exception as e:  # one bad page must not stop the rest
            log(f"   ❌ {url}: {e}")
            results.append(PageResult(url=url, status="failed", message=str(e)[:300]))
    log("🛍️  SHOPIFY AGENT — done")
    return results


if __name__ == "__main__":
    # Quick check from the command line:
    #   SHOPIFY_STORE=mystore.myshopify.com SHOPIFY_TOKEN=shpat_... python shopify_agent.py
    import json
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    store, token = os.getenv("SHOPIFY_STORE", ""), os.getenv("SHOPIFY_TOKEN", "")
    if not store or not token:
        sys.exit("Set SHOPIFY_STORE and SHOPIFY_TOKEN to check the API version.")
    result = ShopifyClient(store, token).check_api_version()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
