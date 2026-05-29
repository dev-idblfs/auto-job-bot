"""
Job filter & scorer: ranks job postings by relevance to the resume profile.

Scoring breakdown (configurable weights in config.yaml, must sum to 100):
  - Title match    (30 pts): does the job title match desired titles?
  - Skills match   (30 pts): how many resume skills appear in the posting?
  - Projects match (10 pts): do project domain phrases/technologies appear?
  - Location       (15 pts): does location match or is it remote?
  - Experience     (15 pts): does level language match profile's level?
  - Industry bonus (0–5  pts): extra points for target industries (capped at 100)

Hard filters (applied before scoring):
  - excluded_keywords: drop if any appear in title/description
  - required_keywords: drop if not all present
  - remote_only: drop non-remote jobs when enabled
  - locations override: drop jobs outside listed locations (unless remote)
  - job_type filter: drop jobs whose type mismatches profile's job_types
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

def _score_title(
    job_title: str,
    profile: ResumeProfile,
    weight: int,
) -> tuple[int, list[str]]:
    """Points for title similarity to desired titles.

    Returns (score, list_of_matched_titles).
    """
    jt = job_title.lower()
    best = 0
    matched: list[str] = []
    for desired in profile.target_titles:
        d = desired.lower()
        if d == jt:
            best = max(best, weight)
            matched.append(desired)
        elif d in jt or jt in d:
            pts = int(weight * 0.8)
            if pts > best:
                best = pts
                matched = [desired]
            elif pts == best:
                matched.append(desired)
        else:
            d_words = set(d.split())
            j_words = set(jt.split())
            overlap = d_words & j_words
            if overlap:
                ratio = len(overlap) / max(len(d_words), len(j_words))
                pts = int(weight * ratio * 0.7)
                if pts > best:
                    best = pts
                    matched = [desired]
                elif pts == best and pts > 0:
                    matched.append(desired)
    return best, list(set(matched))


def _score_skills(
    text: str,
    profile: ResumeProfile,
    weight: int,
) -> tuple[int, list[str]]:
    """Points for skill/keyword mentions in the full job text.

    Returns (score, list_of_matched_skills).
    """
    text_lower = text.lower()
    matched_skills: list[str] = []

    for skill in profile.all_keywords:
        if len(skill) <= 3:
            if re.search(rf"\b{re.escape(skill)}\b", text_lower):
                matched_skills.append(skill)
        elif skill in text_lower:
            matched_skills.append(skill)

    if not profile.all_keywords:
        return 0, []

    ratio = min(len(matched_skills) / len(profile.all_keywords), 1.0)
    primary_matched = [s for s in profile.primary_skills if s.lower() in text_lower]
    primary_ratio = len(primary_matched) / max(len(profile.primary_skills), 1)
    combined = ratio * 0.6 + primary_ratio * 0.4
    score = int(weight * combined)

    # Return display-friendly names (prefer original skill casing)
    skill_lower_map = {s.lower(): s for s in profile.all_skills}
    display_matched = sorted(
        {skill_lower_map.get(s, s) for s in matched_skills},
        key=lambda x: x.lower(),
    )
    return score, display_matched[:12]  # cap at 12 for readability


def _score_projects(
    text: str,
    profile: ResumeProfile,
    weight: int,
) -> tuple[int, list[str]]:
    """Points for project domain phrases and technologies in the job text.

    Returns (score, list_of_matched_project_keywords).
    """
    text_lower = text.lower()
    matched: list[str] = []

    # Check domain phrases first (higher signal)
    for phrase in profile.project_phrases:
        if phrase in text_lower:
            matched.append(phrase)

    # Also check project technologies not already in skills
    extra_techs = profile.project_tech_lower - profile.skills_lower
    for tech in extra_techs:
        if len(tech) <= 3:
            if re.search(rf"\b{re.escape(tech)}\b", text_lower):
                matched.append(tech)
        elif tech in text_lower:
            matched.append(tech)

    all_project_kws = profile.all_project_keywords
    if not all_project_kws:
        return weight // 2, []  # neutral when no project data

    ratio = min(len(matched) / max(len(all_project_kws), 1), 1.0)
    score = int(weight * ratio)
    return score, sorted(set(matched))[:8]


def _score_location(job: JobPosting, profile: ResumeProfile, weight: int) -> int:
    """Points for location match or remote compatibility."""
    loc_lower = job.location.lower()

    if job.remote or "remote" in loc_lower or "worldwide" in loc_lower or "work from home" in loc_lower:
        if profile.remote_ok:
            return weight
        return int(weight * 0.5)

    for term in profile.location_terms:
        if term in loc_lower:
            return weight

    if profile.willing_to_relocate:
        return int(weight * 0.4)

    return 0


def _score_experience(
    text: str,
    profile: ResumeProfile,
    weight: int,
    config: dict,
) -> int:
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


def _score_industry_bonus(text: str, profile: ResumeProfile, max_bonus: int = 5) -> int:
    """Small bonus for jobs that mention target industries."""
    if not profile.target_industries:
        return 0
    text_lower = text.lower()
    matched = sum(1 for ind in profile.target_industries if ind.lower() in text_lower)
    if not matched:
        return 0
    ratio = min(matched / len(profile.target_industries), 1.0)
    return int(max_bonus * ratio)


def score_job(
    job: JobPosting,
    profile: ResumeProfile,
    config: dict,
) -> int:
    """Compute and return a 0–100 relevance score; also populate job.matched_* fields."""
    scoring = config.get("scoring", {})
    title_w    = scoring.get("title_match_weight",    30)
    skills_w   = scoring.get("skills_match_weight",   30)
    projects_w = scoring.get("projects_match_weight", 10)
    loc_w      = scoring.get("location_match_weight", 15)
    exp_w      = scoring.get("experience_match_weight", 15)
    ind_bonus  = scoring.get("industry_bonus_max",     5)

    full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"

    title_score,   job.matched_titles   = _score_title(job.title, profile, title_w)
    skills_score,  job.matched_skills   = _score_skills(full_text, profile, skills_w)
    project_score, job.matched_projects = _score_projects(full_text, profile, projects_w)
    loc_score   = _score_location(job, profile, loc_w)
    exp_score   = _score_experience(full_text, profile, exp_w, config)
    bonus       = _score_industry_bonus(full_text, profile, ind_bonus)

    total = title_score + skills_score + project_score + loc_score + exp_score + bonus
    return min(total, 100)


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------

_JOB_TYPE_ALIASES: dict[str, list[str]] = {
    "full-time":  ["full-time", "full time", "permanent", "regular"],
    "part-time":  ["part-time", "part time"],
    "contract":   ["contract", "contractual", "c2h", "contract to hire", "freelance"],
    "internship": ["intern", "internship", "trainee"],
    "remote":     ["remote", "work from home", "wfh"],
}


def _job_type_matches_profile(job: JobPosting, profile: ResumeProfile) -> bool:
    """Return True if the job's type is acceptable according to profile.job_types."""
    if not profile.job_types:
        return True  # no preference specified → accept all

    job_type_lower = job.job_type.lower()
    # If we can't determine job type, be lenient and include it
    if not job_type_lower or job_type_lower == "unknown":
        return True

    for preferred in profile.job_types:
        aliases = _JOB_TYPE_ALIASES.get(preferred.lower(), [preferred.lower()])
        for alias in aliases:
            if alias in job_type_lower:
                return True

    return False


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

    # Job-type filter: respect profile's desired job types
    if filter_cfg.get("enforce_job_type_filter", True):
        if not _job_type_matches_profile(job, profile):
            logger.debug(
                "Excluded %r – job_type %r not in profile job_types %s",
                job.title, job.job_type, profile.job_types,
            )
            return False

    return True


def _is_recent(job: JobPosting, days_back: int) -> bool:
    """Return True if the job was posted within the last `days_back` days."""
    if not job.posted_at:
        return True

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

    logger.info(
        "Filter pipeline: %d jobs passed (%d total fetched), top %d selected",
        len(filtered),
        len(jobs),
        len(result),
    )
    return result
