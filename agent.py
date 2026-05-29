import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from job_sources import (
    SourceJob,
    extract_ats_boards_from_urls,
    is_live_job_url,
    normalize_apply_link,
    search_ats_jobs,
)
from langchain.agents import create_agent
from langchain_tavily import TavilySearch
from pypdf import PdfReader
from pydantic import BaseModel, Field
from typing import List, Optional

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.resolve()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


JOB_SEARCH_TARGET_RESULTS = _env_int("JOB_SEARCH_TARGET_RESULTS", 50)

if "OPENAI_API_KEY" not in os.environ and __name__ == "__main__":
    os.environ["OPENAI_API_KEY"] = input("Enter your OpenAI API key: ")

tavily_search = (
    TavilySearch(max_results=10, topic="general")
    if os.getenv("TAVILY_API_KEY")
    else None
)


class CandidateInfo(BaseModel):
    """Candidate information extracted from resume."""

    roles: List[str] = Field(description="Roles the candidate should target")
    skills: List[str] = Field(description="Relevant skills of the candidate")
    Exp: int = Field(description="Years of experience of the candidate")


class JobDetails(BaseModel):
    """A single job posting."""

    platform: str = Field(description="Platform on which job is posted")
    role: str = Field(description="Role of the job")
    Exp: int = Field(description="Experience required for the job")
    contact_info: Optional[str] = Field(
        default=None,
        description="Contact info of hiring manager if available",
    )
    location: str
    apply_link: str


class RelevantJobs(BaseModel):
    """List of relevant job postings."""

    jobs: List[JobDetails]


class JobSearchResult(BaseModel):
    """Full pipeline result returned to the API."""

    candidate: CandidateInfo
    jobs: List[JobDetails]


class ApplicationPackage(BaseModel):
    """Tailored materials to help the candidate apply for one job."""

    cover_letter: str = Field(description="Short tailored cover letter, under 250 words")
    why_you_fit: List[str] = Field(
        description="3-5 bullet points matching resume to this role"
    )
    common_answers: dict[str, str] = Field(
        description="Answers for typical form fields, e.g. years of experience, notice period"
    )
    apply_checklist: List[str] = Field(
        description="Step-by-step checklist for completing the application on the job site"
    )
    auto_apply_feasible: bool = Field(
        description="True only if this URL type can realistically be automated later"
    )
    auto_apply_note: str = Field(
        description="Why full auto-apply is or is not feasible for this posting"
    )


extract_agent = create_agent(
    f"openai:{OPENAI_MODEL}",
    response_format=CandidateInfo,
    system_prompt=(
        "You are a helpful assistant. You help in reading the candidate's resume "
        "and extracting relevant experience, skills, and job roles they should target."
    ),
)

job_agent = create_agent(
    f"openai:{OPENAI_MODEL}",
    response_format=RelevantJobs,
    tools=[tavily_search] if tavily_search else [],
    system_prompt=(
        "You are a fallback job-search assistant. Find active, relevant jobs in India "
        "based on skills, experience, and target roles. Prefer direct employer/ATS URLs "
        "(Greenhouse, Lever, Ashby, Workable, company career pages) over job-board mirrors. "
        "Do not invent employers, roles, or links; return only postings that appear active."
    ),
)

apply_agent = create_agent(
    f"openai:{OPENAI_MODEL}",
    response_format=ApplicationPackage,
    system_prompt=(
        "You help candidates apply for jobs in India. Given their profile and one job posting, "
        "write a concise tailored cover letter, bullet points on fit, and short answers for "
        "common application form questions (experience, notice period, expected CTC if relevant, "
        "why this role). Include a practical checklist for submitting on the employer site. "
        "Be honest and professional; do not invent employers or experience not implied by the profile."
    ),
)


def assess_auto_apply_feasibility(apply_link: str) -> tuple[bool, str]:
    """Heuristic: which job URLs might support browser automation later."""
    url = (apply_link or "").lower()
    if not url:
        return False, "No apply link — apply manually via the job board or contact."

    if any(host in url for host in ("linkedin.com", "indeed.com", "naukri.com", "foundit.in")):
        return (
            False,
            "Large job boards require login and often block bots — use their site manually.",
        )
    if any(
        host in url
        for host in (
            "greenhouse.io",
            "jobs.lever.co",
            "lever.co",
            "ashbyhq.com",
            "workable.com",
        )
    ):
        return (
            True,
            "Standard ATS form (Greenhouse/Lever/Ashby/Workable) — a Playwright agent can target these next.",
        )
    return (
        False,
        "Custom company site — semi-automated apply needs a per-site browser script.",
    )


def parse_resume_pdf(file_path: str | Path) -> str:
    """Extract text content from a PDF resume."""
    path = Path(file_path)
    reader = PdfReader(str(path.resolve()))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise ValueError(
            "Could not extract text from the PDF. "
            "Try a text-based PDF (not a scanned image)."
        )
    return text


def extract_candidate_info(resume_text: str) -> CandidateInfo:
    """Use the LLM to extract roles, skills, and experience from resume text."""
    result = extract_agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Here is my resume. Extract my target roles, skills, "
                        f"and years of experience:\n\n{resume_text}"
                    ),
                }
            ]
        }
    )
    return result["structured_response"]


