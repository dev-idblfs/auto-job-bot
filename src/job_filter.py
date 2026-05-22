"""
Job filter & scorer: ranks job postings by relevance to the resume profile.

Scoring breakdown (configurable weights in config.yaml):
  - Title match    (30 pts): does the job title match desired titles?
  - Skills match   (40 pts): how many resume skills appear in the posting?
  - Location       (15 pts): does location match or is it remote?
  - Experience     (15 pts): does level language match profile's experience level?
  - Project bonus  (up to 10 pts): project tech overlap with job description
  - Industry bonus (up to 5 pts):  job posting aligns with target industries
  - Job type bonus (up to 5 pts):  job type / terms match profile preference
  All scores are capped at 100.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .job_searcher import JobPosting
from .resume_parser import ResumeProfile, JOB_TYPE_SIGNALS

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
    """
    Points for skill/keyword mentions in the full job text.

    Weighting within the skills score:
      50% - general keyword coverage (all_keywords)
      30% - primary skills coverage
      20% - project technology coverage
    """
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

    # Primary skills sub-score
    primary_matched = sum(
        1 for s in profile.primary_skills if s.lower() in text_lower
    )
    primary_ratio = primary_matched / max(len(profile.primary_skills), 1)

    # Project technology sub-score
    project_matched = sum(
        1 for t in profile.project_tech_lower
        if len(t) > 2 and (
            re.search(rf"\b{re.escape(t)}\b", text_lower) if len(t) <= 4 else t in text_lower
        )
    )
    project_ratio = (
        project_matched / len(profile.project_tech_lower)
        if profile.project_tech_lower else 0
    )

    combined = ratio * 0.5 + primary_ratio * 0.3 + project_ratio * 0.2
    return int(weight * combined)


def _score_location(job: JobPosting, profile: ResumeProfile, weight: int) -> int:
    """Points for location match or remote compatibility."""
    loc_lower = job.location.lower()

    if job.remote or "remote" in loc_lower or "worldwide" in loc_lower or "wfh" in loc_lower:
        if profile.remote_ok or "remote" in profile.job_types_lower:
            return weight
        return int(weight * 0.5)

    for term in profile.location_terms:
        if term and term in loc_lower:
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
        return weight

    for kw in level_keywords:
        if kw in text_lower:
            return weight

    adjacent = {"junior": "mid", "mid": "senior", "senior": "mid"}.get(level, "")
    adjacent_kws: list[str] = scoring_cfg.get(adjacent, {}).get("keywords", [])
    for kw in adjacent_kws:
        if kw in text_lower:
            return int(weight * 0.5)

    return int(weight * 0.6)


def _score_projects(text: str, profile: ResumeProfile, bonus_pts: int) -> int:
    """
    Bonus points when the job description specifically mentions technologies
    from the candidate's projects (on top of the skills score).
    """
    if not profile.project_tech_lower or bonus_pts <= 0:
        return 0
    text_lower = text.lower()
    matched = sum(
        1 for tech in profile.project_tech_lower
        if len(tech) > 2 and tech in text_lower
    )
    ratio = matched / len(profile.project_tech_lower)
    return int(bonus_pts * min(ratio * 2.5, 1.0))


def _score_industry(text: str, profile: ResumeProfile, bonus_pts: int) -> int:
    """Bonus points when the job aligns with the candidate's target industries."""
    if not profile.industry_keywords or bonus_pts <= 0:
        return 0
    text_lower = text.lower()
    if any(kw in text_lower for kw in profile.industry_keywords):
        return bonus_pts
    return 0


def _score_job_type(job: JobPosting, profile: ResumeProfile, bonus_pts: int) -> int:
    """
    Bonus points when the job type matches the candidate's preferred job types
    (full-time, contract, remote, etc.).
    """
    if not profile.job_types_lower or bonus_pts <= 0:
        return 0

    text = f"{job.title} {job.job_type} {job.description}".lower()

    for desired_type in profile.job_types_lower:
        signals = JOB_TYPE_SIGNALS.get(desired_type, [desired_type])
        if any(sig in text for sig in signals):
            return bonus_pts

    return 0


