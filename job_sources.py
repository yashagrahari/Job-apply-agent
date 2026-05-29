from __future__ import annotations

import concurrent.futures
import html
from http.client import InvalidURL
import json
import os
import re
import socket
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_ATS_BOARD_SOURCES = (
    "greenhouse:airbnb",
    "greenhouse:databricks",
    "greenhouse:mongodb",
    "greenhouse:stripe",
    "greenhouse:twilio",
    "lever:postman",
    "lever:scaleai",
    "lever:zapier",
    "ashby:anthropic",
    "ashby:cursor",
    "ashby:linear",
    "ashby:notion",
    "ashby:openai",
    "ashby:ramp",
    "ashby:replicate",
    "ashby:vercel",
)

DEFAULT_LOCATION_KEYWORDS = (
    "india",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "mumbai",
    "delhi",
    "gurgaon",
    "gurugram",
    "noida",
    "chennai",
    "kolkata",
    "ahmedabad",
    "apac",
    "asia",
    "global",
    "worldwide",
    "anywhere",
    "remote",
)

REMOTE_RESTRICTED_TERMS = (
    "united states",
    " usa",
    " u.s.",
    " us ",
    "canada",
    "europe",
    "emea",
    "united kingdom",
    " uk",
    "germany",
    "france",
    "poland",
    "netherlands",
    "latin america",
    "americas",
)

ROLE_STOPWORDS = {
    "and",
    "developer",
    "engineer",
    "engineering",
    "lead",
    "manager",
    "role",
    "senior",
    "software",
    "specialist",
}

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ATSBoard:
    provider: str
    slug: str


@dataclass
class SourceJob:
    platform: str
    role: str
    experience: int
    contact_info: str | None
    location: str
    apply_link: str
    description: str = ""
    source_type: str = "ats"
    score: float = 0.0


def get_configured_ats_boards() -> list[ATSBoard]:
    """Return ATS boards from env, falling back to a small curated seed list."""
    raw = (
        os.getenv("ATS_BOARD_SOURCES")
        or os.getenv("ATS_COMPANY_BOARDS")
        or os.getenv("ATS_SOURCES")
        or ""
    ).strip()

    if raw.lower() in {"none", "off", "disabled"}:
        return []

    entries = _split_csvish(raw) if raw else list(DEFAULT_ATS_BOARD_SOURCES)
    return _dedupe_boards(parse_ats_board(entry) for entry in entries)


def parse_ats_board(value: str) -> ATSBoard | None:
    """Parse provider:slug strings or common ATS job-board URLs."""
    value = value.strip()
    if not value:
        return None

    if "://" in value or "." in value:
        board = _parse_ats_board_url(value)
        if board:
            return board

    normalized = value.replace("/", ":")
    parts = [part.strip() for part in normalized.split(":") if part.strip()]
    if len(parts) < 2:
        return None

    provider = _normalize_provider(parts[0])
    slug = parts[-1]
    if not provider or not slug:
        return None
    return ATSBoard(provider=provider, slug=slug)


def extract_ats_boards_from_urls(urls: Iterable[str]) -> list[ATSBoard]:
    return _dedupe_boards(
        board
        for url in urls
        if url
        for board in [parse_ats_board(url)]
        if board is not None
    )


def search_ats_jobs(
    roles: Iterable[str],
    skills: Iterable[str],
    years_exp: int,
    boards: Iterable[ATSBoard] | None = None,
    limit: int | None = None,
) -> list[SourceJob]:
    """Fetch active jobs from ATS APIs, then score and filter locally."""
    board_list = list(boards) if boards is not None else get_configured_ats_boards()
    if not board_list:
        return []

    candidate_limit = limit or _env_int("ATS_CANDIDATE_LIMIT", 36)
    max_workers = max(1, min(len(board_list), _env_int("ATS_MAX_WORKERS", 8)))
    fetched: list[SourceJob] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_board_jobs, board) for board in board_list]
        for future in concurrent.futures.as_completed(futures):
            try:
                fetched.extend(future.result())
            except Exception:
                continue

    scored: list[SourceJob] = []
    for job in _dedupe_source_jobs(fetched):
        score = _score_job(job, roles, skills)
        job.score = score
        if _job_matches(job, score, years_exp):
            scored.append(job)

    scored.sort(key=lambda job: (-job.score, job.platform.lower(), job.role.lower()))
    return scored[:candidate_limit]


