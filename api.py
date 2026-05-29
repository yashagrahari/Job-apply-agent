import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from job_sources import get_configured_ats_boards, normalize_apply_link
from pydantic import BaseModel

from agent import (
    OPENAI_MODEL,
    PROJECT_ROOT,
    CandidateInfo,
    JobDetails,
    parse_resume_pdf,
    prepare_application_package,
    process_uploaded_bytes,
)


class PrepareApplicationRequest(BaseModel):
    candidate: CandidateInfo
    job: JobDetails

FRONTEND_DIR = PROJECT_ROOT / "frontend"
ALLOWED_EXTENSIONS = {".pdf"}
MAX_BYTES = 10 * 1024 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger("job-apply-api")

app = FastAPI(title="Job Apply Agent", version="1.0.0")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    path = request.url.path
    logger.info("→ %s %s", request.method, path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("✗ %s %s failed", request.method, path)
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "← %s %s %s (%.0fms)",
        request.method,
        path,
        response.status_code,
        duration_ms,
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _read_resume_upload(resume: UploadFile) -> tuple[bytes, str]:
    """Read and validate the multipart `resume` field from the client."""
    if not resume.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    suffix = Path(resume.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only PDF resumes are supported.",
        )

    logger.info("upload: reading %s", resume.filename)
    content = await resume.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File must be under 10 MB.")

    logger.info("upload: %s (%d bytes)", resume.filename, len(content))
    return content, resume.filename


@app.get("/api/health")
def health():
    ats_boards = get_configured_ats_boards()
    return {
        "status": "ok",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY")),
        "ats_source_count": len(ats_boards),
        "ats_sources": [f"{board.provider}:{board.slug}" for board in ats_boards],
        "model": OPENAI_MODEL,
        "env_file": str(PROJECT_ROOT / ".env"),
    }


@app.post("/api/parse-resume")
async def parse_resume(resume: UploadFile = File(...)):
    """Validate upload and PDF parsing only (no OpenAI call)."""
    content, filename = await _read_resume_upload(resume)

    def _parse():
        suffix = Path(filename).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            path = tmp.name
        try:
            return parse_resume_pdf(path)
        finally:
            os.unlink(path)

    try:
        text = await asyncio.to_thread(_parse)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "filename": filename,
        "chars": len(text),
        "preview": text[:500],
    }


def _job_to_dict(job) -> dict:
    link = normalize_apply_link(job.apply_link)
    return {
        "platform": job.platform,
        "role": job.role,
        "Exp": job.Exp,
        "contact_info": job.contact_info,
        "location": job.location,
        "apply_link": link,
    }


@app.post("/api/search-jobs")
async def search_jobs(resume: UploadFile = File(...)):
    content, filename = await _read_resume_upload(resume)
    logger.info("search-jobs: running pipeline for %s", filename)

    try:
        result = await asyncio.to_thread(process_uploaded_bytes, content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process resume: {exc}",
        ) from exc

    jobs = [_job_to_dict(job) for job in result.jobs]
    links = [j["apply_link"] for j in jobs if j["apply_link"]]
    logger.info("search-jobs: done — %d jobs, %d links", len(jobs), len(links))

    return {
        "candidate": result.candidate.model_dump(),
        "jobs": jobs,
        "links": links,
    }


@app.post("/api/prepare-application")
async def prepare_application(body: PrepareApplicationRequest):
    """Generate cover letter, form answers, and apply checklist for one job."""
    logger.info(
        "prepare-application: %s @ %s",
        body.job.role,
        body.job.platform,
    )
    try:
        package = await asyncio.to_thread(
            prepare_application_package, body.candidate, body.job
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to prepare application: {exc}",
        ) from exc

    return {
        "job": _job_to_dict(body.job),
        "application": package.model_dump(),
    }


@app.get("/")
def serve_index():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not found.")
    return FileResponse(index)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
        access_log=True,
    )
