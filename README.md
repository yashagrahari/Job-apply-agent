# Job Apply Agent

Upload your resume and get AI-matched active job listings in India based on your skills, experience, and target roles.

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

Optional ATS source tuning:

```
# Provider slugs or full ATS URLs. If omitted, a small default seed list is used.
ATS_BOARD_SOURCES=greenhouse:stripe,lever:postman,ashby:openai

# Use "*" or "any" to disable the default India/remote/APAC location filter.
JOB_LOCATION_KEYWORDS=india,bangalore,bengaluru,hyderabad,pune,mumbai,delhi,gurgaon,gurugram,noida,chennai,remote,apac,asia,global,worldwide,anywhere
JOB_SEARCH_TARGET_RESULTS=12
```

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

## Job sourcing strategy

The search pipeline now uses a hybrid source model:

1. **ATS APIs first** — fetch active postings from Greenhouse, Lever, and Ashby board endpoints.
2. **Tavily/search fallback** — fill gaps and discover additional ATS boards when configured sources are not enough.
3. **Live URL validation last** — remove known-dead links before jobs are returned.

Greenhouse, Lever, and Ashby boards are per-company, so coverage depends on the board slugs in `ATS_BOARD_SOURCES`. The health endpoint returns the active source list under `ats_sources`.

## CLI (optional)

```bash
python agent.py
```

Uses the bundled sample resume in the project root.

## API

- `GET /api/health` — health check, including ATS source count/list
- `POST /api/search-jobs` — multipart form field `resume` (PDF), returns candidate profile and job list
- `POST /api/prepare-application` — JSON `{ "candidate", "job" }`, returns cover letter, form answers, and apply checklist

## Apply for jobs (how it works)

### What you have now (Phase 1 — implemented)

1. **Find jobs** → `POST /api/search-jobs`
   - ATS APIs are queried first for active postings
   - Tavily fills gaps when ATS results are sparse
   - Job links are validated before returning
2. **Prepare application** on each card → `POST /api/prepare-application`
   - Tailored cover letter
   - “Why you fit” bullets
   - Answers for common form fields
   - Checklist to finish on the employer site
3. **Open apply page** → paste materials and upload resume manually

This is the reliable approach: every company uses a different form (LinkedIn, Greenhouse, Lever, Naukri, etc.).

### Phase 2 — semi-automatic (browser agent, local)

To actually click “Submit” on forms:

| Piece | Tool |
|-------|------|
| Browser control | [Playwright](https://playwright.dev/python/) |
| Form understanding | LLM + page snapshot (like your LangChain agents) |
| Auth | User logs in once; save `storage_state` to a file |

Run a **separate worker** (not Vercel serverless):

```bash
pip install playwright
playwright install chromium
# python apply_worker.py --url "https://jobs.lever.co/..." --profile profile.json
```

Start with **one ATS** (e.g. Greenhouse/Lever URLs). Your code already flags those in `assess_auto_apply_feasibility()`.

### Phase 3 — full product

- User profile store (name, email, phone, work history, resume path)
- Application queue + status (`draft` → `prepared` → `submitted` → `failed`)
- Per-platform adapters (`greenhouse.py`, `lever.py`, …)
- Human-in-the-loop for captcha / OTP (pause agent, user continues, resume)

### Limits (important)

- **LinkedIn / Indeed / Naukri**: login + anti-bot → manual or official APIs only
- **Legal / ToS**: automate only where allowed; prefer assist + human submit
- **Vercel**: cannot run Playwright; use Render/Fly for the apply worker
