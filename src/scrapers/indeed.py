"""
Indeed India scraper – scrapes in.indeed.com job listings.

Indeed actively blocks bots. This scraper uses browser-like headers,
random delays, and graceful fallback when blocked.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from ..job_searcher import JobPosting
from .base import BaseJobScraper, detect_job_type, get_session, polite_sleep, parse_html

logger = logging.getLogger(__name__)

BASE_URL = "https://in.indeed.com"
SEARCH_URL = f"{BASE_URL}/jobs"


class IndeedIndiaScraper(BaseJobScraper):
    """Scrapes Indeed India job listings."""

    name = "Indeed India"

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []

        session = get_session(
            extra_headers={
                "Referer": "https://in.indeed.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

        # Warm up the session by visiting the homepage first
        try:
            session.get(BASE_URL, timeout=10)
            polite_sleep(1.0, 2.0)
        except Exception:
            pass

        location = getattr(self.profile, "city", "") or "India"

        for query in self.queries:
            if len(jobs) >= self.max_results:
                break

            start = 0
            while len(jobs) < self.max_results:
                params = {
                    "q": query,
                    "l": location,
                    "fromage": "1",  # Last 1 day
                    "start": start,
                    "sort": "date",
                }

                try:
                    resp = session.get(SEARCH_URL, params=params, timeout=20)
                    resp.raise_for_status()
                except Exception as exc:
                    logger.error("Indeed India error for %r (start=%d): %s", query, start, exc)
                    break

                # Indeed sometimes serves a CAPTCHA/block page
                if "captcha" in resp.text.lower() or "robot" in resp.text.lower():
                    logger.warning("Indeed India: bot detection triggered for %r", query)
                    break

                soup = parse_html(resp.text)
                new_jobs = _parse_indeed_page(soup, seen)

                if not new_jobs:
                    break

                for job in new_jobs:
                    jobs.append(job)
                    seen.add(job.id)
                    if len(jobs) >= self.max_results:
                        break

                start += 15
                if start >= 60:
                    break
                polite_sleep(2.0, 5.0)

            polite_sleep(3.0, 6.0)

        return jobs


def _parse_indeed_page(soup, seen: set[str]) -> list[JobPosting]:
    """Parse Indeed search results HTML into JobPosting objects."""
    jobs: list[JobPosting] = []

    # Indeed uses data-jk attribute for job keys
    cards = soup.find_all("div", attrs={"data-jk": True})

    for card in cards:
        jk = card.get("data-jk", "")
        job_id = f"indeed-{jk}"
        if not jk or job_id in seen:
            continue

        title = _indeed_text(card, [
            {"class": re.compile(r"jobTitle|title", re.I)},
        ])
        company = _indeed_text(card, [
            {"data-testid": "company-name"},
            {"class": re.compile(r"companyName|company", re.I)},
        ])
        location = _indeed_text(card, [
            {"data-testid": "text-location"},
            {"class": re.compile(r"companyLocation|location", re.I)},
        ])
        salary = _indeed_text(card, [
            {"class": re.compile(r"salary|compensation", re.I)},
        ])
        snippet = _indeed_text(card, [
            {"class": re.compile(r"summary|snippet", re.I)},
        ])
        date_el = card.find(["span", "div"], attrs={"class": re.compile(r"date|posted", re.I)})
        posted = date_el.get_text(strip=True) if date_el else ""

        apply_url = f"https://in.indeed.com/viewjob?jk={jk}"

        jobs.append(
            JobPosting(
                id=job_id,
                title=title,
                company=company,
                location=location or "India",
                remote="remote" in location.lower() or "work from home" in location.lower(),
                job_type=detect_job_type(title, snippet),
                description=snippet,
                apply_url=apply_url,
                posted_at=posted,
                salary=salary,
                source="Indeed India",
                tags=[],
            )
        )

    return jobs


def _indeed_text(card, selector_list: list[dict]) -> str:
    """Try multiple selectors to extract text from a card."""
    for attrs in selector_list:
        el = card.find(attrs=attrs)
        if el:
            return el.get_text(separator=" ", strip=True)
    return ""
