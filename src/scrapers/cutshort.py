"""
Cutshort.io scraper – tech & startup jobs in India.

Cutshort uses a GraphQL API. We send a public query that doesn't require auth.
"""

from __future__ import annotations

import logging

import requests

from ..job_searcher import JobPosting
from .base import BaseJobScraper, get_session, polite_sleep

logger = logging.getLogger(__name__)

CUTSHORT_GQL = "https://cutshort.io/api/graphql"

JOB_SEARCH_QUERY = """
query SearchJobs($input: JobSearchInput!) {
  searchJobs(input: $input) {
    jobs {
      id
      title
      company {
        name
        websiteUrl
      }
      locations
      skills
      minExp
      maxExp
      minCtc
      maxCtc
      jobType
      isRemote
      description
      applicationUrl
      createdAt
    }
    totalCount
  }
}
"""


class CutshortScraper(BaseJobScraper):
    """Fetches jobs from Cutshort.io via their GraphQL API."""

    name = "Cutshort"

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []
        session = get_session(
            extra_headers={
                "Content-Type": "application/json",
                "Origin": "https://cutshort.io",
                "Referer": "https://cutshort.io/jobs",
            }
        )

        for query in self.queries:
            if len(jobs) >= self.max_results:
                break

            skip = 0
            limit = 20
            while len(jobs) < self.max_results:
                variables = {
                    "input": {
                        "keyword": query,
                        "locations": ["India"],
                        "limit": limit,
                        "skip": skip,
                    }
                }

                try:
                    resp = session.post(
                        CUTSHORT_GQL,
                        json={"query": JOB_SEARCH_QUERY, "variables": variables},
                        timeout=20,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.error("Cutshort GQL error for %r: %s", query, exc)
                    break

                result = (
                    data.get("data", {}).get("searchJobs", {}) or {}
                )
                items = result.get("jobs", []) or []

                if not items:
                    break

                for item in items:
                    job_id = str(item.get("id", ""))
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)

                    apply_url = (
                        item.get("applicationUrl")
                        or f"https://cutshort.io/job/{job_id}"
                    )

                    skills = item.get("skills") or []
                    locations = item.get("locations") or []

                    min_ctc = item.get("minCtc")
                    max_ctc = item.get("maxCtc")
                    salary = ""
                    if min_ctc and max_ctc:
                        salary = f"₹{min_ctc} – ₹{max_ctc} LPA"
                    elif min_ctc:
                        salary = f"₹{min_ctc}+ LPA"

                    jobs.append(
                        JobPosting(
                            id=f"cutshort-{job_id}",
                            title=item.get("title", ""),
                            company=item.get("company", {}).get("name", ""),
                            location=", ".join(locations) or "India",
                            remote=item.get("isRemote", False),
                            job_type=item.get("jobType", "full-time"),
                            description=item.get("description", ""),
                            apply_url=apply_url,
                            posted_at=item.get("createdAt", ""),
                            salary=salary,
                            source="Cutshort",
                            tags=[s.lower() for s in skills if s],
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                total = result.get("totalCount", 0)
                skip += limit
                if skip >= min(total, 100):
                    break
                polite_sleep(1.5, 3.0)

            polite_sleep(2.0, 4.0)

        return jobs
