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

# Common English words and generic tech terms to exclude from project domain keywords
_PROJECT_STOP_WORDS: frozenset[str] = frozenset({
    # Articles, prepositions, conjunctions
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "as", "into", "out", "over", "its",
    "was", "is", "are", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    # Common action verbs (not useful for job matching)
    "using", "built", "developed", "created", "made", "led", "handled",
    "managed", "worked", "designed", "implemented", "deployed", "migrated",
    "built", "wrote", "wrote", "helped", "supported",
    # Pronouns/determiners
    "it", "this", "that", "these", "those", "our", "their", "we", "i", "my",
    # Very generic tech terms (already covered by skills)
    "api", "data", "user", "users", "app", "web", "code", "test", "full",
    "stack", "daily", "new", "large", "high", "scale", "based", "driven",
})


def _extract_project_domain_words(projects: list[dict]) -> set[str]:
    """
    Extract meaningful domain-specific keywords from project descriptions.

    Returns single words and hyphenated terms (e.g. 'e-commerce', 'real-time')
    that describe the business domain or architectural patterns.
    """
    domain_words: set[str] = set()
    for proj in projects:
        name = proj.get("name", "").lower()
        desc = proj.get("description", "").lower()
        combined = f"{name} {desc}"

        # Extract hyphenated compound terms first (e.g. 'e-commerce', 'real-time')
        for match in re.findall(r"[a-z]+-[a-z]+(?:-[a-z]+)*", combined):
            if len(match) >= 5:  # Skip very short hyphenated terms
                domain_words.add(match)

        # Extract individual words, filtering stop words and short words
        for word in re.findall(r"[a-z][a-z0-9]*", combined):
            if len(word) >= 4 and word not in _PROJECT_STOP_WORDS:
                domain_words.add(word)

    return domain_words


def _normalize_job_type(raw: str) -> str:
    """Normalise various job-type strings to a canonical form."""
    t = raw.lower().replace("_", " ").replace("-", " ").strip()
    if t in ("fulltime", "full time", "permanent", "regular", "perm"):
        return "full-time"
    if t in ("parttime", "part time"):
        return "part-time"
    if t in ("contractor", "contract to hire", "c2h", "freelance", "temporary"):
        return "contract"
    if t in ("intern", "internship"):
        return "internship"
    # Return normalised (with dashes instead of spaces) to keep it tidy
    return t.replace(" ", "-")


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
        # Normalise job types so matching is consistent
        raw_job_types: list[str] = target.get("job_types", ["full-time"])
        self.job_types: list[str] = [_normalize_job_type(jt) for jt in raw_job_types]
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.salary_currency: str = target.get("salary_currency", "")
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

        # Project keywords – technologies + domain words from descriptions
        project_techs: list[str] = []
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}

        # Domain-specific keywords extracted from project names and descriptions
        self.project_domain_keywords: set[str] = _extract_project_domain_words(projects)

        # Combined keyword pool for matching (skills + project technologies)
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