def _get_matched_skills(text: str, profile: ResumeProfile) -> list[str]:
    """
    Return a display-friendly list of skills/technologies from the resume
    that appear in the job text, ordered by importance (primary first).
    """
    text_lower = text.lower()
    matched: list[str] = []

    # Primary skills first
    for skill in profile.primary_skills:
        if skill.lower() in text_lower and skill not in matched:
            matched.append(skill)

    # Then other skills (secondary, cloud, tools)
    for skill in profile.all_skills:
        if skill not in matched and skill.lower() in text_lower:
            matched.append(skill)

    # Then project technologies not already listed
    for tech in sorted(profile.project_technologies):
        if tech not in matched and len(tech) > 2 and tech.lower() in text_lower:
            matched.append(tech)

    return matched[:12]  # Cap at 12 for display


def score_job(job: JobPosting, profile: ResumeProfile, config: dict) -> int:
    """Compute and return a 0-100 relevance score for a job posting."""
    scoring = config.get("scoring", {})
    title_w = scoring.get("title_match_weight", 30)
    skills_w = scoring.get("skills_match_weight", 40)
    loc_w = scoring.get("location_match_weight", 15)
    exp_w = scoring.get("experience_match_weight", 15)
    proj_bonus = scoring.get("project_match_bonus", 10)
    industry_bonus = scoring.get("industry_match_bonus", 5)
    jobtype_bonus = scoring.get("job_type_match_bonus", 5)

    full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"

    score = (
        _score_title(job.title, profile, title_w)
        + _score_skills(full_text, profile, skills_w)
        + _score_location(job, profile, loc_w)
        + _score_experience(full_text, profile, exp_w, config)
        + _score_projects(full_text, profile, proj_bonus)
        + _score_industry(full_text, profile, industry_bonus)
        + _score_job_type(job, profile, jobtype_bonus)
    )
    return min(score, 100)


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------

_YEAR_REQUIREMENT_RE = re.compile(
    r"(\d+)\s*\+\s*years?|"
    r"minimum\s+(\d+)\s+years?|"
    r"at\s+least\s+(\d+)\s+years?|"
    r"(\d+)\s*[-–]\s*\d+\s*years?\s+(?:of\s+)?(?:experience|exp)",
    re.IGNORECASE,
)


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

    # Location filter (if explicit locations specified in config)
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

    # Dynamic experience year filter – skip jobs that require far more years
    # than the candidate has (buffer set via config.filters.experience_year_buffer)
    profile_years = getattr(profile, "years_experience", 0)
    if profile_years > 0:
        year_buffer: int = filter_cfg.get("experience_year_buffer", 4)
        max_acceptable = profile_years + year_buffer
        for m in _YEAR_REQUIREMENT_RE.finditer(text_lower):
            required_years = int(next(g for g in m.groups() if g is not None))
            if required_years > max_acceptable:
                logger.debug(
                    "Excluded %r – requires %d+ yrs, profile has %d (buffer=%d)",
                    job.title, required_years, profile_years, year_buffer,
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

    Returns a ranked list of JobPosting objects ready for emailing, with
    matched_skills populated for display in the email digest.
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

        filtered.append(job)
        new_seen_ids.add(job.id)

    if dedup_enabled and new_seen_ids:
        save_seen_jobs(history_file, seen_ids | new_seen_ids, retention_days)

    filtered.sort(key=lambda j: j.relevance_score, reverse=True)

    max_jobs = config.get("email", {}).get("max_jobs_per_email", 30)
    result = filtered[:max_jobs]

    # Compute matched skills for each selected job (used in email cards)
    for job in result:
        full_text = f"{job.title} {job.description} {' '.join(job.tags)}"
        job.matched_skills = _get_matched_skills(full_text, profile)

    logger.info(
        "Filter pipeline: %d jobs passed (%d total fetched), top %d selected",
        len(filtered),
        len(jobs),
        len(result),
    )
    return result
