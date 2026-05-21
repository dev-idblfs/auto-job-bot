"""
Foundit.in (formerly Monster India) scraper.

Uses Foundit's internal REST API which is publicly accessible.
"""

from __future__ import annotations

import logging

from ..job_searcher import JobPosting
from .base import BaseJobScraper, http_get, polite_sleep

logger = logging.getLogger(__name__)

FOUNDIT_SEARCH_API = "https://www.foundit.in/middleware/jobsearch/v1/search"
FOUNDIT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.foundit.in",
    "Referer": "https://www.foundit.in/",
}


class FounditScraper(BaseJobScraper):
    """Fetches jobs from Foundit.in (formerly Monster India)."""

    name = "Foundit"

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []

        location = getattr(self.profile, "city", "") or "India"
        exp_years = getattr(self.profile, "years_experience", 0)

        for query in self.queries:
            if len(jobs) >= self.max_results:
                break

            start = 0
            while len(jobs) < self.max_results:
                params = {
                    "query": query,
                    "locationName": location,
                    "limit": min(25, self.max_results - len(jobs)),
                    "start": start,
                    "postedDate": "1",  # Last 1 day
                    "sort": "1",  # Sort by date
                }

                try:
                    data = http_get(
                        FOUNDIT_SEARCH_API,
                        params=params,
                        headers=FOUNDIT_HEADERS,
                        as_json=True,
                    )
                except Exception as exc:
                    logger.error("Foundit API error for %r: %s", query, exc)
                    break

                job_list = data.get("jobSearchResponse", {}).get("data", []) or []
                if not job_list:
                    break

                for item in job_list:
                    job_id = str(item.get("jobId", item.get("id", "")))
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)

                    apply_url = item.get("applyUrl", "") or (
                        f"https://www.foundit.in/job/{item.get('jobTitleSlug', job_id)}-{job_id}"
                    )

                    jobs.append(
                        JobPosting(
                            id=f"foundit-{job_id}",
                            title=item.get("jobTitle", ""),
                            company=item.get("companyName", ""),
                            location=_format_location(item),
                            remote=_is_remote(item),
                            job_type=_map_employment_type(item),
                            description=item.get("jobDescription", ""),
                            apply_url=apply_url,
                            posted_at=item.get("postedDate", ""),
                            salary=_extract_salary(item),
                            source="Foundit",
                            tags=[s.lower() for s in item.get("keySkills", [])],
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                total = data.get("jobSearchResponse", {}).get("totalCount", 0)
                start += 25
                if start >= min(total, 100):
                    break
                polite_sleep(1.5, 3.0)

            polite_sleep(2.0, 4.0)

        return jobs


def _format_location(item: dict) -> str:
    locs = item.get("location", [])
    if isinstance(locs, list):
        return ", ".join(str(l) for l in locs[:3]) if locs else "India"
    return str(locs) or "India"


def _is_remote(item: dict) -> bool:
    loc = _format_location(item).lower()
    title = item.get("jobTitle", "").lower()
    wfh = item.get("isWorkFromHome", False)
    return wfh or "remote" in loc or "work from home" in loc or "remote" in title


def _map_employment_type(item: dict) -> str:
    etype = str(item.get("employmentType", "")).lower()
    if "contract" in etype:
        return "contract"
    if "part" in etype:
        return "part-time"
    if "freelance" in etype:
        return "freelance"
    return "full-time"


def _extract_salary(item: dict) -> str:
    min_sal = item.get("minSalary")
    max_sal = item.get("maxSalary")
    currency = item.get("salaryCurrency", "INR")
    if min_sal and max_sal:
        return f"{currency} {int(min_sal):,} – {int(max_sal):,}"
    if min_sal:
        return f"{currency} {int(min_sal):,}+"
    return ""
