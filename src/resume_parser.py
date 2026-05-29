"""
Resume parser: loads resume.json and builds a structured profile
used for job matching and scoring.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Multi-word domain phrases commonly found in job descriptions
_PHRASE_PATTERNS = [
    r"real[- ]time analytics",
    r"real[- ]time data",
    r"real[- ]time processing",
    r"real[- ]time streaming",
    r"microservices? architecture",
    r"microservices? migration",
    r"distributed systems?",
    r"event[- ]driven architecture",
    r"machine learning",
    r"deep learning",
    r"natural language processing",
    r"computer vision",
    r"data pipeline",
    r"data warehouse",
    r"data lake",
    r"etl pipeline",
    r"rest(?:ful)? api",
    r"graphql api",
    r"message queue",
    r"message broker",
    r"stream processing",
    r"batch processing",
    r"cloud[- ]native",
    r"serverless architecture",
    r"continuous integration",
    r"continuous deployment",
    r"ci/cd pipeline",
    r"container orchestration",
    r"infrastructure as code",
    r"payment gateway",
    r"e[- ]commerce platform",
    r"recommendation engine",
    r"search engine",
    r"high availability",
    r"fault tolerance",
    r"load balancing",
    r"auto[- ]scaling",
]


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
        self.target_industries: list[str] = target.get("industries", [])

        # Experience
        self.years_experience: int = experience.get("years_total", 0)
        self.current_title: str = experience.get("current_title", "")

        # Skills – flatten to a single set of lowercase strings
        all_skills: list[str] = []
        for category in ["primary", "secondary", "cloud", "tools", "soft"]:
            all_skills.extend(skills_data.get(category, []))
        self.all_skills: list[str] = all_skills
        self.skills_lower: set[str] = {s.lower() for s in all_skills}
        self.primary_skills: list[str] = skills_data.get("primary", [])

        # Project keywords
        project_techs: list[str] = []
        project_names: list[str] = []
        all_project_text = ""
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
            project_names.append(proj.get("name", ""))
            all_project_text += " " + proj.get("description", "")
        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}
        self.project_names: list[str] = [n for n in project_names if n]

        # Multi-word domain phrases extracted from project descriptions
        self.project_phrases: list[str] = _extract_domain_phrases(all_project_text.lower())

        # Combined keyword pool for matching
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
        # Add primary-skill + title combos for richer search
        for title in self.target_titles[:2]:
            for skill in self.primary_skills[:2]:
                queries.append(f"{title} {skill}")
        return queries

    @property
    def all_project_keywords(self) -> set[str]:
        """Combined set of project technologies + extracted domain phrases."""
        phrases: set[str] = set(p.lower() for p in self.project_phrases)
        return self.project_tech_lower | phrases

    def __repr__(self) -> str:
        return (
            f"<ResumeProfile name={self.name!r} "
            f"level={self.experience_level} "
            f"location={self.location_display!r}>"
        )


def _extract_domain_phrases(text: str) -> list[str]:
    """
    Extract multi-word domain phrases that appear in the given text.
    Returns normalised lowercase phrase strings.
    """
    found: list[str] = []
    for pattern in _PHRASE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.append(match.group(0).lower())
    return found


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
    logger.info("Loaded resume for: %s (%s)", profile.name, profile.location_display)
    return profile
