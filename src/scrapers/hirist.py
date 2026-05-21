"""
Hirist.tech scraper – India's dedicated tech job board.

Hirist serves a JSON API at their search endpoint used by their own SPA.
"""

from __future__ import annotations

import logging

from ..job_searcher import JobPosting
from .base import BaseJobScraper, http_get, polite_sleep

logger = logging.getLogger(__name__)

HIRIST_API = "https://www.hirist.tech/api/v1/jobs"
HIRIST_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://www.hirist.tech/",
    "Origin": "https://www.hirist.tech",
}


class HiristScraper(BaseJobScraper):
    """Fetches tech jobs from Hirist.tech."""

    name = "Hirist"

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []

        for query in self.queries:
            if len(jobs) >= self.max_results:
                break

            page = 1
            while len(jobs) < self.max_results:
                try:
                    data = http_get(
                        HIRIST_API,
                        params={
                            "q": query,
                            "page": page,
                            "limit": 20,
                            "sort": "date",
                        },
                        headers=HIRIST_HEADERS,
                        as_json=True,
                    )
                except Exception as exc:
                    logger.error("Hirist API error for %r: %s", query, exc)
                    break

                # Hirist API response shape may vary; handle gracefully
                items = (
                    data.get("jobs")
                    or data.get("data", {}).get("jobs")
                    or data.get("results")
                    or []
                )
                if not items:
                    break

                for item in items:
                    job_id = str(item.get("id", item.get("jobId", "")))
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)

                    slug = item.get("slug", job_id)
                    apply_url = (
                        item.get("url")
                        or item.get("applyUrl")
                        or f"https://www.hirist.tech/j/{slug}"
                    )

                    skills = item.get("skills") or item.get("tags") or []
                    if isinstance(skills, str):
                        skills = [s.strip() for s in skills.split(",")]

                    jobs.append(
                        JobPosting(
                            id=f"hirist-{job_id}",
                            title=item.get("title", item.get("jobTitle", "")),
                            company=item.get("company", {}).get("name", "")
                            if isinstance(item.get("company"), dict)
                            else item.get("company", ""),
                            location=item.get("location", "India"),
                            remote=_is_remote(item),
                            job_type=item.get("employmentType", "full-time"),
                            description=item.get("description", ""),
                            apply_url=apply_url,
                            posted_at=item.get("postedAt", item.get("createdAt", "")),
                            salary=_extract_salary(item),
                            source="Hirist",
                            tags=[s.lower() for s in skills if s],
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                page += 1
                total_pages = data.get("totalPages") or data.get("pages", 1)
                if page > total_pages:
                    break
                polite_sleep(1.0, 2.5)

            polite_sleep(2.0, 3.5)

        return jobs


def _is_remote(item: dict) -> bool:
    loc = item.get("location", "").lower()
    title = item.get("title", item.get("jobTitle", "")).lower()
    return "remote" in loc or "remote" in title or item.get("isRemote", False)


def _extract_salary(item: dict) -> str:
    sal = item.get("salary") or item.get("ctc") or {}
    if isinstance(sal, dict):
        min_s = sal.get("min")
        max_s = sal.get("max")
        curr = sal.get("currency", "INR")
        if min_s and max_s:
            return f"{curr} {min_s} – {max_s} LPA"
        if min_s:
            return f"{curr} {min_s}+ LPA"
    if isinstance(sal, str) and sal:
        return sal
    return ""
