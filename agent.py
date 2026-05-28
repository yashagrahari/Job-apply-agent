import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_tavily import TavilySearch
from pypdf import PdfReader
from pydantic import BaseModel, Field
from typing import List, Optional

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.resolve()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")

if "OPENAI_API_KEY" not in os.environ and __name__ == "__main__":
    os.environ["OPENAI_API_KEY"] = input("Enter your OpenAI API key: ")

tavily_search = TavilySearch(max_results=10, topic="general")


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
    tools=[tavily_search],
    system_prompt=(
        "You are a helpful assistant. You help candidates fetch the latest and "
        "relevant jobs in India based on their skills, experience, and target roles. "
        "You have access to search tools. Use them to fetch the latest relevant job postings."
    ),
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


def search_relevant_jobs(candidate: CandidateInfo) -> List[JobDetails]:
    """Search for jobs matching the candidate profile."""
    details = (
        f"roles={candidate.roles} skills={candidate.skills} Exp={candidate.Exp}"
    )
    result = job_agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"Find relevant jobs in India with these details: {details}",
                }
            ]
        }
    )
    structured = result["structured_response"]
    if isinstance(structured, RelevantJobs):
        return structured.jobs
    return structured.jobs if hasattr(structured, "jobs") else structured["jobs"]


def process_resume_file(file_path: str | Path) -> JobSearchResult:
    """Full pipeline: parse PDF, extract profile, search jobs."""
    resume_text = parse_resume_pdf(file_path)
    candidate = extract_candidate_info(resume_text)
    jobs = search_relevant_jobs(candidate)
    return JobSearchResult(candidate=candidate, jobs=jobs)


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
