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
