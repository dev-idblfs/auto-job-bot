"""
Base scraper class and shared HTTP utilities for all India job scrapers.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..job_searcher import JobPosting

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# Rotate user-agents to reduce bot detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


def get_session(extra_headers: dict | None = None) -> requests.Session:
    """Return a requests.Session with browser-like headers."""
    session = requests.Session()
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra_headers:
        headers.update(extra_headers)
    session.headers.update(headers)
    return session


def http_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    session: requests.Session | None = None,
    as_json: bool = False,
) -> Any:
    """GET with retry/back-off. Returns JSON or response object."""
    s = session or get_session(headers)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = s.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json() if as_json else resp
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt + random.uniform(0, 1)
            logger.warning("GET %s failed (%s), retry %d in %.1fs", url, exc, attempt, wait)
            time.sleep(wait)


def polite_sleep(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """Sleep a random amount to be polite to servers."""
    time.sleep(random.uniform(min_s, max_s))


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class BaseJobScraper(ABC):
    """Abstract base for all job scrapers."""

    name: str = "base"

    def __init__(self, queries: list[str], profile: Any, config: dict) -> None:
        self.queries = queries
        self.profile = profile
        self.config = config
        self.max_results: int = config.get("search", {}).get("max_results_per_source", 50)
        self.session = get_session()

    @abstractmethod
    def fetch(self) -> list[JobPosting]:
        """Fetch and return job postings."""
        ...

    def safe_fetch(self) -> list[JobPosting]:
        """Wrapper with error handling around fetch()."""
        try:
            jobs = self.fetch()
            logger.info("%s: fetched %d jobs", self.name, len(jobs))
            return jobs
        except Exception as exc:
            logger.error("%s: scraper failed – %s", self.name, exc)
            return []
