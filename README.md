# Autonomous Website Optimizer — On-page + Technical Agents

Your original single script is split into two independent agents that share
common plumbing (`common.py`). Off-page is **not** built yet, per your call —
`orchestrator.py` has a clear slot to plug it in later.

## Files

| File | What it owns |
|---|---|
| `common.py` | Git clone/push, stack detection, URL→file mapping, CSV parsing, OpenAI call, safe file writes. No optimization logic. |
| `onpage_agent.py` | 🟢 Title, meta description, H1/H2/H3 structure notes, keywords, content clarity notes, internal link suggestions, alt-text flags, JSON-LD (WebPage w/ `dateModified` + entity typing, BreadcrumbList, FAQ, HowTo), Open Graph, Twitter tags, answer-first summary, extractability notes — maps to AEO parameters 9, 11, 12, 13, 14. |
| `technical_agent.py` | 🔵 `robots.txt` (incl. explicit AI-bot allowlist), `llms.txt`, `sitemap.xml` + Next.js `sitemap.js`, canonical tags, noindex handling, hreflang, orphan-page + client-component detection, a redirects() scaffold check, and `TECHNICAL_SEO_REPORT.md` for things that need a live crawl (HTTPS, page speed, Core Web Vitals, 404/5xx) — maps to AEO parameters 7, 8, 10. |
| `orchestrator.py` | Clones the repo once, runs whichever agents you ask for, commits **each agent separately** (clean git history), pushes. |
| `api.py` | FastAPI wrapper — a form where you pick "on-page / technical / both", upload your CSV, and it runs + pushes. |

## Why two separate commits per run

So your git history says exactly what changed and why — `chore(seo): on-page optimization ...` vs `chore(seo): technical optimization ...` — instead of one giant "SEO stuff" commit.

## Honest limitation

Page speed, Core Web Vitals, HTTPS validity, real 404/5xx codes, and JS-rendering
correctness **cannot be fixed by editing source files without running the site**.
The technical agent writes a `TECHNICAL_SEO_REPORT.md` flagging these instead of
faking a fix — pair it with Lighthouse/PageSpeed Insights/a live crawler for those.

## Run it

```bash
export OPENAI_API_KEY=...
export GITHUB_TOKEN=...
export REPO_URL=https://github.com/you/yoursite
export SITE_BASE=https://yoursite.com
export CSV_PATH=seo_report.csv
export AGENTS=onpage,technical   # or just "onpage" / "technical"
python orchestrator.py
```

Or run the API and use the form:

```bash
pip install fastapi uvicorn python-dotenv openai==0.28.1
python api.py   # http://localhost:7860
```

## Next: off-page agent (not built yet)

When you're ready, `off_page_agent.py` would own: backlink profile checks,
citation/NAP consistency, social profile linking, brand-mention monitoring —
none of which are fixed by editing the repo, so it'll mostly be a
research + outreach-recommendation agent rather than a file-writer.
