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

# Multi-word technical phrases to extract from project descriptions
_DOMAIN_PHRASE_PATTERNS = [
    re.compile(r"\b(real[- ]time\s+\w+(?:\s+\w+)?)\b", re.I),
    re.compile(r"\b(microservices?\s+\w+(?:\s+\w+)?)\b", re.I),
    re.compile(r"\b(machine learning[^,\.]{0,40})\b", re.I),
    re.compile(r"\b(deep learning[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(natural language[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(computer vision[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(data pipeline[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(event[- ]driven[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(distributed system[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(cloud[- ]native[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(serverless[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(e[- ]commerce\s+\w+(?:\s+\w+)?)\b", re.I),
    re.compile(r"\b(data analytics[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(ci[/\s]?cd pipeline[^,\.]{0,30})\b", re.I),
    re.compile(r"\b(rest(?:ful)?\s+api[^,\.]{0,20})\b", re.I),
    re.compile(r"\b(graphql\s+\w+(?:\s+\w+)?)\b", re.I),
    re.compile(r"\b(websocket[^,\.]{0,20})\b", re.I),
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
        project_phrases: list[str] = []
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
            desc = proj.get("description", "")
            # Extract multi-word domain phrases
            for pattern in _DOMAIN_PHRASE_PATTERNS:
                for match in pattern.findall(desc):
                    phrase = match.strip().lower()
                    if 4 < len(phrase) < 60:
                        project_phrases.append(phrase)
        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}
        self.project_phrases: list[str] = list(set(project_phrases))

        # Store raw project data for scoring
        self._projects = projects

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

    def __repr__(self) -> str:
        return (
            f"<ResumeProfile name={self.name!r} "
            f"level={self.experience_level} "
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
    logger.info("Loaded resume for: %s (%s)", profile.name, profile.location_display)
    return profile
