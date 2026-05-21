"""
Internshala scraper – entry-level jobs and fresher openings in India.

Useful for junior/entry-level candidates. Scrapes the public jobs section
(not internships) of internshala.com.
"""

from __future__ import annotations

import logging
import re

from ..job_searcher import JobPosting
from .base import BaseJobScraper, get_session, polite_sleep, parse_html

logger = logging.getLogger(__name__)

BASE_URL = "https://internshala.com"
JOBS_URL = f"{BASE_URL}/jobs"


class IntershalaJobsScraper(BaseJobScraper):
    """Scrapes jobs from Internshala (good for fresher / entry-level)."""

    name = "Internshala"

    def fetch(self) -> list[JobPosting]:
        # Only run for junior/entry-level profiles
        if getattr(self.profile, "years_experience", 0) > 3:
            logger.info("Internshala skipped – profile has >3 years experience")
            return []

        seen: set[str] = set()
        jobs: list[JobPosting] = []
        session = get_session(
            extra_headers={"Referer": BASE_URL + "/"}
        )

        for query in self.queries[:3]:  # Limit to 3 queries for Internshala
            if len(jobs) >= self.max_results:
                break

            slug = "-".join(query.lower().split())
            url = f"{JOBS_URL}/{slug}-jobs"

            try:
                resp = session.get(url, timeout=20)
                if resp.status_code == 404:
                    url = f"{JOBS_URL}?keywords={query}"
                    resp = session.get(url, timeout=20)
                resp.raise_for_status()
            except Exception as exc:
                logger.error("Internshala error for %r: %s", query, exc)
                continue

            soup = parse_html(resp.text)
            new_jobs = _parse_internshala_page(soup, seen, query)
            jobs.extend(new_jobs)
            polite_sleep(2.0, 4.0)

        return jobs


def _parse_internshala_page(soup, seen: set[str], query: str) -> list[JobPosting]:
    jobs: list[JobPosting] = []

    # Internshala job cards
    cards = soup.find_all(
        "div",
        attrs={"class": re.compile(r"individual_internship|job_card|container-shadow", re.I)},
    )

    for card in cards:
        # Extract job ID from data attribute or link
        link_el = card.find("a", href=True)
        href = link_el["href"] if link_el else ""
        m = re.search(r"/(\d+)/?", href)
        job_id = m.group(1) if m else abs(hash(href))

        unique_id = f"internshala-{job_id}"
        if unique_id in seen:
            continue
        seen.add(unique_id)

        apply_url = href if href.startswith("http") else f"https://internshala.com{href}"

        title_el = card.find(
            ["h3", "h2", "a"],
            attrs={"class": re.compile(r"title|heading|job-title", re.I)},
        )
        company_el = card.find(
            attrs={"class": re.compile(r"company|organization", re.I)}
        )
        location_el = card.find(
            attrs={"class": re.compile(r"location|city", re.I)}
        )
        salary_el = card.find(
            attrs={"class": re.compile(r"stipend|salary|ctc", re.I)}
        )

        title = title_el.get_text(strip=True) if title_el else query
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else "India"
        salary = salary_el.get_text(strip=True) if salary_el else ""

        jobs.append(
            JobPosting(
                id=unique_id,
                title=title,
                company=company,
                location=location,
                remote="remote" in location.lower() or "work from home" in location.lower(),
                job_type="full-time",
                description="",
                apply_url=apply_url,
                posted_at="",
                salary=salary,
                source="Internshala",
                tags=[],
            )
        )

    return jobs
