"""
Resume parser: loads resume.json and builds a structured profile
used for job matching, scoring, and search query generation.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ResumeProfile:
    """Structured profile extracted from resume.json."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._build_profile()

    def _build_profile(self) -> None:
        personal = self._data.get("personal", {})
        target = self._data.get("target", {})
        experience = self._data.get("experience", {})
        skills_data = self._data.get("skills", {})
        projects = self._data.get("projects", [])

        # Personal / location
        self.name: str = personal.get("name", "")
        self.email: str = personal.get("email", "")
        loc = personal.get("location", {})
        self.city: str = loc.get("city", "")
        self.state: str = loc.get("state", "")
        self.country: str = loc.get("country", "")
        self.remote_ok: bool = loc.get("remote_ok", True)
        self.willing_to_relocate: bool = loc.get("willing_to_relocate", False)

        # Target job preferences
        self.target_titles: list[str] = target.get("job_titles", [])
        self.job_types: list[str] = target.get("job_types", ["full-time"])
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.salary_currency: str = target.get("salary_currency", "USD")
        self.target_industries: list[str] = target.get("industries", [])

        # Experience
        self.years_experience: int = experience.get("years_total", 0)
        self.current_title: str = experience.get("current_title", "")
        self.experience_history: list[dict] = experience.get("history", [])

        # Skills – flatten to a single ordered list and a lowercase set
        all_skills: list[str] = []
        for category in ["primary", "secondary", "cloud", "tools", "soft"]:
            all_skills.extend(skills_data.get(category, []))
        self.all_skills: list[str] = all_skills
        self.skills_lower: set[str] = {s.lower() for s in all_skills}
        self.primary_skills: list[str] = skills_data.get("primary", [])

        # Project data
        self.projects: list[dict] = projects
        project_techs: list[str] = []
        project_descriptions: list[str] = []
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
            desc = proj.get("description", "")
            if desc:
                project_descriptions.append(desc)
        self.project_technologies: list[str] = list(dict.fromkeys(project_techs))  # deduped, ordered
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}
        self.project_descriptions: list[str] = project_descriptions

        # Combined keyword pool for matching (skills + project tech)
        self.all_keywords: set[str] = self.skills_lower | self.project_tech_lower

        # Education and certifications
        edu = self._data.get("education", {})
        self.degree: str = edu.get("degree", "")
        self.institution: str = edu.get("institution", "")
        self.graduation_year: int = edu.get("graduation_year", 0)
        self.certifications: list[str] = self._data.get("certifications", [])

        # Location strings for matching (lowercase)
        self.location_terms: list[str] = [
            t.lower()
            for t in [self.city, self.state, self.country]
            if t
        ]

    @property
    def location_display(self) -> str:
        parts = [p for p in [self.city, self.state, self.country] if p]
        return ", ".join(parts)

    def get_search_queries(self, config: dict | None = None) -> list[str]:
        """
        Build a rich set of search query strings derived from the profile.

        Produces queries at multiple specificity levels:
        1. Bare job titles (broadest)
        2. Title + primary skill combinations
        3. Title + location (if not remote-only)
        4. Title + experience level keyword
        """
        config = config or {}
        filter_cfg = config.get("filters", {})
        title_overrides: list[str] = filter_cfg.get("job_titles", [])
        titles = title_overrides if title_overrides else self.target_titles

        queries: list[str] = list(titles)  # bare titles first

        # Title + top primary skills (improves relevance on skill-aware job boards)
        for title in titles[:2]:
            for skill in self.primary_skills[:3]:
                queries.append(f"{title} {skill}")

        # Title + location (helps with location-specific boards)
        if self.city and not filter_cfg.get("remote_only", False):
            for title in titles[:2]:
                queries.append(f"{title} {self.city}")

        # Title + experience keyword (helps on boards that index exp labels)
        exp_label = {
            "junior": "junior",
            "mid": "mid-level",
            "senior": "senior",
        }.get(self.experience_level, "")
        if exp_label:
            for title in titles[:2]:
                queries.append(f"{exp_label} {title}")

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                unique.append(q)

        return unique

    def __repr__(self) -> str:
        return (
            f"<ResumeProfile name={self.name!r} "
            f"level={self.experience_level} "
            f"years={self.years_experience} "
            f"location={self.location_display!r}>"
        )


def load_resume(path: str | Path = "resume.json") -> ResumeProfile:
    """Load and parse resume.json into a ResumeProfile."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"resume.json not found at {path.resolve()}. "
            "Please edit resume.json with your details."
        )
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    profile = ResumeProfile(data)
    logger.info(
        "Loaded resume for: %s | %s | %d yrs | %s",
        profile.name,
        profile.experience_level,
        profile.years_experience,
        profile.location_display,
    )
    return profile
