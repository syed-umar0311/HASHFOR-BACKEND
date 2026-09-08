#!/usr/bin/env python3
"""
orchestrator.py — runs the optimization agents end-to-end and pushes.

Off-page agent is intentionally NOT included yet (per request: "no now").
Wire it in later as off_page_agent.run_offpage_agent(rows, ctx).

Usage (env vars):
  OPENAI_API_KEY, GITHUB_TOKEN, REPO_URL, BRANCH, SITE_BASE, CSV_PATH
  GIT_USERNAME, GIT_EMAIL
  AGENTS=onpage,technical   (default: both)
  LOCALES=en,fr             (optional, comma-separated, for hreflang)
"""
import os
import sys

from common import (
    Context, require_env, clone_repo, detect_stack, map_urls_to_files,
    parse_csv, commit_and_push, new_workdir, CSV_PATH, SITE_BASE,
)
from onpage_agent import run_onpage_agent
from technical_agent import run_technical_agent


def main():
    print("🚀 Autonomous Website Optimizer")
    print("=" * 50)
    require_env()

    agents = [a.strip().lower() for a in os.getenv("AGENTS", "onpage,technical").split(",") if a.strip()]
    locales = [l.strip() for l in os.getenv("LOCALES", "").split(",") if l.strip()]

    workdir = new_workdir()
    print("📥 Cloning repo...")
    clone_repo(workdir)

    print("🔎 Detecting stack...")
    stack, is_app_router = detect_stack(workdir)
    print(f"✅ Stack: {stack}, App Router: {is_app_router}")

    print("📄 Parsing CSV...")
    rows = parse_csv(CSV_PATH)
    if not rows:
        print("⚠️  No rows found in CSV — nothing to optimize.")
        sys.exit(0)
    print(f"✅ Found {len(rows)} URLs")

    ctx = Context(site_base=SITE_BASE, repo_dir=workdir, stack=stack, is_app_router=is_app_router)
    ctx.url_to_file = map_urls_to_files(rows, ctx)

    if stack != "next":
        print(f"⚠️  Stack '{stack}' has no per-page fixer wired up yet — only site-level technical files will be written.")

    if "onpage" in agents and stack == "next":
        run_onpage_agent(rows, ctx)
        commit_and_push(ctx, "chore(seo): on-page optimization (title, meta, headings, schema, OG/Twitter, alt text, internal links)")

    if "technical" in agents:
        run_technical_agent(rows, ctx, locales=locales)
        commit_and_push(ctx, "chore(seo): technical optimization (robots.txt, sitemap, canonical, noindex, hreflang)")

    print("\n" + "=" * 50)
    print("✅ Autonomous optimization run complete.")


if __name__ == "__main__":
    main()
