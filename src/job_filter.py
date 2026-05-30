"""
Job filter & scorer: ranks job postings by relevance to the resume profile.

Scoring breakdown (weights configurable in config.yaml; must sum to 100):
  - Title match    (30 pts): job title vs. candidate's desired titles
  - Skills match   (30 pts): resume skills found in the job posting text
  - Projects match (10 pts): project technologies / domain phrases in posting
  - Location       (15 pts): location match or remote compatibility
  - Experience     (15 pts): experience-level language matches profile level
  - Industry bonus (0-5 pts): bonus for preferred industries (capped at 100 total)
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
# Industry keyword map for bonus scoring
# ---------------------------------------------------------------------------

INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "fintech":     ["fintech", "financial technology", "payments", "banking", "insurtech", "wealthtech"],
    "saas":        ["saas", "software as a service", "b2b software", "cloud software"],
    "ai/ml":       ["artificial intelligence", "machine learning", "deep learning", "nlp", "computer vision", "llm", "generative ai"],
    "startup":     ["startup", "early stage", "series a", "series b", "pre-ipo", "funded startup"],
    "ecommerce":   ["e-commerce", "ecommerce", "online marketplace", "retail tech"],
    "healthtech":  ["healthtech", "health technology", "medtech", "digital health"],
    "edtech":      ["edtech", "education technology", "e-learning"],
    "logistics":   ["logistics", "supply chain", "delivery tech"],
}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_title(job_title: str, profile: ResumeProfile, weight: int) -> tuple[int, list[str]]:
    """Points for title similarity to desired titles. Also returns matched titles."""
    jt = job_title.lower()
    best = 0
    matched: list[str] = []
    for desired in profile.target_titles:
        d = desired.lower()
        if d == jt:
            score = weight
        elif d in jt or jt in d:
            score = int(weight * 0.8)
        else:
            d_words = set(d.split())
            j_words = set(jt.split())
            overlap = d_words & j_words
            score = int(weight * len(overlap) / max(len(d_words), len(j_words)) * 0.7) if overlap else 0

        if score > 0 and desired not in matched:
            matched.append(desired)
        best = max(best, score)
    return best, matched


def _score_skills(text: str, profile: ResumeProfile, weight: int) -> tuple[int, list[str]]:
    """Points for skill mentions in the full job text. Returns (score, matched_skills)."""
    text_lower = text.lower()
    matched: list[str] = []

    for skill in profile.all_keywords:
        if len(skill) <= 3:
            if re.search(rf"\b{re.escape(skill)}\b", text_lower):
                matched.append(skill)
        elif skill in text_lower:
            matched.append(skill)

    if not profile.all_keywords:
        return 0, []

    ratio = min(len(matched) / len(profile.all_keywords), 1.0)
    primary_matched = [s for s in profile.primary_skills if s.lower() in text_lower]
    primary_ratio = len(primary_matched) / max(len(profile.primary_skills), 1)
    combined = ratio * 0.6 + primary_ratio * 0.4

    # Use display-friendly skill names (original case from primary_skills)
    display_skills: list[str] = []
    for s in profile.all_skills:
        if s.lower() in matched:
            display_skills.append(s)

    return int(weight * combined), display_skills[:10]


def _score_projects(text: str, profile: ResumeProfile, weight: int) -> tuple[int, list[str]]:
    """
    Points for project domain phrases and project technologies appearing in
    the job description. Returns (score, matched_project_terms).
    """
    text_lower = text.lower()
    matched: list[str] = []

    # Check project technologies
    for tech in profile.project_technologies:
        if tech.lower() in text_lower:
            matched.append(tech)

    # Check multi-word domain phrases extracted from project descriptions
    for phrase in profile.project_phrases:
        if phrase in text_lower:
            matched.append(phrase)

    if not (profile.project_technologies or profile.project_phrases):
        return 0, []

    total = len(profile.project_technologies) + len(profile.project_phrases)
    ratio = min(len(matched) / total, 1.0) if total else 0.0
    return int(weight * ratio), list(dict.fromkeys(matched))[:8]


def _score_location(job: JobPosting, profile: ResumeProfile, weight: int) -> int:
    """Points for location match or remote compatibility."""
    loc_lower = job.location.lower()

    if job.remote or "remote" in loc_lower or "worldwide" in loc_lower:
        return weight if profile.remote_ok else int(weight * 0.5)

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
    """Bonus points when the job is in one of the candidate's preferred industries."""
    if not profile.target_industries:
        return 0
    text_lower = text.lower()
    for industry in profile.target_industries:
        kws = INDUSTRY_KEYWORDS.get(industry.lower(), [industry.lower()])
        for kw in kws:
            if kw in text_lower:
                return max_bonus
    return 0


def score_job(job: JobPosting, profile: ResumeProfile, config: dict) -> int:
    """
    Compute and store a 0-100 relevance score for a job posting.
    Also populates job.matched_skills, job.matched_projects, job.matched_titles.
    """
    scoring = config.get("scoring", {})
    title_w    = scoring.get("title_match_weight", 30)
    skills_w   = scoring.get("skills_match_weight", 30)
    projects_w = scoring.get("projects_match_weight", 10)
    loc_w      = scoring.get("location_match_weight", 15)
    exp_w      = scoring.get("experience_match_weight", 15)
    ind_bonus  = scoring.get("industry_bonus_max", 5)

    full_text = f"{job.title} {job.company} {job.description} {' '.join(job.tags)}"

    title_score, matched_titles   = _score_title(job.title, profile, title_w)
    skills_score, matched_skills  = _score_skills(full_text, profile, skills_w)
    proj_score, matched_projects  = _score_projects(full_text, profile, projects_w)
    loc_score                     = _score_location(job, profile, loc_w)
    exp_score                     = _score_experience(full_text, profile, exp_w, config)
    bonus                         = _score_industry_bonus(full_text, profile, ind_bonus)

    job.matched_titles   = matched_titles
    job.matched_skills   = matched_skills
    job.matched_projects = matched_projects

    return min(title_score + skills_score + proj_score + loc_score + exp_score + bonus, 100)


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

    # Job-type hard filter: only keep jobs matching the profile's desired job types
    if profile.job_types:
        desired_types = {jt.lower() for jt in profile.job_types}
        job_type_lower = (job.job_type or "").lower()
        # Normalise common variants
        _type_map = {
            "full-time": {"full-time", "fulltime", "full_time", "permanent"},
            "contract": {"contract", "contractor", "fixed-term"},
            "part-time": {"part-time", "parttime", "part_time"},
            "internship": {"internship", "intern", "trainee"},
            "remote": {"remote", "wfh", "work from home"},
        }
        matched_type = False
        for desired in desired_types:
            variants = _type_map.get(desired, {desired})
            if any(v in job_type_lower for v in variants):
                matched_type = True
                break
            # Also check title/description for job-type clues when API data is vague
            if any(v in text_lower for v in variants):
                matched_type = True
                break
        if not matched_type and job_type_lower not in ("", "full-time"):
            logger.debug(
                "Excluded %r – job_type %r not in desired types %s",
                job.title, job.job_type, desired_types,
            )
            return False

    # Remote-only filter
    if filter_cfg.get("remote_only", False):
        if not job.remote and "remote" not in job.location.lower():
            return False

    # Explicit location override filter
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

    return True


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
