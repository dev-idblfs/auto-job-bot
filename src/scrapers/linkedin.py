"""
LinkedIn scraper – two modes:

1. **Jobs** (no auth): Uses LinkedIn's public guest job search API.
   Endpoint: https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search

2. **Posts** (optional auth): Searches LinkedIn posts for job links using
   the unofficial linkedin-api library when LINKEDIN_EMAIL + LINKEDIN_PASSWORD
   env vars are set. Falls back gracefully if credentials are absent.
"""

from __future__ import annotations

import logging
import os
import re
import time
from urllib.parse import urlencode, urlparse

from ..job_searcher import JobPosting
from .base import BaseJobScraper, detect_job_type, get_session, http_get, polite_sleep, parse_html

logger = logging.getLogger(__name__)

GUEST_JOBS_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
GUEST_JOB_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


class LinkedInJobsScraper(BaseJobScraper):
    """Scrapes LinkedIn job listings using the public guest API (no auth)."""

    name = "LinkedIn Jobs"

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []

        for query in self.queries:
            if len(jobs) >= self.max_results:
                break

            start = 0
            while len(jobs) < self.max_results:
                try:
                    resp = http_get(
                        GUEST_JOBS_API,
                        params={
                            "keywords": query,
                            "location": "India",
                            "f_TPR": "r86400",  # Posted in last 24 hours
                            "start": start,
                        },
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "en-IN,en;q=0.9",
                            "Referer": "https://www.linkedin.com/jobs/search/",
                        },
                    )
                except Exception as exc:
                    logger.error("LinkedIn guest API error for %r: %s", query, exc)
                    break

                soup = parse_html(resp.text)
                cards = soup.find_all("div", {"class": re.compile(r"base-card|job-search-card")})

                if not cards:
                    break

                for card in cards:
                    job_id = _extract_li_job_id(card)
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)

                    title_el = card.find(["h3", "span"], {"class": re.compile(r"title|job-title", re.I)})
                    company_el = card.find(["h4", "a"], {"class": re.compile(r"company|subtitle", re.I)})
                    location_el = card.find(["span", "div"], {"class": re.compile(r"location", re.I)})
                    link_el = card.find("a", href=True)
                    date_el = card.find(["time", "span"], {"class": re.compile(r"date|listed", re.I)})

                    apply_url = ""
                    if link_el:
                        href = link_el["href"]
                        apply_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"

                    title_text = _text(title_el)
                    jobs.append(
                        JobPosting(
                            id=f"linkedin-{job_id}",
                            title=title_text,
                            company=_text(company_el),
                            location=_text(location_el),
                            remote="remote" in _text(location_el).lower(),
                            job_type=detect_job_type(title_text, ""),
                            description="",
                            apply_url=apply_url,
                            posted_at=card.find("time", {"datetime": True})["datetime"]
                            if card.find("time", {"datetime": True})
                            else "",
                            salary="",
                            source="LinkedIn",
                            tags=[],
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                start += 25
                if start >= 100:  # LinkedIn caps guest results at ~100
                    break
                polite_sleep(1.5, 3.0)

            polite_sleep(2.0, 4.0)

        # Enrich top jobs with description from detail API
        for job in jobs[:20]:
            _enrich_job_description(job)
            polite_sleep(0.5, 1.5)

        return jobs


def _extract_li_job_id(card) -> str:
    """Extract LinkedIn numeric job ID from a card element."""
    for attr in ["data-entity-urn", "data-occludable-job-id"]:
        val = card.get(attr, "")
        if val:
            m = re.search(r"\d+", val)
            if m:
                return m.group()
    # Try job link href
    link = card.find("a", href=True)
    if link:
        m = re.search(r"/(\d+)/?(?:\?|$)", link["href"])
        if m:
            return m.group(1)
    return ""


def _enrich_job_description(job: JobPosting) -> None:
    """Fetch job description from LinkedIn detail API and refine job type."""
    job_num = job.id.replace("linkedin-", "")
    try:
        resp = http_get(
            GUEST_JOB_DETAIL.format(job_id=job_num),
            headers={"Accept": "text/html"},
        )
        soup = parse_html(resp.text)
        desc_el = soup.find("div", {"class": re.compile(r"description|show-more-less-html", re.I)})
        if desc_el:
            job.description = desc_el.get_text(separator=" ", strip=True)[:2000]
            job.job_type = detect_job_type(job.title, job.description, fallback=job.job_type)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LinkedIn Posts scraper (requires LINKEDIN_EMAIL + LINKEDIN_PASSWORD)
# ---------------------------------------------------------------------------

class LinkedInPostsScraper(BaseJobScraper):
    """
    Searches LinkedIn posts that contain job links using the unofficial
    linkedin-api library. Requires credentials via env vars:
      LINKEDIN_EMAIL, LINKEDIN_PASSWORD
    """

    name = "LinkedIn Posts"
    JOB_HASHTAGS = ["#hiring", "#jobopenings", "#jobopportunity", "#wearehiring", "#techjobs"]

    def fetch(self) -> list[JobPosting]:
        email = os.getenv("LINKEDIN_EMAIL", "")
        password = os.getenv("LINKEDIN_PASSWORD", "")

        if not email or not password:
            logger.info("LinkedIn Posts scraper skipped – credentials not set")
            return []

        try:
            from linkedin_api import Linkedin  # type: ignore
        except ImportError:
            logger.warning("linkedin_api not installed; run: pip install linkedin-api")
            return []

        try:
            api = Linkedin(email, password)
        except Exception as exc:
            logger.error("LinkedIn login failed: %s", exc)
            return []

        jobs: list[JobPosting] = []
        seen_urls: set[str] = set()

        # Search posts for hiring hashtags
        search_terms = self.queries[:3]  # Limit API calls
        for term in search_terms:
            try:
                posts = api.search_posts(term + " hiring India", limit=20)
            except Exception as exc:
                logger.error("LinkedIn post search failed for %r: %s", term, exc)
                continue

            for post in posts:
                commentary = post.get("commentary", {})
                text = _extract_post_text(commentary)
                urls = _extract_urls_from_text(text)

                for url in urls:
                    if url in seen_urls:
                        continue
                    if _is_job_url(url):
                        seen_urls.add(url)
                        jobs.append(
                            JobPosting(
                                id=f"li-post-{abs(hash(url))}",
                                title=_guess_title_from_text(text, self.queries),
                                company=_extract_company_from_post(post),
                                location="India",
                                remote="remote" in text.lower(),
                                job_type="full-time",
                                description=text[:500],
                                apply_url=url,
                                posted_at="",
                                salary="",
                                source="LinkedIn Post",
                                tags=[],
                            )
                        )
            polite_sleep(2.0, 4.0)

        return jobs


def _extract_post_text(commentary: dict | str) -> str:
    if isinstance(commentary, str):
        return commentary
    if isinstance(commentary, dict):
        return commentary.get("text", "")
    return ""


def _extract_urls_from_text(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\)\]>\"']+", text)


