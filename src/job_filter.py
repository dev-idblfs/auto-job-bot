"""
Job filter & scorer: ranks job postings by relevance to the resume profile.

Scoring breakdown (configurable weights in config.yaml):
  - Title match   (25 pts): does the job title match desired titles?
  - Skills match  (35 pts): how many resume skills appear in the posting?
  - Projects match (15 pts): do project technologies appear in the posting?
  - Location      (15 pts): does location match or is it remote?
  - Experience    (10 pts): does level / years language match profile?

Hard filters (applied before scoring):
  - Excluded keywords
  - Required keywords
  - Remote-only toggle
  - Location allow-list
  - Job type (full-time / contract / internship)
  - Minimum salary (if detectable and profile.min_salary set)
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

# Salary-range patterns (to extract numbers from job postings)
_SALARY_PATTERN = re.compile(
    r"(?:₹|rs\.?|inr|lpa|lakh|lac|k|usd|\$)\s*(\d[\d,.]*)"
    r"|(\d[\d,.]*)\s*(?:₹|rs\.?|inr|lpa|lakh|lac|k|usd|\$)",
    re.IGNORECASE,
)
_LPA_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakh|lac)",
    re.IGNORECASE,
)
_USD_PATTERN = re.compile(
    r"\$\s*(\d{2,3})[kK]?\s*[-–]\s*\$?\s*(\d{2,3})[kK]?",
)


# ---------------------------------------------------------------------------
# Salary extraction helpers
# ---------------------------------------------------------------------------

def _extract_max_salary_inr(text: str) -> int | None:
    """
    Try to extract the maximum salary from text, normalised to annual INR
    (or USD for non-India postings). Returns None if undetectable.
    """
    text_lower = text.lower()

    # LPA range (Indian style: "8-15 LPA")
    m = _LPA_PATTERN.search(text_lower)
    if m:
        high = float(m.group(2))
        return int(high * 100_000)  # LPA → annual INR

    # USD range ("$80k-$120k" / "$80-$120k")
    m = _USD_PATTERN.search(text)
    if m:
        high = float(m.group(2).replace(",", ""))
        # Assume in thousands if <= 999
        if high < 1_000:
            high *= 1_000
        return int(high)

    return None


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_title(job_title: str, profile: ResumeProfile, weight: int) -> tuple[int, list[str]]:
    """Points for title similarity to desired titles. Also returns match reasons."""
    jt = job_title.lower()
    best = 0
    reasons: list[str] = []
    for desired in profile.target_titles:
        d = desired.lower()
        if d == jt:
            best = max(best, weight)
            reasons.append(f"Exact title match: {desired}")
        elif d in jt or jt in d:
            score = int(weight * 0.8)
            if score > best:
                best = score
                reasons.append(f"Title match: {desired}")
        else:
            d_words = set(d.split())
            j_words = set(jt.split())
            overlap = d_words & j_words
            if overlap:
                ratio = len(overlap) / max(len(d_words), len(j_words))
                score = int(weight * ratio * 0.7)
                if score > best:
                    best = score
                    reasons.append(f"Partial title match: {desired}")
    return best, reasons


def _score_skills(
    text: str,
    profile: ResumeProfile,
    weight: int,
) -> tuple[int, list[str]]:
    """Points for skill/keyword mentions. Returns (score, matched_skill_list)."""
    text_lower = text.lower()
    matched_skills: list[str] = []

    for skill in profile.all_skills:
        skill_lower = skill.lower()
        if len(skill_lower) <= 3:
            if re.search(rf"\b{re.escape(skill_lower)}\b", text_lower):
                matched_skills.append(skill)
        elif skill_lower in text_lower:
            matched_skills.append(skill)

    total = len(profile.all_skills) or 1
    ratio = min(len(matched_skills) / total, 1.0)

    # Bonus weighting for primary skills
    primary_matched = [s for s in profile.primary_skills if s.lower() in text_lower]
    primary_ratio = len(primary_matched) / max(len(profile.primary_skills), 1)
    combined = ratio * 0.6 + primary_ratio * 0.4

    return int(weight * combined), matched_skills


def _score_projects(
    text: str,
    profile: ResumeProfile,
    weight: int,
) -> tuple[int, list[str]]:
    """
    Points for project-technology mentions and matched project names.
    Returns (score, matched_project_tech_list).
    """
    text_lower = text.lower()
    matched_techs: list[str] = []

    for tech in profile.project_technologies:
        tech_lower = tech.lower()
        if len(tech_lower) <= 3:
            if re.search(rf"\b{re.escape(tech_lower)}\b", text_lower):
                matched_techs.append(tech)
        elif tech_lower in text_lower:
            matched_techs.append(tech)

    if not profile.project_technologies:
        return 0, []

    ratio = min(len(matched_techs) / len(profile.project_technologies), 1.0)
    return int(weight * ratio), matched_techs


def _score_location(job: JobPosting, profile: ResumeProfile, weight: int) -> tuple[int, list[str]]:
    """Points for location match or remote compatibility."""
    loc_lower = job.location.lower()
    reasons: list[str] = []

    if job.remote or "remote" in loc_lower or "worldwide" in loc_lower:
        if profile.remote_ok:
            reasons.append("Remote / WFH")
            return weight, reasons
        return int(weight * 0.5), reasons

    for term in profile.location_terms:
        if term in loc_lower:
            reasons.append(f"Location: {job.location}")
            return weight, reasons

    if profile.willing_to_relocate:
        reasons.append("Willing to relocate")
        return int(weight * 0.4), reasons

    return 0, reasons


def _score_experience(
    text: str,
    profile: ResumeProfile,
    weight: int,
    config: dict,
) -> tuple[int, list[str]]:
    """Points for experience-level language matching the profile."""
    text_lower = text.lower()
    scoring_cfg = config.get("filters", {}).get("experience_levels", {})
    reasons: list[str] = []

    level = profile.experience_level
    level_keywords: list[str] = scoring_cfg.get(level, {}).get("keywords", [])

    if not level_keywords:
        return weight, reasons

    for kw in level_keywords:
        if kw in text_lower:
            reasons.append(f"Experience level: {level}")
            return weight, reasons

    # Years-of-experience numeric check
    years = profile.years_experience
    if years > 0:
        patterns = [
            rf"\b{years}\+?\s*(?:years?|yrs?)\b",
            rf"\b{max(years-1,0)}-{years+1}\s*(?:years?|yrs?)\b",
            rf"\b{max(years-2,0)}-{years+1}\s*(?:years?|yrs?)\b",
        ]
        for pat in patterns:
            if re.search(pat, text_lower):
                reasons.append(f"{years} yrs experience match")
                return weight, reasons

    # Mild partial credit for adjacent levels
    adjacent = {"junior": "mid", "mid": "senior", "senior": "mid"}.get(level, "")
    adjacent_kws: list[str] = scoring_cfg.get(adjacent, {}).get("keywords", [])
    for kw in adjacent_kws:
        if kw in text_lower:
            return int(weight * 0.5), reasons

    return int(weight * 0.6), reasons


def score_job(
    job: JobPosting,
    profile: ResumeProfile,
    config: dict,
) -> tuple[int, list[str], list[str], list[str]]:
    """
    Compute a 0-100 relevance score for a job posting.

    Returns:
        (score, matched_skills, matched_projects, match_reasons)
    """
    scoring = config.get("scoring", {})
    title_w   = scoring.get("title_match_weight",    25)
    skills_w  = scoring.get("skills_match_weight",   35)
    project_w = scoring.get("project_match_weight",  15)
    loc_w     = scoring.get("location_match_weight", 15)
    exp_w     = scoring.get("experience_match_weight", 10)

    full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"

    title_score,   title_reasons    = _score_title(job.title, profile, title_w)
    skills_score,  matched_skills   = _score_skills(full_text, profile, skills_w)
    project_score, matched_projects = _score_projects(full_text, profile, project_w)
    loc_score,     loc_reasons      = _score_location(job, profile, loc_w)
    exp_score,     exp_reasons      = _score_experience(full_text, profile, exp_w, config)

    total = min(title_score + skills_score + project_score + loc_score + exp_score, 100)

    all_reasons = title_reasons + loc_reasons + exp_reasons
    if matched_skills:
        all_reasons.append(f"Skills: {', '.join(matched_skills[:6])}")
    if matched_projects:
        all_reasons.append(f"Project tech: {', '.join(matched_projects[:4])}")

    return total, matched_skills, matched_projects, all_reasons


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------

def _passes_hard_filters(
    job: JobPosting,
    profile: ResumeProfile,
    config: dict,
) -> bool:
    """Return False if job should be excluded regardless of score."""
    filter_cfg = config.get("filters", {})
    text_lower = f"{job.title} {job.description}".lower()

    # Excluded keywords
    for kw in filter_cfg.get("excluded_keywords", []):
        if kw.lower() in text_lower:
            logger.debug("Excluded %r – matched excluded keyword %r", job.title, kw)
            return False

    # Required keywords
    for kw in filter_cfg.get("required_keywords", []):
        if kw.lower() not in text_lower:
            logger.debug("Excluded %r – missing required keyword %r", job.title, kw)
            return False

    # Remote-only filter
    if filter_cfg.get("remote_only", False):
        if not job.remote and "remote" not in job.location.lower():
            return False

    # Location allow-list filter
    location_overrides: list[str] = filter_cfg.get("locations", [])
    if location_overrides:
        loc_lower = job.location.lower()
        remote_ok_here = (
            job.remote
            or "remote" in loc_lower
            or any("remote" in lo.lower() for lo in location_overrides)
        )
        if not remote_ok_here:
            if not any(lo.lower() in loc_lower for lo in location_overrides):
                return False

    # Job-type filter (full-time, contract, internship, etc.)
    job_type_filter: list[str] = filter_cfg.get("job_types", [])
    if not job_type_filter and profile.job_types:
        job_type_filter = profile.job_types
    if job_type_filter and job.job_type:
        jt_lower = job.job_type.lower()
        if not any(jt.lower() in jt_lower or jt_lower in jt.lower() for jt in job_type_filter):
            # Only hard-exclude if type is confidently known (not empty)
            logger.debug("Excluded %r – job_type %r not in %s", job.title, job.job_type, job_type_filter)
            return False

    # Minimum salary filter
    if filter_cfg.get("apply_salary_filter", False) and profile.min_salary > 0:
        full_text = f"{job.title} {job.description} {job.salary}"
        max_salary = _extract_max_salary_inr(full_text)
        if max_salary is not None and max_salary < profile.min_salary:
            logger.debug(
                "Excluded %r – max salary %d < min %d",
                job.title, max_salary, profile.min_salary,
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

        retained = {
            jid: ts
            for jid, ts in timestamps.items()
            if _parse_ts(ts) >= cutoff
        }

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
        if dedup_enabled and job.id in seen_ids:
            continue

        if not _is_recent(job, days_back):
            continue

        if not _passes_hard_filters(job, profile, config):
            continue

        score, matched_skills, matched_projects, reasons = score_job(job, profile, config)
        if score < min_score:
            logger.debug("Filtered out %r (score %d < %d)", job.title, score, min_score)
            continue

        job.relevance_score = score
        job.matched_skills = matched_skills
        job.matched_projects = matched_projects
        job.match_reasons = reasons

        filtered.append(job)
        new_seen_ids.add(job.id)

    if dedup_enabled and new_seen_ids:
        save_seen_jobs(history_file, seen_ids | new_seen_ids, retention_days)

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
