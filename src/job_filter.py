"""
Job filter & scorer: ranks job postings by relevance to the resume profile.

Scoring breakdown (configurable weights in config.yaml → scoring):
  - Title match    (30 pts): does the job title match desired titles?
  - Skills match   (30 pts): how many resume skills appear in the posting?
  - Projects match (10 pts): do project domains / technologies appear in the posting?
  - Location       (15 pts): does location match or is it remote?
  - Experience     (15 pts): does level language match profile's experience level?

Hard filters (applied before scoring, config.yaml → filters):
  - excluded_keywords: instantly discard any job containing these phrases
  - required_keywords: job must contain all of these to pass
  - remote_only: discard non-remote postings when true
  - locations: require the job to match one of these cities/regions
  - job_types: require the job type (full-time / contract / internship) to match
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


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_title(job_title: str, profile: ResumeProfile, weight: int) -> int:
    """Points for title similarity to desired titles."""
    jt = job_title.lower()
    best = 0
    for desired in profile.target_titles:
        d = desired.lower()
        if d == jt:
            best = max(best, weight)
        elif d in jt or jt in d:
            best = max(best, int(weight * 0.8))
        else:
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
        1 for s in profile.primary_skills_lower if s in text_lower
    )
    primary_ratio = primary_matched / max(len(profile.primary_skills), 1)
    combined = ratio * 0.6 + primary_ratio * 0.4
    return int(weight * combined)


def _score_projects(text: str, profile: ResumeProfile, weight: int) -> int:
    """
    Points for how well the job description aligns with the candidate's
    project experience domains (e.g. e-commerce, analytics, microservices).

    Uses two signals:
      1. Project technology overlap (already in skills, but weighted separately
         here to reward domain depth rather than breadth).
      2. Domain phrases extracted from project descriptions (e.g. "real-time",
         "rest api", "data visualization", "microservices architecture").
    """
    if not profile.projects:
        return int(weight * 0.5)  # Neutral if no projects listed

    text_lower = text.lower()

    # Signal 1: project tech mentions
    proj_tech_matched = sum(1 for t in profile.project_tech_lower if t in text_lower)
    tech_ratio = min(proj_tech_matched / max(len(profile.project_tech_lower), 1), 1.0)

    # Signal 2: domain phrase mentions (higher quality signal)
    domain_matched = sum(1 for p in profile.project_domain_phrases if p in text_lower)
    domain_ratio = min(domain_matched / max(len(profile.project_domain_phrases), 1), 1.0)

    # Weight domain phrases more than raw tech mentions (they're richer signals)
    combined = tech_ratio * 0.4 + domain_ratio * 0.6
    return int(weight * combined)


def _score_location(job: JobPosting, profile: ResumeProfile, weight: int) -> int:
    """Points for location match or remote compatibility."""
    loc_lower = job.location.lower()

    if job.remote or "remote" in loc_lower or "worldwide" in loc_lower or "wfh" in loc_lower:
        if profile.remote_ok:
            return weight
        return int(weight * 0.5)

    for term in profile.location_terms:
        if term in loc_lower:
            return weight

    if profile.willing_to_relocate:
        return int(weight * 0.4)

    return 0


def _score_experience(text: str, profile: ResumeProfile, weight: int, config: dict) -> int:
    """Points for experience-level language matching the profile."""
    text_lower = text.lower()
    scoring_cfg = config.get("filters", {}).get("experience_levels", {})

    level = profile.experience_level
    level_keywords: list[str] = scoring_cfg.get(level, {}).get("keywords", [])

    if not level_keywords:
        return weight  # Don't penalise if no keywords defined

    for kw in level_keywords:
        if kw in text_lower:
            return weight

    # Partial credit for adjacent level
    adjacent = {"junior": "mid", "mid": "senior", "senior": "mid"}.get(level, "")
    adjacent_kws: list[str] = scoring_cfg.get(adjacent, {}).get("keywords", [])
    for kw in adjacent_kws:
        if kw in text_lower:
            return int(weight * 0.5)

    return int(weight * 0.6)  # Neutral – no level language found


def _score_industry(text: str, profile: ResumeProfile) -> int:
    """
    Bonus points (0–5) for matching target industries.
    This is a supplementary signal on top of the 100-point scale.
    The final score is capped at 100 anyway.
    """
    if not profile.target_industries:
        return 0

    text_lower = text.lower()
    matched = sum(1 for ind in profile.target_industries if ind in text_lower)
    # At most 5 bonus points
    return min(matched * 2, 5)


def score_job(job: JobPosting, profile: ResumeProfile, config: dict) -> int:
    """Compute and return a 0-100 relevance score for a job posting."""
    scoring = config.get("scoring", {})
    title_w   = scoring.get("title_match_weight",    30)
    skills_w  = scoring.get("skills_match_weight",   30)
    projects_w = scoring.get("projects_match_weight", 10)
    loc_w     = scoring.get("location_match_weight", 15)
    exp_w     = scoring.get("experience_match_weight", 15)

    full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"

    score = (
        _score_title(job.title, profile, title_w)
        + _score_skills(full_text, profile, skills_w)
        + _score_projects(full_text, profile, projects_w)
        + _score_location(job, profile, loc_w)
        + _score_experience(full_text, profile, exp_w, config)
        + _score_industry(full_text, profile)
    )
    return min(score, 100)


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------

# Job type normalisation map
_JOB_TYPE_ALIASES: dict[str, list[str]] = {
    "full-time":  ["full-time", "full time", "permanent", "regular", "ft"],
    "part-time":  ["part-time", "part time", "pt"],
    "contract":   ["contract", "freelance", "consultant", "c2c", "c2h"],
    "internship": ["intern", "internship", "trainee", "apprentice"],
}


def _normalise_job_type(raw: str) -> str:
    """Map a raw job-type string to a canonical name."""
    raw_lower = raw.lower()
    for canonical, aliases in _JOB_TYPE_ALIASES.items():
        if any(a in raw_lower for a in aliases):
            return canonical
    return raw_lower


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
        if not job.remote and "remote" not in job.location.lower() and "wfh" not in job.location.lower():
            return False

    # Location filter (if explicit locations specified in config)
    location_overrides: list[str] = filter_cfg.get("locations", [])
    if location_overrides:
        loc_lower = job.location.lower()
        remote_ok_here = (
            job.remote
            or "remote" in loc_lower
            or "wfh" in loc_lower
            or any("remote" in lo.lower() for lo in location_overrides)
        )
        if not remote_ok_here:
            matched_loc = any(lo.lower() in loc_lower for lo in location_overrides)
            if not matched_loc:
                return False

    # Job type filter – only enforce when profile specifies preferences
    desired_types: list[str] = profile.job_types
    if desired_types:
        # Also allow config override
        cfg_types: list[str] = [
            t.lower() for t in filter_cfg.get("job_types", [])
        ]
        effective_types = cfg_types if cfg_types else desired_types

        # "full-time" in preferences → also accept jobs with no type specified
        # (most postings don't explicitly state full-time)
        if job.job_type:
            normalised = _normalise_job_type(job.job_type)
            if normalised not in effective_types:
                # Only hard-exclude if the job is *explicitly* a type we don't want
                excluded_types = [
                    t for t in list(_JOB_TYPE_ALIASES.keys()) if t not in effective_types
                ]
                if normalised in excluded_types:
                    logger.debug(
                        "Excluded %r – job type %r not in desired %s",
                        job.title, job.job_type, effective_types,
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

    Attaches `matched_skills` attribute to each passing JobPosting so the
    email template can highlight why a job was selected.

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

        job.relevance_score = score_job(job, profile, config)
        if job.relevance_score < min_score:
            logger.debug(
                "Filtered out %r (score %d < %d)", job.title, job.relevance_score, min_score
            )
            continue

        # Attach the matched skills list so the email can highlight them
        full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"
        job.matched_skills = profile.get_matching_skills(full_text)  # type: ignore[attr-defined]

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
