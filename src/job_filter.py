"""
Job filter & scorer: ranks job postings by relevance to the resume profile.

Scoring breakdown (configurable weights in config.yaml):
  - Title match   (30 pts): does the job title match desired titles?
  - Skills match  (30 pts): resume skills found in the posting
  - Projects match (10 pts): project technologies / themes in the posting
  - Location      (15 pts): does location match or is it remote?
  - Experience    (15 pts): does level language match profile's experience level?

Hard filters (config.yaml → filters):
  - Location, remote, required/excluded keywords
  - Job type (full-time, contract, etc.) from resume.json → target.job_types
  - Minimum salary from resume.json → target.min_salary (when salary is listed)
  - Experience years cap vs resume.json → experience.years_total
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .job_searcher import JobPosting
from .resume_parser import ResumeProfile

logger = logging.getLogger(__name__)

# Job-type phrases used for hard filtering (resume.json → target.job_types)
_JOB_TYPE_PHRASES: dict[str, list[str]] = {
    "full-time": ["full time", "full-time", "fulltime", "permanent", "fte"],
    "part-time": ["part time", "part-time", "parttime"],
    "contract": ["contract", "contractor", "consulting", "consultant", "c2h", "c2c"],
    "internship": ["intern", "internship", "trainee", "apprentice"],
    "freelance": ["freelance", "freelancer", "gig"],
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_title(job_title: str, profile: ResumeProfile, weight: int) -> int:
    """Points for title similarity to desired titles."""
    jt = job_title.lower()
    best = 0
    for desired in profile.target_titles:
        d = desired.lower()
        # Exact match
        if d == jt:
            best = max(best, weight)
        # Desired title is a substring of the job title or vice-versa
        elif d in jt or jt in d:
            best = max(best, int(weight * 0.8))
        else:
            # Word-level overlap
            d_words = set(d.split())
            j_words = set(jt.split())
            overlap = d_words & j_words
            if overlap:
                ratio = len(overlap) / max(len(d_words), len(j_words))
                best = max(best, int(weight * ratio * 0.7))
    return best


def _score_skills(text: str, profile: ResumeProfile, weight: int) -> int:
    """Points for skill/keyword mentions in the full job text."""
    text_lower = text.lower()
    matched = 0
    for skill in profile.all_keywords:
        # Use word-boundary matching for short skills to avoid false positives
        if len(skill) <= 3:
            if re.search(rf"\b{re.escape(skill)}\b", text_lower):
                matched += 1
        elif skill in text_lower:
            matched += 1

    if not profile.all_keywords:
        return 0

    ratio = min(matched / len(profile.all_keywords), 1.0)
    # Bonus for primary skills
    primary_matched = sum(
        1 for s in profile.primary_skills if s.lower() in text_lower
    )
    primary_ratio = primary_matched / max(len(profile.primary_skills), 1)
    combined = ratio * 0.6 + primary_ratio * 0.4
    return int(weight * combined)


def _score_projects(text: str, profile: ResumeProfile, weight: int) -> int:
    """Points for project technology / theme overlap in the job text."""
    if not profile.project_tech_lower:
        return 0
    text_lower = text.lower()
    matched = sum(1 for tech in profile.project_tech_lower if tech in text_lower)
    ratio = min(matched / len(profile.project_tech_lower), 1.0)
    return int(weight * ratio)


def _score_location(job: JobPosting, profile: ResumeProfile, weight: int) -> int:
    """Points for location match or remote compatibility."""
    loc_lower = job.location.lower()

    if job.remote or "remote" in loc_lower or "worldwide" in loc_lower:
        if profile.remote_ok:
            return weight
        return int(weight * 0.5)

    for term in profile.location_terms:
        if term in loc_lower:
            return weight

    if profile.willing_to_relocate:
        return int(weight * 0.4)

    # No match at all
    return 0


def _score_experience(text: str, profile: ResumeProfile, weight: int, config: dict) -> int:
    """Points for experience-level language matching the profile."""
    text_lower = text.lower()
    scoring_cfg = config.get("filters", {}).get("experience_levels", {})

    level = profile.experience_level
    level_keywords: list[str] = scoring_cfg.get(level, {}).get("keywords", [])

    # If no level keywords defined, give full score (don't penalise)
    if not level_keywords:
        return weight

    for kw in level_keywords:
        if kw in text_lower:
            return weight

    # Mild partial credit for adjacent levels
    adjacent = {"junior": "mid", "mid": "senior", "senior": "mid"}.get(level, "")
    adjacent_kws: list[str] = scoring_cfg.get(adjacent, {}).get("keywords", [])
    for kw in adjacent_kws:
        if kw in text_lower:
            return int(weight * 0.5)

    # If no level language found at all → neutral (don't heavily penalise)
    return int(weight * 0.6)


def score_job(job: JobPosting, profile: ResumeProfile, config: dict) -> int:
    """Compute and return a 0-100 relevance score for a job posting."""
    scoring = config.get("scoring", {})
    title_w = scoring.get("title_match_weight", 30)
    skills_w = scoring.get("skills_match_weight", 30)
    projects_w = scoring.get("projects_match_weight", 10)
    loc_w = scoring.get("location_match_weight", 15)
    exp_w = scoring.get("experience_match_weight", 15)

    full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"

    score = (
        _score_title(job.title, profile, title_w)
        + _score_skills(full_text, profile, skills_w)
        + _score_projects(full_text, profile, projects_w)
        + _score_location(job, profile, loc_w)
        + _score_experience(full_text, profile, exp_w, config)
    )
    return min(score, 100)


# ---------------------------------------------------------------------------
# Salary / experience / job-type helpers
# ---------------------------------------------------------------------------

def _normalise_currency(currency: str) -> str:
    c = (currency or "INR").upper()
    if c in ("INR", "RS", "RUPEES", "₹"):
        return "INR"
    if c in ("USD", "US$", "$"):
        return "USD"
    return c


def _parse_salary_floor(text: str, currency: str) -> int | None:
    """
    Extract the lowest plausible annual salary from free text.
    Returns amount in the same unit as resume min_salary (INR annual or USD annual).
    """
    if not text:
        return None
    t = text.lower().replace(",", "")
    cur = _normalise_currency(currency)

    if cur == "INR":
        # 12-18 LPA, 12 LPA, 12lpa, 12 lakhs, ₹12L
        for pattern in (
            r"(\d+(?:\.\d+)?)\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*(?:lpa|l\.?p\.?a|lakhs?|lacs?)\b",
            r"(\d+(?:\.\d+)?)\s*(?:lpa|l\.?p\.?a|lakhs?|lacs?)\b",
            r"₹?\s*(\d+(?:\.\d+)?)\s*l\b",
        ):
            m = re.search(pattern, t)
            if m:
                low = float(m.group(1))
                return int(low * 100_000)
        # Raw annual INR: ₹1200000, 12,00,000
        m = re.search(r"(?:₹|rs\.?|inr)\s*(\d{6,9})\b", t)
        if m:
            return int(m.group(1))
    else:
        for pattern in (
            r"\$\s*(\d+(?:\.\d+)?)\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*k\b",
            r"\$\s*(\d+(?:\.\d+)?)\s*k\b",
            r"(\d{2,3})\s*[-–to]+\s*(\d{2,3})\s*k\b",
            r"(\d{2,3})\s*k\b",
        ):
            m = re.search(pattern, t)
            if m:
                low = float(m.group(1))
                return int(low * 1000)
        m = re.search(r"\$\s*(\d{5,7})\b", t)
        if m:
            return int(m.group(1))

    return None


def _job_mentions_unwanted_type(text: str, wanted_types: list[str]) -> bool:
    """True if posting explicitly mentions a job type the candidate does not want."""
    wanted = {wt.lower() for wt in wanted_types}
    text_lower = text.lower()
    for jtype, phrases in _JOB_TYPE_PHRASES.items():
        if jtype in wanted:
            continue
        for phrase in phrases:
            if phrase in text_lower:
                return True
    return False


def _min_years_required(text: str) -> int | None:
    """Parse minimum years of experience required (e.g. '5+ years', '3-6 years')."""
    patterns = [
        r"(\d+)\s*\+\s*years?",
        r"minimum\s+(\d+)\s*years?",
        r"at\s+least\s+(\d+)\s*years?",
        r"(\d+)\s*[-–]\s*\d+\s*years?",
        r"(\d+)\s*years?\s+of\s+experience",
    ]
    found: list[int] = []
    for pat in patterns:
        for m in re.finditer(pat, text.lower()):
            found.append(int(m.group(1)))
    return max(found) if found else None


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------

def _passes_hard_filters(job: JobPosting, profile: ResumeProfile, config: dict) -> bool:
    """Return False if job should be excluded regardless of score."""
    filter_cfg = config.get("filters", {})
    text_lower = f"{job.title} {job.description}".lower()

    # Excluded keywords
    for kw in filter_cfg.get("excluded_keywords", []):
        if kw.lower() in text_lower:
            logger.debug("Excluded %r – matched excluded keyword %r", job.title, kw)
            return False

    # Required keywords (all must be present)
    for kw in filter_cfg.get("required_keywords", []):
        if kw.lower() not in text_lower:
            logger.debug("Excluded %r – missing required keyword %r", job.title, kw)
            return False

    # Remote-only filter
    if filter_cfg.get("remote_only", False):
        if not job.remote and "remote" not in job.location.lower():
            return False

    # Location filter (if explicit locations specified)
    location_overrides: list[str] = filter_cfg.get("locations", [])
    if location_overrides:
        loc_lower = job.location.lower()
        remote_ok_here = (
            job.remote
            or "remote" in loc_lower
            or any("remote" in lo.lower() for lo in location_overrides)
        )
        if not remote_ok_here:
            matched_loc = any(lo.lower() in loc_lower for lo in location_overrides)
            if not matched_loc:
                return False

    # Job type (full-time, contract, etc.)
    if filter_cfg.get("enforce_job_types", True) and profile.job_types:
        type_text = f"{job.title} {job.description} {job.job_type}".lower()
        if _job_mentions_unwanted_type(type_text, profile.job_types):
            logger.debug("Excluded %r – unwanted job type mentioned", job.title)
            return False

    # Minimum salary (only when a salary figure is present in the posting)
    if filter_cfg.get("enforce_min_salary", False) and profile.min_salary > 0:
        salary_text = f"{job.salary} {job.description}"
        floor = _parse_salary_floor(salary_text, profile.salary_currency)
        if floor is not None and floor < profile.min_salary:
            logger.debug(
                "Excluded %r – salary floor %d below min %d",
                job.title,
                floor,
                profile.min_salary,
            )
            return False

    # Years of experience vs resume (skip roles asking for far more experience)
    max_gap = filter_cfg.get("max_experience_years_gap", 3)
    if profile.years_experience > 0:
        req_years = _min_years_required(f"{job.title} {job.description}")
        if req_years is not None and req_years > profile.years_experience + max_gap:
            logger.debug(
                "Excluded %r – requires %d yrs (profile has %d)",
                job.title,
                req_years,
                profile.years_experience,
            )
            return False

    return True


def _is_recent(job: JobPosting, days_back: int) -> bool:
    """Return True if the job was posted within the last `days_back` days."""
    if not job.posted_at:
        return True  # Include if date unknown

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(job.posted_at[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days_back)
            return dt >= cutoff
        except ValueError:
            continue

    return True  # Unparseable date → include


# ---------------------------------------------------------------------------
# Deduplication against previously seen jobs
# ---------------------------------------------------------------------------

def load_seen_jobs(history_file: str) -> set[str]:
    path = Path(history_file)
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("ids", []))
    except Exception:
        return set()


def save_seen_jobs(history_file: str, seen_ids: set[str], retention_days: int) -> None:
    path = Path(history_file)
    try:
        existing: dict = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                existing = json.load(f)

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
        timestamps: dict[str, str] = existing.get("timestamps", {})

        # Remove old entries
        retained = {
            jid: ts
            for jid, ts in timestamps.items()
            if _parse_ts(ts) >= cutoff
        }

        # Add new
        now_str = datetime.now(tz=timezone.utc).isoformat()
        for jid in seen_ids:
            retained[jid] = retained.get(jid, now_str)

        with path.open("w", encoding="utf-8") as f:
            json.dump({"ids": list(retained.keys()), "timestamps": retained}, f, indent=2)
    except Exception as exc:
        logger.error("Failed to save seen jobs: %s", exc)


def _parse_ts(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Main filter pipeline
# ---------------------------------------------------------------------------

def filter_and_rank_jobs(
    jobs: list[JobPosting],
    profile: ResumeProfile,
    config: dict,
) -> list[JobPosting]:
    """
    Apply hard filters, score relevance, deduplicate, and sort by score.

    Returns a ranked list of JobPosting objects ready for emailing.
    """
    search_cfg = config.get("search", {})
    days_back: int = search_cfg.get("days_back", 1)
    min_score: int = search_cfg.get("min_relevance_score", 40)
    dedup_cfg = config.get("deduplication", {})
    dedup_enabled: bool = dedup_cfg.get("enabled", True)
    history_file: str = dedup_cfg.get("history_file", "seen_jobs.json")
    retention_days: int = dedup_cfg.get("retention_days", 30)

    seen_ids = load_seen_jobs(history_file) if dedup_enabled else set()

    new_seen_ids: set[str] = set()
    filtered: list[JobPosting] = []

    for job in jobs:
        # Skip previously seen jobs
        if dedup_enabled and job.id in seen_ids:
            continue

        if not _is_recent(job, days_back):
            continue

        if not _passes_hard_filters(job, profile, config):
            continue

        job.relevance_score = score_job(job, profile, config)
        if job.relevance_score < min_score:
            logger.debug(
                "Filtered out %r (score %d < %d)", job.title, job.relevance_score, min_score
            )
            continue

        filtered.append(job)
        new_seen_ids.add(job.id)

    # Save updated seen IDs
    if dedup_enabled and new_seen_ids:
        save_seen_jobs(history_file, seen_ids | new_seen_ids, retention_days)

    # Sort by relevance descending
    filtered.sort(key=lambda j: j.relevance_score, reverse=True)

    max_jobs = config.get("email", {}).get("max_jobs_per_email", 30)
    result = filtered[:max_jobs]

    logger.info(
        "Filter pipeline: %d jobs passed (%d total fetched), top %d selected",
        len(filtered),
        len(jobs),
        len(result),
    )
    return result
