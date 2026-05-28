# Job Apply Agent

Upload your resume and get AI-matched job listings in India based on your skills, experience, and target roles.

## Setup

1. Create a virtual environment and install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Add API keys to `.env`:

```
OPENAI_API_KEY=your_key
TAVILY_API_KEY=your_key
OPENAI_MODEL=gpt-4o
```

Optional: set `OPENAI_MODEL=gpt-5.4` if your account supports it.

## Run the web UI

```bash
# Either command prints each request in the terminal
python api.py
# or
uvicorn api:app --reload --host 0.0.0.0 --port 8000 --log-level info
```

**Port already in use?** Free port 8000 or use another port:

```bash
# Free port 8000 (macOS)
lsof -ti :8000 | xargs kill

# Run on port 8001 instead
PORT=8001 python api.py
# or
uvicorn api:app --reload --host 0.0.0.0 --port 8001 --log-level info
```

Open [http://localhost:8000](http://localhost:8000) (or `:8001` if you changed the port), upload a PDF resume, and click **Find relevant jobs**.

## CLI (optional)

```bash
python agent.py
```

Uses the bundled sample resume in the project root.

## API

- `GET /api/health` — health check
- `POST /api/search-jobs` — multipart form field `resume` (PDF), returns candidate profile and job list

## Deploy

### Important: Vercel vs long-running API

`POST /api/search-jobs` runs PDF parsing + OpenAI + Tavily and often takes **1–3+ minutes**.

| Platform | Good fit? | Why |
|----------|-----------|-----|
| **Render / Railway / Fly.io** | Yes | Long HTTP requests, always-on or container |
| **Vercel** | Risky | Serverless **timeout** (Hobby ~10s; Pro up to 300s with config). May fail mid-search. |

**Recommended:** deploy the full app on [Render](https://render.com) (free tier) or Railway. Use Vercel only for the static UI if you host the API elsewhere.

---

### Option A — Vercel (frontend + API, with limits)

1. Push the repo to GitHub (`.env` is gitignored; never commit keys).

2. Import the project on [vercel.com](https://vercel.com) → **Add New Project** → select the repo.

3. **Environment variables** (Project → Settings → Environment Variables):

   | Name | Value |
   |------|--------|
   | `OPENAI_API_KEY` | your key |
   | `TAVILY_API_KEY` | your key |
   | `OPENAI_MODEL` | e.g. `gpt-4o` |

4. Deploy. Vercel uses `vercel.json` and `api/index.py` in this repo.

5. Open your `*.vercel.app` URL (same origin for UI + `/api/*`).

**If search-jobs times out:** upgrade to Vercel Pro (for `maxDuration: 300` in `vercel.json`) or move the API to Render (Option B).

**CLI deploy:**

```bash
npm i -g vercel
vercel login
vercel          # preview
vercel --prod   # production
```

---

### Option B — Render (recommended for this app)

1. [render.com](https://render.com) → **New → Web Service** → connect GitHub repo.

2. Settings:

   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`

3. Add env vars: `OPENAI_API_KEY`, `TAVILY_API_KEY`, `OPENAI_MODEL`.

4. Deploy and open the Render URL.

---

### Option C — Vercel UI + API on Render

1. Deploy API on Render (Option B). Note the URL, e.g. `https://job-apply-agent.onrender.com`.

2. In `frontend/app.js`, set:

   ```js
   const API_BASE = "https://job-apply-agent.onrender.com";
   ```

3. Deploy only `frontend/` to Vercel as a static site, or use any static host.

Enable CORS on the API if the UI and API are on different domains (already allows `*` in `api.py`).