def _is_job_url(url: str) -> bool:
    job_patterns = [
        "linkedin.com/jobs",
        "naukri.com",
        "foundit.in",
        "indeed.com",
        "careers.",
        "/jobs/",
        "/careers/",
        "lever.co",
        "greenhouse.io",
        "workday.com",
        "icims.com",
        "smartrecruiters.com",
        "hire.withgoogle.com",
        "myworkdayjobs.com",
        "job",
    ]
    url_lower = url.lower()
    return any(p in url_lower for p in job_patterns)


def _guess_title_from_text(text: str, queries: list[str]) -> str:
    text_lower = text.lower()
    for q in queries:
        if q.lower() in text_lower:
            return q
    # Try common patterns like "looking for a Python Developer"
    m = re.search(
        r"(?:hiring|looking for|opening for|role for|position for)\s+(?:a\s+)?([A-Z][A-Za-z\s]+(?:Engineer|Developer|Analyst|Manager|Designer|Lead|Architect))",
        text,
    )
    if m:
        return m.group(1).strip()
    return "Job Opening"


def _extract_company_from_post(post: dict) -> str:
    try:
        actor = post.get("actor", {})
        name = actor.get("name", {})
        if isinstance(name, dict):
            return name.get("text", "")
        return str(name)
    except Exception:
        return ""


def _text(el) -> str:
    """Safe text extraction from BeautifulSoup element."""
    if el is None:
        return ""
    return el.get_text(separator=" ", strip=True)
