"""
cms_common.py — shared plumbing for the CMS (Shopify / WordPress) agents.

The GitHub flow edits source files and pushes commits. A hosted CMS has no
repository, so these agents push the same on-page fixes through the
platform's API instead. Fix *content* comes from the existing on-page agent
(generate_onpage_suggestions / generate_alt_text / build_schemas), so both
flows produce the same titles, descriptions, alt text and JSON-LD.

Nothing here changes the GitHub agents' behavior.
"""
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass, field
from html import unescape
from typing import Dict, List, Optional
from urllib.parse import urlparse

import openai
import requests

from common import Row
from onpage_agent import build_schemas, generate_alt_text, generate_onpage_suggestions

SCHEMA_START = "<!-- hashfor:schema:start -->"
SCHEMA_END = "<!-- hashfor:schema:end -->"
_SCHEMA_BLOCK_RE = re.compile(re.escape(SCHEMA_START) + r".*?" + re.escape(SCHEMA_END), re.DOTALL)

# Schema types safe to add without matching visible content. FAQ/HowTo schema
# must mirror on-page copy, so those are returned as recommendations instead.
CMS_SCHEMA_TYPES = {"WebPage", "BreadcrumbList"}

# Which AI-visibility audit parameters (backend metric families) each change addresses.
# Title/meta improve classic SEO but no AI-visibility parameter measures them directly.
FIELD_METRICS = {
    "image_alt": ["rendering_accessibility"],
    "json_ld": ["structured_data", "topical_semantic_entity"],
}

HTTP_TIMEOUT = 30


class CmsAuthError(Exception):
    """Token rejected / missing scope — the whole run must stop."""


def init_openai():
    if not os.getenv("OPENAI_API_KEY"):
        raise CmsAuthError("OPENAI_API_KEY not set on the agent server.")
    openai.api_key = os.getenv("OPENAI_API_KEY")


def assert_public_host(url: str):
    """Refuse URLs that resolve to private/internal addresses (the agent calls user-supplied hosts)."""
    host = urlparse(url).hostname or ""
    if not host or urlparse(url).scheme != "https":
        raise ValueError(f"URL must be https: {url}")
    for info in socket.getaddrinfo(host, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"Refusing to call non-public host: {host}")


def strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html or "", flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def make_row(url: str, title: str = "", meta_description: str = "", body_html: str = "") -> Row:
    text = strip_html(body_html)
    return Row(url=url, title=title or "", meta_description=meta_description or "", word_count=len(text.split()))


def generate_fixes(row: Row, site_base: str) -> Dict:
    """Same LLM suggestions the GitHub on-page agent uses."""
    return generate_onpage_suggestions(row, site_base)


def alt_text_for(row: Row, image_hint: str, site_base: str) -> str:
    return generate_alt_text(row, image_hint, site_base)[:125]


def schema_block(row: Row, suggestions: Dict, site_base: str) -> str:
    scripts = []
    for js in build_schemas(row, suggestions, site_base):
        if json.loads(js).get("@type") in CMS_SCHEMA_TYPES:
            safe = js.replace("</", "<\\/")  # never let JSON close the <script> early
            scripts.append(f'<script type="application/ld+json">{safe}</script>')
    return f"{SCHEMA_START}\n" + "\n".join(scripts) + f"\n{SCHEMA_END}"


def upsert_schema(body_html: str, block: str) -> str:
    """Idempotent: replaces our previous block instead of stacking a new one each run."""
    body = body_html or ""
    if _SCHEMA_BLOCK_RE.search(body):
        return _SCHEMA_BLOCK_RE.sub(lambda _m: block, body)
    return (body.rstrip() + "\n" + block) if body.strip() else block


def recommendations_from(suggestions: Dict) -> Dict:
    """Content changes that need human review — reported, never auto-published."""
    return {
        "answer_first_summary": suggestions.get("answer_first_summary", ""),
        "faq": suggestions.get("faq_schema", []),
        "heading_fixes": suggestions.get("heading_fixes", ""),
        "content_clarity_notes": suggestions.get("content_clarity_notes", ""),
        "extractability_notes": suggestions.get("extractability_notes", ""),
    }


@dataclass
class PageResult:
    url: str
    resource: str = ""
    status: str = "pending"  # updated | planned | unchanged | skipped | failed
    message: str = ""
    changes: List[Dict] = field(default_factory=list)
    recommendations: Dict = field(default_factory=dict)

    def change(self, field_name: str, before, after):
        self.changes.append({
            "field": field_name,
            "before": before,
            "after": after,
            "metrics": FIELD_METRICS.get(field_name, []),
        })

    def to_dict(self):
        return {
            "url": self.url,
            "resource": self.resource,
            "status": self.status,
            "message": self.message,
            "changes": self.changes,
            "recommendations": self.recommendations,
        }


class Log:
    """Collects a human-readable log, returned as `stdout` like the GitHub /run endpoint."""

    def __init__(self):
        self.lines: List[str] = []

    def __call__(self, msg: str):
        self.lines.append(msg)
        try:
            print(msg)
        except UnicodeEncodeError:  # e.g. Windows console code page; logging must never fail a run
            print(msg.encode("ascii", "replace").decode("ascii"))

    def text(self) -> str:
        return "\n".join(self.lines)


class Http:
    """Thin requests wrapper: JSON, timeouts, and auth failures surfaced as CmsAuthError."""

    def __init__(self, base: str, headers: Optional[Dict] = None, auth=None, platform: str = ""):
        self.base = base.rstrip("/")
        self.platform = platform
        self.last_headers = {}
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", **(headers or {})})
        if auth:
            self.session.auth = auth

    def request(self, method: str, path: str, params=None, body=None, absolute: bool = False):
        url = path if absolute else f"{self.base}/{path.lstrip('/')}"
        resp = self.session.request(method, url, params=params, json=body, timeout=HTTP_TIMEOUT)
        self.last_headers = resp.headers  # e.g. Shopify reports the API version it actually served
        if resp.status_code in (401, 403):
            raise CmsAuthError(
                f"{self.platform} rejected the request ({resp.status_code}). "
                f"Reconnect {self.platform} and make sure the app has write permission."
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def put(self, path, body):
        return self.request("PUT", path, body=body)

    def post(self, path, body):
        return self.request("POST", path, body=body)


def summarize(results: List[PageResult], dry_run: bool) -> Dict:
    applied = [r for r in results if r.status in ("updated", "planned")]
    addressed = sorted({m for r in applied for c in r.changes for m in c["metrics"]})
    counts = {s: sum(1 for r in results if r.status == s)
              for s in ("updated", "planned", "unchanged", "skipped", "failed")}
    failed = counts["failed"]
    return {
        "success": bool(results) and failed < len(results),
        "dry_run": dry_run,
        "results": [r.to_dict() for r in results],
        "addressed_metrics": [] if dry_run else addressed,
        "summary": counts,
    }
