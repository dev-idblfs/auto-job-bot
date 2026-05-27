"""
Job searcher: aggregates results from all India-specific job scrapers.

Sources:
  - LinkedIn Jobs (public guest API, no auth)
  - LinkedIn Posts (optional, requires credentials)
  - Naukri.com (internal API)
  - Indeed India (HTML scraping)
  - Foundit.in / Monster India (internal API)
  - Hirist.tech (tech jobs API)
  - Cutshort.io (GraphQL API)
  - Internshala (entry-level, HTML scraping)
  - Company Career Pages (curated list, HTML scraping)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class JobPosting:
    """Normalised job posting shared across all scrapers."""

    id: str
    title: str
    company: str
    location: str
    remote: bool
    job_type: str
    description: str
    apply_url: str
    posted_at: str
    salary: str
    source: str
    tags: list[str] = field(default_factory=list)
    relevance_score: int = 0
    # Profile-match tracking (populated by job_filter)
    matched_skills: list[str] = field(default_factory=list)
    matched_projects: list[str] = field(default_factory=list)
    matched_titles: list[str] = field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JobPosting) and self.id == other.id

    @property
    def short_description(self) -> str:
        text = " ".join(self.description.split())
        return text[:400] + ("…" if len(text) > 400 else "")


def fetch_all_jobs(profile: Any, config: dict) -> list[JobPosting]:
    """
    Fetch jobs from all enabled India-specific sources and return a
    deduplicated list.

    Scraper configuration lives under config.yaml → search.sources.
    """
    # Import scrapers here to avoid circular imports at module load time
    from .scrapers import (
        LinkedInJobsScraper,
        LinkedInPostsScraper,
        NaukriScraper,
        IndeedIndiaScraper,
        FounditScraper,
        HiristScraper,
        CutshortScraper,
        IntershalaJobsScraper,
        CompanyCareersScraper,
    )

    search_cfg = config.get("search", {})
    sources_cfg = search_cfg.get("sources", {})

    # Build search queries from resume profile + config overrides
    filter_cfg = config.get("filters", {})
    title_overrides: list[str] = filter_cfg.get("job_titles", [])
    queries: list[str] = title_overrides if title_overrides else profile.get_search_queries()

    # Map source name → (scraper_class, enabled_key)
    scraper_registry = [
        (LinkedInJobsScraper,    "linkedin_jobs"),
        (LinkedInPostsScraper,   "linkedin_posts"),
        (NaukriScraper,          "naukri"),
        (IndeedIndiaScraper,     "indeed"),
        (FounditScraper,         "foundit"),
        (HiristScraper,          "hirist"),
        (CutshortScraper,        "cutshort"),
        (IntershalaJobsScraper,  "internshala"),
        (CompanyCareersScraper,  "company_careers"),
    ]

    all_jobs: list[JobPosting] = []

    for ScraperClass, source_key in scraper_registry:
        # Default: all sources enabled except ones we flag as opt-in
        default_on = source_key not in ("linkedin_posts",)
        if not sources_cfg.get(source_key, default_on):
            logger.info("Source %r disabled in config", source_key)
            continue

        scraper = ScraperClass(queries=queries, profile=profile, config=config)
        jobs = scraper.safe_fetch()
        all_jobs.extend(jobs)

    # Deduplicate by ID
    unique: dict[str, JobPosting] = {}
    for job in all_jobs:
        if job.id not in unique:
            unique[job.id] = job

    # Secondary dedup: same title + company (different IDs across sources)
    title_company_seen: set[str] = set()
    final: list[JobPosting] = []
    for job in unique.values():
        key = f"{job.title.lower().strip()}|{job.company.lower().strip()}"
        if key in title_company_seen:
            continue
        title_company_seen.add(key)
        final.append(job)

    logger.info(
        "Total unique jobs fetched: %d (from %d raw across all sources)",
        len(final),
        len(all_jobs),
    )
    return final
