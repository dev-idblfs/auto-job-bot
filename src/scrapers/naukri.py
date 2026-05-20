"""
Naukri.com scraper – uses Naukri's internal search API.

The API endpoint is used by Naukri's own website; no auth token required
but specific app headers are needed.
"""

from __future__ import annotations

import logging
import re

from ..job_searcher import JobPosting
from .base import BaseJobScraper, http_get, polite_sleep

logger = logging.getLogger(__name__)

NAUKRI_API = "https://www.naukri.com/jobapi/v3/search"
NAUKRI_HEADERS = {
    "appid": "109",
    "systemid": "109",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.naukri.com/",
    "Origin": "https://www.naukri.com",
}


class NaukriScraper(BaseJobScraper):
    """Fetches jobs from Naukri.com via their internal search API."""

    name = "Naukri"

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []

        # Determine location filter from profile
        location = _build_location(self.profile)

        for query in self.queries:
            if len(jobs) >= self.max_results:
                break

            page_no = 1
            while len(jobs) < self.max_results:
                params = {
                    "noOfResults": min(20, self.max_results - len(jobs)),
                    "urlType": "search_by_keyword",
                    "searchType": "adv",
                    "keyword": query,
                    "location": location,
                    "pageNo": page_no,
                    "jobAge": 1,  # Posted in last 1 day
                    "sort": "r",  # Sort by relevance
                }

                try:
                    data = http_get(
                        NAUKRI_API,
                        params=params,
                        headers=NAUKRI_HEADERS,
                        as_json=True,
                    )
                except Exception as exc:
                    logger.error("Naukri API error for %r (page %d): %s", query, page_no, exc)
                    break

                job_list = data.get("jobDetails", []) or []
                if not job_list:
                    break

                for item in job_list:
                    job_id = str(item.get("jobId", ""))
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)

                    # Extract salary info
                    salary = _extract_salary(item)

                    # Skills / tags
                    tags = [s.lower() for s in item.get("tagsAndSkills", "").split(",") if s.strip()]

                    jobs.append(
                        JobPosting(
                            id=f"naukri-{job_id}",
                            title=item.get("title", ""),
                            company=item.get("companyName", ""),
                            location=_format_location(item),
                            remote=_is_remote(item),
                            job_type=_map_job_type(item),
                            description=item.get("jobDescription", ""),
                            apply_url=item.get("jdURL", f"https://www.naukri.com/job-listings-{job_id}"),
                            posted_at=_parse_posted_date(item),
                            salary=salary,
                            source="Naukri",
                            tags=tags,
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                total = data.get("noOfJobs", 0)
                if page_no * 20 >= min(total, 100):  # Cap at 100 results per query
                    break
                page_no += 1
                polite_sleep(1.5, 3.0)

            polite_sleep(2.0, 4.0)

        return jobs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_location(profile) -> str:
    """Build location string for Naukri search."""
    if hasattr(profile, "city") and profile.city:
        return profile.city
    return ""


def _extract_salary(item: dict) -> str:
    label = item.get("placeholders", [])
    for ph in label:
        if ph.get("type") == "salary":
            return ph.get("label", "")
    return ""


def _format_location(item: dict) -> str:
    placeholders = item.get("placeholders", [])
    for ph in placeholders:
        if ph.get("type") == "location":
            return ph.get("label", "")
    return item.get("location", "India")


def _is_remote(item: dict) -> bool:
    loc = _format_location(item).lower()
    title = item.get("title", "").lower()
    return "remote" in loc or "work from home" in loc or "wfh" in loc or "remote" in title


def _map_job_type(item: dict) -> str:
    wfh = item.get("isWFHJob", False)
    if wfh:
        return "remote"
    return "full-time"


def _parse_posted_date(item: dict) -> str:
    """Convert Naukri's footerPlaceholderLabel to ISO-ish date."""
    for ph in item.get("footerPlaceholderLabel", []):
        label = ph.get("label", "")
        if "ago" in label.lower() or "day" in label.lower():
            return label
    return ""
