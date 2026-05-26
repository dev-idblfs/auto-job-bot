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

# Stop-words to skip when extracting domain keywords from project descriptions
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "as", "is", "was", "are", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "this", "that", "these", "those", "i", "we", "you", "he", "she", "it",
    "they", "my", "our", "your", "his", "her", "its", "their", "using",
    "built", "developed", "created", "led", "managed", "handled", "built",
    "deployed", "integrated", "implemented", "designed", "architected",
    "migrated", "scaled", "improved", "reduced", "increased", "achieved",
    "team", "users", "data", "system", "platform", "service", "services",
    "app", "application", "applications", "project", "projects",
})

# Canonical job-type synonyms → normalised value
_JOB_TYPE_MAP: dict[str, str] = {
    "full-time": "full-time",
    "full time": "full-time",
    "permanent": "full-time",
    "contract": "contract",
    "contractor": "contract",
    "freelance": "contract",
    "c2h": "contract",
    "contract-to-hire": "contract",
    "part-time": "part-time",
    "part time": "part-time",
    "internship": "internship",
    "intern": "internship",
    "trainee": "internship",
    "remote": "remote",
    "work from home": "remote",
    "wfh": "remote",
}


def _normalise_job_type(raw: str) -> str:
    """Return canonical job-type string for a raw value."""
    raw_lower = raw.lower().strip()
    return _JOB_TYPE_MAP.get(raw_lower, raw_lower)


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

        # Preferred locations (additional cities beyond primary)
        preferred_locs: list[str] = loc.get("preferred_locations", [])
        self.preferred_locations: list[str] = preferred_locs

        # Target job preferences
        self.target_titles: list[str] = target.get("job_titles", [])
        raw_job_types: list[str] = target.get("job_types", ["full-time"])
        self.job_types: list[str] = raw_job_types
        self.preferred_job_types: set[str] = {
            _normalise_job_type(jt) for jt in raw_job_types
        }
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.salary_currency: str = target.get("salary_currency", "INR")
        self.target_industries: list[str] = target.get("industries", [])
        self.preferred_terms: list[str] = [
            t.lower() for t in target.get("preferred_terms", [])
        ]

        # Experience
        self.years_experience: int = experience.get("years_total", 0)
        self.current_title: str = experience.get("current_title", "")
        self.experience_history: list[dict] = experience.get("history", [])

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
        domain_words: set[str] = set()

        for proj in projects:
            techs = proj.get("technologies", [])
            project_techs.extend(techs)
            name = proj.get("name", "")
            if name:
                project_names.append(name)
            desc = proj.get("description", "")
            # Extract meaningful domain words from project description
            words = re.findall(r"[a-zA-Z]{4,}", desc.lower())
            for w in words:
                if w not in _STOP_WORDS and w not in {t.lower() for t in techs}:
                    domain_words.add(w)

        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}
        self.project_names: list[str] = project_names
        self.project_domain_keywords: set[str] = domain_words

        # Combined keyword pool for matching
        self.all_keywords: set[str] = self.skills_lower | self.project_tech_lower

        # Location strings for matching (city + state + country + preferred)
        base_locs = [self.city, self.state, self.country]
        all_loc_terms = base_locs + self.preferred_locations
        self.location_terms: list[str] = [t.lower() for t in all_loc_terms if t]

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
