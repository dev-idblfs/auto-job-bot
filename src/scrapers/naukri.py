"""
Naukri.com scraper – uses Naukri's internal search API.

Naukri returns HTTP 406 with "recaptcha required" unless a valid `nkparam`
header is sent. The token is RSA-encrypted metadata (timestamp + page context).
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..job_searcher import JobPosting
from .base import BaseJobScraper, polite_sleep

logger = logging.getLogger(__name__)

NAUKRI_API = "https://www.naukri.com/jobapi/v3/search"
NAUKRI_HOME = "https://www.naukri.com/"

# Naukri's RSA public key (from their frontend bundle; used to build nkparam)
NAUKRI_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MIm
K6LgzJhmLhN7B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3nbOvt80CAwEAAQ==
-----END PUBLIC KEY-----"""

_RSA_CIPHER = None


def _generate_nkparam(page_type: str = "srp") -> str:
    """Build a fresh nkparam token (required on every API call)."""
    global _RSA_CIPHER
    if _RSA_CIPHER is None:
        key = serialization.load_pem_public_key(NAUKRI_PUBLIC_KEY_PEM)
        _RSA_CIPHER = key

    timestamp = int(time.time() * 1000)
    plaintext = f"v0|{timestamp}|121_{page_type}"
    encrypted = _RSA_CIPHER.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("utf-8")


def _naukri_headers(seo_slug: str) -> dict[str, str]:
    nk = _generate_nkparam()
    return {
        "appid": "109",
        "systemid": "Naukri",
        "nkparam": nk,
        "Nkparam": nk,
        "Accept": "application/json",
        "Accept-Language": "en-IN,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": f"{NAUKRI_HOME}{seo_slug}",
        "Origin": NAUKRI_HOME.rstrip("/"),
    }


def _seo_slug(query: str, location: str) -> str:
    """Build SEO path used for Referer (e.g. brand-manager-jobs-in-mumbai)."""
    q = query.lower().strip().replace(" ", "-")
    loc = location.lower().strip().replace(" ", "-") if location else "india"
    return f"{q}-jobs-in-{loc}" if loc else f"{q}-jobs"


class NaukriScraper(BaseJobScraper):
    """Fetches jobs from Naukri.com via their internal search API."""

    name = "Naukri"

    def __init__(self, queries: list[str], profile: Any, config: dict) -> None:
        super().__init__(queries, profile, config)
        self._api_session = requests.Session()
        self._api_session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-IN,en;q=0.9",
            }
        )
        try:
            self._api_session.get(NAUKRI_HOME, timeout=15)
        except Exception as exc:
            logger.debug("Naukri homepage warm-up failed: %s", exc)

    def fetch(self) -> list[JobPosting]:
        seen: set[str] = set()
        jobs: list[JobPosting] = []
        location = _build_location(self.profile)
        api_blocked = False

        for query in self.queries:
            if len(jobs) >= self.max_results or api_blocked:
                break

            seo_slug = _seo_slug(query, location)
            page_no = 1

            while len(jobs) < self.max_results:
                params = {
                    "noOfResults": min(20, self.max_results - len(jobs)),
                    "urlType": "search_by_keyword",
                    "searchType": "adv",
                    "keyword": query,
                    "location": location,
                    "pageNo": page_no,
                    "jobAge": 1,
                    "sort": "r",
                    "k": query,
                    "seoKey": seo_slug,
                    "src": "jobsearchDesk",
                    "latLong": "",
                }

                try:
                    data = _naukri_search(self._api_session, params, seo_slug)
                except NaukriApiBlocked as exc:
                    logger.error(
                        "Naukri API blocked for %r: %s – skipping remaining Naukri queries",
                        query,
                        exc,
                    )
                    api_blocked = True
                    break
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

                    salary = _extract_salary(item)
                    tags = [
                        s.lower()
                        for s in item.get("tagsAndSkills", "").split(",")
                        if s.strip()
                    ]

                    jobs.append(
                        JobPosting(
                            id=f"naukri-{job_id}",
                            title=item.get("title", ""),
                            company=item.get("companyName", ""),
                            location=_format_location(item),
                            remote=_is_remote(item),
                            job_type=_map_job_type(item),
                            description=item.get("jobDescription", ""),
                            apply_url=item.get(
                                "jdURL", f"https://www.naukri.com/job-listings-{job_id}"
                            ),
                            posted_at=_parse_posted_date(item),
                            salary=salary,
                            source="Naukri",
                            tags=tags,
                        )
                    )

                    if len(jobs) >= self.max_results:
                        break

                total = data.get("noOfJobs", 0)
                if page_no * 20 >= min(total, 100):
                    break
                page_no += 1
                polite_sleep(1.5, 3.0)

            polite_sleep(2.0, 4.0)

        return jobs


class NaukriApiBlocked(Exception):
    """Raised when Naukri requires captcha or rejects the request."""


def _naukri_search(
    session: requests.Session,
    params: dict[str, Any],
    seo_slug: str,
) -> dict[str, Any]:
    """GET jobapi/v3/search with a fresh nkparam; retry once on 406."""
    last_error = ""
    for attempt in range(2):
        headers = _naukri_headers(seo_slug)
        resp = session.get(NAUKRI_API, params=params, headers=headers, timeout=20)

        if resp.status_code == 200:
            return resp.json()

        last_error = resp.text[:200]
        try:
            body = resp.json()
            message = body.get("message", "")
        except Exception:
            message = resp.text[:120]

        if resp.status_code == 406 and "recaptcha" in message.lower():
            if attempt == 0:
                polite_sleep(1.0, 2.0)
                continue
            raise NaukriApiBlocked(message)

        resp.raise_for_status()

    raise NaukriApiBlocked(last_error or "Naukri search failed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_location(profile) -> str:
    """Build location string for Naukri search."""
    if hasattr(profile, "city") and profile.city:
        return profile.city
    return "India"


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
        if isinstance(ph, str):
            label = ph
        elif isinstance(ph, dict):
            label = ph.get("label", "")
        else:
            continue
        if "ago" in label.lower() or "day" in label.lower():
            return label
    return ""
