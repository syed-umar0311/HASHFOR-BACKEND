# Dockerfile for Render deployment of the SEO optimizer backend (api.py + agents)
FROM python:3.11-slim

# git is required — orchestrator.py shells out to `git clone/config/add/commit/push`
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Render assigns a dynamic port via $PORT — do NOT hardcode 7860 here.
# Shell form so ${PORT} actually expands; falls back to 7860 for local `docker run`.
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-7860}"]