def _source_job_to_details(job: SourceJob) -> JobDetails:
    return JobDetails(
        platform=job.platform,
        role=job.role,
        Exp=max(0, job.experience),
        contact_info=job.contact_info,
        location=job.location,
        apply_link=normalize_apply_link(job.apply_link),
    )


def _search_jobs_with_tavily(candidate: CandidateInfo) -> List[JobDetails]:
    """Fallback: use Tavily/search when ATS sources do not produce enough jobs."""
    if tavily_search is None:
        return []

    details = (
        f"roles={candidate.roles} skills={candidate.skills} Exp={candidate.Exp}"
    )
    try:
        result = job_agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Find relevant active jobs in India with these details. "
                            "Return direct apply URLs and prefer employer ATS links "
                            f"when available: {details}"
                        ),
                    }
                ]
            }
        )
    except Exception:
        return []

    structured = result["structured_response"]
    jobs = structured.jobs if isinstance(structured, RelevantJobs) else (
        structured.jobs if hasattr(structured, "jobs") else structured["jobs"]
    )
    for job in jobs:
        job.apply_link = normalize_apply_link(job.apply_link)
        if not job.platform:
            job.platform = "Search fallback"
    return jobs


def _dedupe_jobs(jobs: List[JobDetails]) -> List[JobDetails]:
    seen: set[str] = set()
    unique: list[JobDetails] = []
    for job in jobs:
        link = normalize_apply_link(job.apply_link)
        key = link.lower().rstrip("/") if link else (
            f"{job.platform}|{job.role}|{job.location}".lower()
        )
        if key in seen:
            continue
        seen.add(key)
        job.apply_link = link
        unique.append(job)
    return unique


def _validate_live_jobs(jobs: List[JobDetails]) -> List[JobDetails]:
    max_checks = _env_int("JOB_URL_VALIDATION_MAX_CHECKS", 30)
    checked = 0
    live: list[JobDetails] = []

    for job in jobs:
        job.apply_link = normalize_apply_link(job.apply_link)
        if not job.apply_link:
            continue

        if checked < max_checks:
            checked += 1
            if not is_live_job_url(job.apply_link):
                continue

        live.append(job)
        if len(live) >= JOB_SEARCH_TARGET_RESULTS:
            break

    return live


def search_relevant_jobs(candidate: CandidateInfo) -> List[JobDetails]:
    """Search active jobs: ATS APIs first, Tavily fallback, URL validation last."""
    primary_ats_jobs = search_ats_jobs(
        roles=candidate.roles,
        skills=candidate.skills,
        years_exp=candidate.Exp,
        limit=JOB_SEARCH_TARGET_RESULTS * 3,
    )
    jobs = [_source_job_to_details(job) for job in primary_ats_jobs]

    fallback_jobs: List[JobDetails] = []
    if len(_dedupe_jobs(jobs)) < JOB_SEARCH_TARGET_RESULTS:
        fallback_jobs = _search_jobs_with_tavily(candidate)
        discovered_boards = extract_ats_boards_from_urls(
            job.apply_link for job in fallback_jobs
        )
        if discovered_boards:
            discovered_ats_jobs = search_ats_jobs(
                roles=candidate.roles,
                skills=candidate.skills,
                years_exp=candidate.Exp,
                boards=discovered_boards,
                limit=JOB_SEARCH_TARGET_RESULTS * 2,
            )
            jobs.extend(_source_job_to_details(job) for job in discovered_ats_jobs)

    if len(_dedupe_jobs(jobs)) < JOB_SEARCH_TARGET_RESULTS:
        jobs.extend(fallback_jobs)

    return _validate_live_jobs(_dedupe_jobs(jobs))


def process_resume_file(file_path: str | Path) -> JobSearchResult:
    """Full pipeline: parse PDF, extract profile, search jobs."""
    resume_text = parse_resume_pdf(file_path)
    candidate = extract_candidate_info(resume_text)
    jobs = search_relevant_jobs(candidate)
    return JobSearchResult(candidate=candidate, jobs=jobs)


def prepare_application_package(
    candidate: CandidateInfo, job: JobDetails
) -> ApplicationPackage:
    """Generate cover letter, answers, and apply checklist for one job."""
    job_desc = (
        f"role={job.role} platform={job.platform} location={job.location} "
        f"required_exp={job.Exp} apply_link={job.apply_link} contact={job.contact_info}"
    )
    profile = (
        f"roles={candidate.roles} skills={candidate.skills} years_exp={candidate.Exp}"
    )
    result = apply_agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Prepare application materials for this candidate applying to this job.\n\n"
                        f"Candidate profile: {profile}\n\nJob: {job_desc}"
                    ),
                }
            ]
        }
    )
    package = result["structured_response"]
    feasible, note = assess_auto_apply_feasibility(job.apply_link)
    package.auto_apply_feasible = feasible
    package.auto_apply_note = note
    return package


def process_uploaded_bytes(content: bytes, filename: str = "resume.pdf") -> JobSearchResult:
    """Save uploaded bytes to a temp file and run the pipeline."""
    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return process_resume_file(tmp_path)
    finally:
        os.unlink(tmp_path)


def main():
    default_resume = Path(__file__).parent / "Yash_Agrahari_Backend_Engineer_Resume.pdf.pdf"
    result = process_resume_file(default_resume)
    print("Candidate:", result.candidate)
    print("Jobs found:", len(result.jobs))
    for job in result.jobs:
        print(f"  - {job.role} @ {job.platform} ({job.location})")


if __name__ == "__main__":
    main()
