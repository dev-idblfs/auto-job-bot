"""
Company career pages scraper.

Reads company_careers.json and scrapes each company's career page
to find open positions matching the candidate's profile.

This is a best-effort scraper – individual career pages vary widely.
It uses BeautifulSoup to extract job titles and links from known
selectors, and falls back to link-text heuristics when selectors fail.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..job_searcher import JobPosting
from .base import BaseJobScraper, get_session, polite_sleep, parse_html

logger = logging.getLogger(__name__)

# Path relative to project root
CAREERS_JSON = Path(__file__).parent.parent.parent / "company_careers.json"

# URL patterns that look like job detail pages
JOB_LINK_PATTERNS = re.compile(
    r"/(job|jobs|careers|opening|openings|position|positions|role|roles|"
    r"vacancy|vacancies|apply|application)/",
    re.I,
)

# Anchor text patterns that signal a job listing link
JOB_TEXT_PATTERNS = re.compile(
    r"\b(engineer|developer|designer|analyst|manager|architect|lead|"
    r"scientist|intern|associate|specialist|director|consultant)\b",
    re.I,
)


class CompanyCareersScraper(BaseJobScraper):
    """Scrapes curated company career pages for matching job postings."""

    name = "Company Careers"

    def fetch(self) -> list[JobPosting]:
        if not CAREERS_JSON.exists():
            logger.warning("company_careers.json not found at %s", CAREERS_JSON)
            return []

        with CAREERS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)

        companies = data.get("companies", [])
        jobs: list[JobPosting] = []
        session = get_session()

        for company in companies:
            if len(jobs) >= self.max_results:
                break
            company_jobs = self._scrape_company(company, session)
            jobs.extend(company_jobs)
            polite_sleep(2.0, 4.0)

        return jobs

    def _scrape_company(self, company: dict, session) -> list[JobPosting]:
        name = company.get("name", "Unknown")
        url = company.get("careers_url", "")
        selector = company.get("job_list_selector", "")
        location = company.get("location", "India")

        if not url:
            return []

        try:
            resp = session.get(url, timeout=25)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("Career page %s (%s) failed: %s", name, url, exc)
            return []

        soup = parse_html(resp.text)
        jobs: list[JobPosting] = []

        # Strategy 1: Use provided CSS selector
        if selector:
            for sel in selector.split(","):
                try:
                    elements = soup.select(sel.strip())
                    for el in elements:
                        job = _element_to_job(el, name, location, url)
                        if job:
                            jobs.append(job)
                except Exception:
                    continue

        # Strategy 2: Fallback – find all job-like links on the page
        if not jobs:
            jobs = _extract_job_links(soup, name, location, url)

        # Filter by relevance to profile queries
        matched = [j for j in jobs if _matches_queries(j, self.queries)]
        logger.debug("Company %s: found %d jobs, %d matched queries", name, len(jobs), len(matched))
        return matched[:10]  # Cap per company


def _element_to_job(el, company: str, location: str, base_url: str) -> JobPosting | None:
    """Convert a scraped HTML element into a JobPosting."""
    link_el = el if el.name == "a" else el.find("a", href=True)
    if not link_el:
        return None

    href = link_el.get("href", "")
    if not href:
        return None

    apply_url = href if href.startswith("http") else _build_absolute(base_url, href)

    # Title: try heading tags first, then link text
    title_el = el.find(["h1", "h2", "h3", "h4", "span", "p"], recursive=True)
    title = (title_el or link_el).get_text(separator=" ", strip=True)

    if not title or len(title) < 3:
        return None

    # Must look like a job title (not a nav link)
    if len(title) > 120:
        title = title[:120]

    return JobPosting(
        id=f"careers-{abs(hash(apply_url))}",
        title=title,
        company=company,
        location=location,
        remote="remote" in location.lower() or "remote" in title.lower(),
        job_type="full-time",
        description="",
        apply_url=apply_url,
        posted_at="",
        salary="",
        source=f"Careers ({company})",
        tags=[],
    )


def _extract_job_links(soup, company: str, location: str, base_url: str) -> list[JobPosting]:
    """Fallback: find all <a> tags that look like job listings."""
    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(separator=" ", strip=True)

        if not text or not href:
            continue

        full_url = href if href.startswith("http") else _build_absolute(base_url, href)

        if full_url in seen_urls:
            continue

        # Check if link looks job-related
        if not (JOB_LINK_PATTERNS.search(full_url) and JOB_TEXT_PATTERNS.search(text)):
            continue

        seen_urls.add(full_url)
        jobs.append(
            JobPosting(
                id=f"careers-{abs(hash(full_url))}",
                title=text[:120],
                company=company,
                location=location,
                remote="remote" in location.lower() or "remote" in text.lower(),
                job_type="full-time",
                description="",
                apply_url=full_url,
                posted_at="",
                salary="",
                source=f"Careers ({company})",
                tags=[],
            )
        )

    return jobs


def _matches_queries(job: JobPosting, queries: list[str]) -> bool:
    """Return True if the job title matches any search query."""
    title_lower = job.title.lower()
    for q in queries:
        words = q.lower().split()
        if any(w in title_lower for w in words if len(w) > 2):
            return True
    return False


def _build_absolute(base_url: str, href: str) -> str:
    """Convert a relative URL to absolute using the base URL."""
    if href.startswith("//"):
        scheme = base_url.split(":")[0] if ":" in base_url else "https"
        return f"{scheme}:{href}"
    if href.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    return href
