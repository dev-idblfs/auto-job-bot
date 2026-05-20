"""
auto-job-bot: Daily India Job Alert System

Entry point. Run directly or trigger via cron / GitHub Actions.

Usage:
    python main.py                  # Run full pipeline
    python main.py --dry-run        # Fetch & filter, print results, no email
    python main.py --send-test      # Send email with current results (ignore dedup)
    python main.py --list-sources   # Show configured sources
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.resume_parser import load_resume
from src.job_searcher import fetch_all_jobs
from src.job_filter import filter_and_rank_jobs
from src.email_sender import send_email, build_html_email, build_plain_text

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s – %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        logger.error("config.yaml not found at %s", config_path.resolve())
        sys.exit(1)
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(config: dict, dry_run: bool = False, skip_dedup: bool = False) -> int:
    """
    Full pipeline: load resume → fetch jobs → filter & rank → email.
    Returns the number of jobs sent (or would be sent in dry-run mode).
    """
    # 1. Load resume profile
    profile = load_resume("resume.json")
    logger.info("Profile: %s | %s | %s | skills: %s",
                profile.name,
                profile.experience_level,
                profile.location_display,
                ", ".join(profile.primary_skills[:5]))

    # 2. Temporarily disable deduplication if skip_dedup requested
    if skip_dedup:
        config.setdefault("deduplication", {})["enabled"] = False

    # 3. Fetch jobs from all sources
    logger.info("Fetching jobs from all enabled sources…")
    all_jobs = fetch_all_jobs(profile, config)
    logger.info("Fetched %d unique jobs total", len(all_jobs))

    # 4. Filter and rank
    logger.info("Filtering and ranking…")
    ranked_jobs = filter_and_rank_jobs(all_jobs, profile, config)
    logger.info("After filtering: %d jobs to report", len(ranked_jobs))

    if not ranked_jobs:
        logger.info("No relevant jobs found today – no email will be sent")
        return 0

    # 5. Print summary
    _print_summary(ranked_jobs)

    # 6. Send email (unless dry-run)
    if dry_run:
        logger.info("[DRY RUN] Would send email with %d jobs – skipping", len(ranked_jobs))
        if os.getenv("DRY_RUN_SAVE_HTML"):
            _save_html_preview(ranked_jobs, profile, config)
    else:
        success = send_email(ranked_jobs, profile, config)
        if not success:
            logger.error("Email delivery failed")
            return -1

    return len(ranked_jobs)


def _print_summary(jobs) -> None:
    print(f"\n{'='*70}")
    print(f"  TOP JOBS ({len(jobs)} results)  –  {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}")
    print(f"{'='*70}")
    for i, job in enumerate(jobs[:20], 1):
        remote_flag = " [REMOTE]" if job.remote else ""
        print(
            f"  {i:>2}. [{job.relevance_score:>3}%] {job.title[:45]:<45} "
            f"@ {job.company[:25]:<25} | {job.source}{remote_flag}"
        )
        print(f"      📍 {job.location}  {'💰 ' + job.salary if job.salary else ''}")
        print(f"      🔗 {job.apply_url}")
    if len(jobs) > 20:
        print(f"\n  … and {len(jobs) - 20} more jobs in the email")
    print(f"{'='*70}\n")


def _save_html_preview(jobs, profile, config) -> None:
    html = build_html_email(jobs, profile, config)
    out = Path("job_digest_preview.html")
    out.write_text(html, encoding="utf-8")
    logger.info("HTML preview saved to %s", out.resolve())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="auto-job-bot: Daily India Job Alert System"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and filter jobs but do not send email",
    )
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="Send email ignoring deduplication (re-sends already-seen jobs)",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List all configured job sources and exit",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    config = load_config(args.config)

    if args.list_sources:
        sources = config.get("search", {}).get("sources", {})
        print("\nConfigured job sources:")
        source_list = [
            ("linkedin_jobs",    "LinkedIn Jobs       (public guest API, no auth)"),
            ("linkedin_posts",   "LinkedIn Posts      (requires LINKEDIN_EMAIL + LINKEDIN_PASSWORD)"),
            ("naukri",           "Naukri.com          (internal API)"),
            ("indeed",           "Indeed India        (HTML scraping)"),
            ("foundit",          "Foundit.in          (Monster India, internal API)"),
            ("hirist",           "Hirist.tech         (tech jobs API)"),
            ("cutshort",         "Cutshort.io         (GraphQL API)"),
            ("internshala",      "Internshala         (entry-level, HTML scraping)"),
            ("company_careers",  "Company Career Pages (curated list, HTML scraping)"),
        ]
        for key, desc in source_list:
            status = "✅ enabled" if sources.get(key, key != "linkedin_posts") else "❌ disabled"
            print(f"  {status}  {desc}")
        print()
        return

    result = run_pipeline(
        config,
        dry_run=args.dry_run,
        skip_dedup=args.send_test,
    )
    sys.exit(0 if result >= 0 else 1)


if __name__ == "__main__":
    main()
