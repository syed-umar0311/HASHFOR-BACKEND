from dotenv import load_dotenv
load_dotenv()

import os
import sys
import logging
import tempfile
import traceback
import subprocess

from fastapi import FastAPI, Form, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

if not os.getenv("OPENAI_API_KEY"):
    logger.error("❌ OPENAI_API_KEY is MISSING! Add it to .env file")

app = FastAPI(
    title="Autonomous Website Optimizer",
    description="On-page + technical SEO/GEO/AEO agents that clone, fix, and push to GitHub",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <!DOCTYPE html><html><head><title>Autonomous Website Optimizer</title>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
      body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:800px;margin:0 auto;padding:20px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;color:#333}
      .container{background:#fff;padding:30px;border-radius:15px;box-shadow:0 10px 30px rgba(0,0,0,.2)}
      h1{color:#2c3e50;text-align:center}.subtitle{text-align:center;color:#7f8c8d;margin-bottom:30px}
      .form-group{margin-bottom:20px}label{display:block;margin-bottom:8px;font-weight:600}
      input,select{width:100%;padding:12px;border:2px solid #e1e8ed;border-radius:8px;font-size:16px;box-sizing:border-box}
      button{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:15px 30px;border:none;border-radius:8px;cursor:pointer;font-size:16px;font-weight:600;width:100%}
      .output{background:#f8f9fa;padding:20px;border-radius:8px;margin-top:30px;white-space:pre-wrap;font-family:monospace;font-size:14px;border-left:4px solid #667eea;display:none;max-height:400px;overflow-y:auto}
      .success{border-left-color:#28a745}.error{border-left-color:#dc3545}
      .feature-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin:20px 0}
      .feature{background:#f8f9fa;padding:15px;border-radius:8px;text-align:center;border-left:4px solid #667eea}
    </style></head><body><div class="container">
      <h1>🚀 Autonomous Website Optimizer</h1>
      <div class="subtitle">On-page + Technical SEO/GEO/AEO agents</div>
      <div class="feature-list">
        <div class="feature">🟢 On-page (title, meta, schema, alt text, internal links)</div>
        <div class="feature">🔵 Technical (robots.txt, sitemap, canonical, hreflang)</div>
      </div>
      <form id="seoForm">
        <div class="form-group"><label>🤖 Agents to run</label>
          <select id="agents" name="agents">
            <option value="onpage,technical">Both (on-page + technical)</option>
            <option value="onpage">On-page only</option>
            <option value="technical">Technical only</option>
          </select>
        </div>
        <div class="form-group"><label>📁 Repository URL</label><input type="url" id="repo_url" name="repo_url" required></div>
        <div class="form-group"><label>🌐 Site Base URL</label><input type="url" id="site_base" name="site_base" required></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:15px">
          <div class="form-group"><label>🌿 Branch</label><input type="text" id="branch" name="branch" value="main"></div>
          <div class="form-group"><label>👤 Git Username</label><input type="text" id="git_username" name="git_username" value="SEO-Auto-Fix-Bot"></div>
        </div>
        <div class="form-group"><label>📧 Git Email</label><input type="email" id="git_email" name="git_email" value="seo-bot@example.com"></div>
        <div class="form-group"><label>🔑 GitHub Token</label><input type="password" id="github_token" name="github_token" required></div>
        <div class="form-group"><label>📊 SEO Report CSV</label><input type="file" id="csv_file" name="csv_file" accept=".csv" required></div>
        <button type="submit">🚀 Run Optimization</button>
      </form>
      <div id="output" class="output"></div>
    </div>
    <script>
      document.getElementById('seoForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const output = document.getElementById('output');
        output.style.display='block'; output.className='output';
        output.textContent='🚀 Running... this can take a few minutes.';
        const formData = new FormData(e.target);
        try {
          const response = await fetch('/run', { method: 'POST', body: formData });
          const result = await response.json();
          if (!response.ok) { output.className='output error'; output.textContent='❌ '+(result.detail||'Unknown error'); return; }
          output.className = result.success ? 'output success' : 'output error';
          output.textContent = `Return Code: ${result.returncode}\\n\\nSTDOUT:\\n${result.stdout}\\n\\nSTDERR:\\n${result.stderr}`;
        } catch (err) { output.className='output error'; output.textContent='❌ Network error: '+err.message; }
      });
    </script></body></html>
    """


@app.get("/health")
async def health_check():
    return JSONResponse({"status": "healthy"})


@app.post("/run")
async def run_optimizer(
    background_tasks: BackgroundTasks,
    github_token: str = Form(...),
    repo_url: str = Form(...),
    branch: str = Form("main"),
    site_base: str = Form(...),
    git_username: str = Form("SEO-Auto-Fix-Bot"),
    git_email: str = Form("seo-bot@example.com"),
    agents: str = Form("onpage,technical"),
    locales: str = Form(""),
    csv_file: UploadFile = Form(...),
):
    logger.info(f"Received optimization request: agents={agents}")
    csv_path = None
    try:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set in environment.")
        if not csv_file or not csv_file.filename.endswith(".csv"):
            raise HTTPException(status_code=400, detail="CSV file is required (must be .csv)")

        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as f:
            content = await csv_file.read()
            if not content:
                raise HTTPException(status_code=400, detail="CSV file is empty")
            f.write(content)
            csv_path = f.name

        env = os.environ.copy()
        env.update({
            "GITHUB_TOKEN": github_token,
            "REPO_URL": repo_url,
            "BRANCH": branch,
            "SITE_BASE": site_base,
            "GIT_USERNAME": git_username,
            "GIT_EMAIL": git_email,
            "CSV_PATH": csv_path,
            "AGENTS": agents,
            "LOCALES": locales,
            "PYTHONIOENCODING": "utf-8",
        })

        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orchestrator.py")
        proc = subprocess.run(
            [sys.executable, script_path], env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=600,
        )

        if csv_path and os.path.exists(csv_path):
            background_tasks.add_task(cleanup_file, csv_path)

        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "success": proc.returncode == 0}

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        if csv_path and os.path.exists(csv_path):
            background_tasks.add_task(cleanup_file, csv_path)
        raise HTTPException(status_code=500, detail="Process timed out after 10 minutes")
    except Exception as e:
        logger.error(traceback.format_exc())
        if csv_path and os.path.exists(csv_path):
            background_tasks.add_task(cleanup_file, csv_path)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


async def cleanup_file(file_path: str):
    try:
        if os.path.exists(file_path):
            os.unlink(file_path)
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")


if __name__ == "__main__":
    # Render sets $PORT dynamically; the Dockerfile's CMD reads it directly,
    # but this path (running `python api.py` locally) needs the same fallback.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 7860)))
