"""
Resume parser: loads resume.json and builds a structured profile
used for job matching and scoring.
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
        self.phone: str = personal.get("phone", "")
        loc = personal.get("location", {})
        self.city: str = loc.get("city", "")
        self.state: str = loc.get("state", "")
        self.country: str = loc.get("country", "")
        self.remote_ok: bool = loc.get("remote_ok", True)
        self.willing_to_relocate: bool = loc.get("willing_to_relocate", False)

        # Target job preferences
        self.target_titles: list[str] = target.get("job_titles", [])
        self.job_types: list[str] = target.get("job_types", ["full-time"])
        self.job_types_normalized: set[str] = {
            jt.lower().replace("-", " ").replace("_", " ")
            for jt in self.job_types
        }
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.salary_currency: str = target.get("salary_currency", "INR")
        self.target_industries: list[str] = target.get("industries", [])

        # Experience
        self.years_experience: int = experience.get("years_total", 0)
        self.current_title: str = experience.get("current_title", "")
        self.current_company: str = experience.get("current_company", "")

        # Skills – flatten to a single set of lowercase strings
        all_skills: list[str] = []
        for category in ["primary", "secondary", "cloud", "tools", "soft"]:
            all_skills.extend(skills_data.get(category, []))
        self.all_skills: list[str] = all_skills
        self.skills_lower: set[str] = {s.lower() for s in all_skills}
        self.primary_skills: list[str] = skills_data.get("primary", [])

        # Projects – raw data kept for email display (matched project names)
        self.projects_raw: list[dict] = projects
        project_techs: list[str] = []
        project_names: list[str] = []
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
            name = proj.get("name", "")
            if name:
                project_names.append(name)
        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}
        self.project_names: list[str] = project_names

        # Combined keyword pool for matching (skills + project techs)
        self.all_keywords: set[str] = self.skills_lower | self.project_tech_lower

        # Location strings for matching
        self.location_terms: list[str] = [
            t.lower()
            for t in [self.city, self.state, self.country]
            if t
        ]

    @property
    def location_display(self) -> str:
        parts = [p for p in [self.city, self.state, self.country] if p]
        return ", ".join(parts)

    def get_search_queries(self) -> list[str]:
        """Return a list of search query strings derived from the profile."""
        queries = list(self.target_titles)
        # Primary-skill + title combos for richer search
        for title in self.target_titles[:3]:
            for skill in self.primary_skills[:3]:
                queries.append(f"{title} {skill}")
        return queries

    def __repr__(self) -> str:
        return (
            f"<ResumeProfile name={self.name!r} "
            f"level={self.experience_level} "
            f"location={self.location_display!r} "
            f"job_types={self.job_types}>"
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
        "Loaded resume for: %s (%s) | %d yrs exp | looking for: %s",
        profile.name,
        profile.location_display,
        profile.years_experience,
        ", ".join(profile.job_types),
    )
    return profile