def normalize_apply_link(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


def is_live_job_url(url: str) -> bool:
    """Conservatively remove known-dead links without dropping bot-blocked pages."""
    url = normalize_apply_link(url)
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or any(char.isspace() for char in parsed.netloc)
    ):
        return False

    timeout = _env_float("JOB_URL_VALIDATION_TIMEOUT_SECONDS", 4.0)
    for method in ("HEAD", "GET"):
        try:
            request = Request(
                url,
                method=method,
                headers={
                    "User-Agent": _user_agent(),
                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.status < 400
        except HTTPError as exc:
            if method == "HEAD" and exc.code in {405, 501}:
                continue
            if exc.code in {401, 403, 429}:
                return True
            if exc.code in {400, 404, 410}:
                return False
            return exc.code < 500
        except (InvalidURL, ValueError):
            return False
        except (TimeoutError, socket.timeout, URLError):
            # Network blips and bot shields are common on ATS pages. Keep unless
            # the server explicitly tells us the posting is gone.
            return True

    return True


def _fetch_board_jobs(board: ATSBoard) -> list[SourceJob]:
    if board.provider == "greenhouse":
        return _fetch_greenhouse_jobs(board.slug)
    if board.provider in {"lever", "lever-eu"}:
        return _fetch_lever_jobs(board.slug, eu=board.provider == "lever-eu")
    if board.provider == "ashby":
        return _fetch_ashby_jobs(board.slug)
    return []


def _fetch_greenhouse_jobs(slug: str) -> list[SourceJob]:
    url = (
        "https://boards-api.greenhouse.io/v1/boards/"
        f"{quote(slug, safe='')}/jobs?content=true"
    )
    data = _get_json(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    results: list[SourceJob] = []
    for item in jobs[: _env_int("ATS_MAX_JOBS_PER_BOARD", 5)]:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
        apply_link = normalize_apply_link(_clean_text(item.get("absolute_url")))
        if not title or not apply_link:
            continue
        description = _clean_html(item.get("content"))
        location = _greenhouse_location(item)
        results.append(
            SourceJob(
                platform=f"Greenhouse: {slug}",
                role=title,
                experience=_infer_experience(f"{title}\n{description}"),
                contact_info=None,
                location=location,
                apply_link=apply_link,
                description=description,
            )
        )
    return results


def _fetch_lever_jobs(slug: str, eu: bool = False) -> list[SourceJob]:
    host = "api.eu.lever.co" if eu else "api.lever.co"
    url = f"https://{host}/v0/postings/{quote(slug, safe='')}?mode=json"
    data = _get_json(url)
    jobs = data if isinstance(data, list) else []
    results: list[SourceJob] = []
    for item in jobs[: _env_int("ATS_MAX_JOBS_PER_BOARD", 5)]:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("text"))
        apply_link = normalize_apply_link(
            _clean_text(item.get("applyUrl") or item.get("hostedUrl"))
        )
        if not title or not apply_link:
            continue
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        location = _lever_location(categories)
        description = "\n".join(
            part
            for part in (
                _clean_text(item.get("openingPlain")),
                _clean_text(item.get("descriptionPlain")),
                _clean_text(item.get("additionalPlain")),
                _lever_lists_text(item.get("lists")),
            )
            if part
        )
        results.append(
            SourceJob(
                platform=f"Lever: {slug}",
                role=title,
                experience=_infer_experience(f"{title}\n{description}"),
                contact_info=None,
                location=location,
                apply_link=apply_link,
                description=description,
            )
        )
    return results


def _fetch_ashby_jobs(slug: str) -> list[SourceJob]:
    url = (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{quote(slug, safe='')}?includeCompensation=true"
    )
    data = _get_json(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    results: list[SourceJob] = []
    for item in jobs[: _env_int("ATS_MAX_JOBS_PER_BOARD", 5)]:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        title = _clean_text(item.get("title"))
        apply_link = normalize_apply_link(
            _clean_text(item.get("applyUrl") or item.get("jobUrl"))
        )
        if not title or not apply_link:
            continue
        description = _clean_html(
            item.get("descriptionHtml")
            or item.get("description")
            or item.get("jobDescriptionHtml")
            or item.get("jobDescription")
        )
        location = _ashby_location(item)
        results.append(
            SourceJob(
                platform=f"Ashby: {slug}",
                role=title,
                experience=_infer_experience(f"{title}\n{description}"),
                contact_info=None,
                location=location,
                apply_link=apply_link,
                description=description,
            )
        )
    return results


def _get_json(url: str) -> Any:
    request = Request(
        url,
        headers={
            "User-Agent": _user_agent(),
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=_env_float("ATS_HTTP_TIMEOUT_SECONDS", 6.0)) as response:
        if response.status >= 400:
            return None
        return json.loads(response.read().decode("utf-8"))


def _parse_ats_board_url(value: str) -> ATSBoard | None:
    url = value if "://" in value else f"https://{value}"
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    if "greenhouse.io" in host:
        if "for" in query and query["for"]:
            return ATSBoard(provider="greenhouse", slug=query["for"][0])
        if host.startswith("boards-api.") and len(parts) >= 3 and parts[1] == "boards":
            return ATSBoard(provider="greenhouse", slug=parts[2])
        if parts:
            return ATSBoard(provider="greenhouse", slug=parts[0])

    if "lever.co" in host:
        provider = "lever-eu" if ".eu." in host or host.startswith("jobs.eu.") else "lever"
        if host.startswith("api.") and len(parts) >= 3 and parts[1] == "postings":
            return ATSBoard(provider=provider, slug=parts[2])
        if parts:
            return ATSBoard(provider=provider, slug=parts[0])

    if "ashbyhq.com" in host:
        if host.startswith("api.") and len(parts) >= 3 and parts[-2] == "job-board":
            return ATSBoard(provider="ashby", slug=parts[-1])
        if parts:
            return ATSBoard(provider="ashby", slug=parts[0])

    return None


def _normalize_provider(provider: str) -> str | None:
    provider = provider.lower().strip()
    aliases = {
        "gh": "greenhouse",
        "greenhouse": "greenhouse",
        "lever": "lever",
        "lever-eu": "lever-eu",
        "lever_eu": "lever-eu",
        "ashby": "ashby",
        "ashbyhq": "ashby",
    }
    return aliases.get(provider)


def _score_job(job: SourceJob, roles: Iterable[str], skills: Iterable[str]) -> float:
    role_phrases = [_normalize_text(role) for role in roles if role]
    role_terms = _important_terms(roles, stopwords=ROLE_STOPWORDS)
    skill_terms = _important_terms(skills, stopwords=set())
    title = _normalize_text(job.role)
    haystack = _normalize_text(f"{job.role} {job.location} {job.description}")
    haystack_terms = set(_tokens(haystack))
    title_terms = set(_tokens(title))

    score = 0.0
    for phrase in role_phrases:
        if phrase and phrase in title:
            score += 18
        elif phrase and phrase in haystack:
            score += 6

    for term in role_terms:
        if term in title_terms:
            score += 5
        elif term in haystack_terms:
            score += 2

    skill_hits = 0
    for term in skill_terms:
        if term in title_terms:
            score += 3
            skill_hits += 1
        elif term in haystack_terms:
            score += 1
            skill_hits += 1
    score += min(skill_hits, 8)

    if _is_location_relevant(job.location):
        score += 10

    return score


def _job_matches(job: SourceJob, score: float, years_exp: int) -> bool:
    if not _is_location_relevant(job.location):
        return False
    if _looks_like_internship(job.role) and years_exp > 2:
        return False
    if job.experience and years_exp > 0 and job.experience > years_exp + 3:
        return False
    return score >= _env_float("ATS_MIN_MATCH_SCORE", 12.0)


def _is_location_relevant(location: str) -> bool:
    normalized = f" {_normalize_text(location)} "
    if not normalized.strip():
        return False

    terms = _location_terms()
    if "*" in terms or "any" in terms:
        return True

    strong_terms = [term for term in terms if term != "remote"]
    if any(f" {term} " in normalized or term in normalized for term in strong_terms):
        return True

    if "remote" in terms and "remote" in normalized:
        return not _remote_looks_region_locked(normalized)

    return False


def _remote_looks_region_locked(location: str) -> bool:
    if any(term in location for term in ("india", "apac", "asia", "global", "worldwide", "anywhere")):
        return False
    return any(term in location for term in REMOTE_RESTRICTED_TERMS)


def _location_terms() -> list[str]:
    raw = os.getenv("JOB_LOCATION_KEYWORDS", "")
    if not raw:
        return list(DEFAULT_LOCATION_KEYWORDS)
    return [_normalize_text(term) for term in _split_csvish(raw) if term.strip()]


def _greenhouse_location(item: dict[str, Any]) -> str:
    primary = item.get("location") if isinstance(item.get("location"), dict) else {}
    locations = [_clean_text(primary.get("name"))]
    for office in item.get("offices") or []:
        if isinstance(office, dict):
            locations.append(_clean_text(office.get("location") or office.get("name")))
    return _join_unique(locations) or "Unspecified"


def _lever_location(categories: dict[str, Any]) -> str:
    locations: list[str] = []
    all_locations = categories.get("allLocations")
    if isinstance(all_locations, list):
        locations.extend(_clean_text(location) for location in all_locations)
    locations.append(_clean_text(categories.get("location")))
    return _join_unique(locations) or "Unspecified"


def _ashby_location(item: dict[str, Any]) -> str:
    locations = [_clean_text(item.get("location"))]
    for secondary in item.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            locations.append(_clean_text(secondary.get("location")))
    return _join_unique(locations) or "Unspecified"


def _lever_lists_text(lists: Any) -> str:
    if not isinstance(lists, list):
        return ""
    pieces: list[str] = []
    for item in lists:
        if not isinstance(item, dict):
            continue
        pieces.append(_clean_text(item.get("text")))
        pieces.append(_clean_html(item.get("content")))
    return "\n".join(piece for piece in pieces if piece)


def _infer_experience(text: str) -> int:
    normalized = _normalize_text(text[:8000])
    matches = [
        int(match.group(1))
        for match in re.finditer(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", normalized)
    ]
    if matches:
        return min(matches)

    title = normalized[:160]
    if _looks_like_internship(title):
        return 0
    if any(term in title for term in ("junior", "graduate", "entry level")):
        return 1
    if any(term in title for term in ("principal", "staff")):
        return 7
    if "senior" in title:
        return 5
    if any(term in title for term in ("lead", "manager")):
        return 6
    return 0


def _looks_like_internship(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(term in normalized for term in ("intern", "internship", "trainee"))


def _important_terms(values: Iterable[str], stopwords: set[str]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        for token in _tokens(_normalize_text(value)):
            if len(token) < 3 or token in stopwords:
                continue
            terms.add(token)
    if not terms:
        for value in values:
            terms.update(token for token in _tokens(_normalize_text(value)) if len(token) >= 3)
    return terms


def _tokens(value: str) -> list[str]:
    return [token.replace("-", "") for token in TOKEN_RE.findall(value)]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_text(value).lower()).strip()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return html.unescape(str(value)).strip()


def _clean_html(value: Any) -> str:
    if value is None:
        return ""
    without_tags = TAG_RE.sub(" ", str(value))
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _join_unique(values: Iterable[str]) -> str:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        value = _clean_text(value)
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            unique.append(value)
    return ", ".join(unique)


def _dedupe_boards(values: Iterable[ATSBoard | None]) -> list[ATSBoard]:
    seen: set[tuple[str, str]] = set()
    boards: list[ATSBoard] = []
    for board in values:
        if board is None:
            continue
        key = (board.provider, board.slug.lower())
        if key in seen:
            continue
        seen.add(key)
        boards.append(board)
    return boards


def _dedupe_source_jobs(jobs: Iterable[SourceJob]) -> list[SourceJob]:
    seen: set[str] = set()
    unique: list[SourceJob] = []
    for job in jobs:
        key = _job_key(job)
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique


def _job_key(job: SourceJob) -> str:
    parsed = urlparse(normalize_apply_link(job.apply_link))
    if parsed.netloc:
        return f"{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}?{parsed.query}"
    return f"{job.platform.lower()}|{job.role.lower()}|{job.location.lower()}"


def _split_csvish(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\n,]+", value) if part.strip()]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _user_agent() -> str:
    return os.getenv(
        "JOB_FETCH_USER_AGENT",
        "JobApplyAgent/1.0 (+https://localhost)",
    )
