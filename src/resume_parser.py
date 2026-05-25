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

# Common stop-words to ignore when extracting project domain terms
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "through", "during",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "it", "its", "we", "our", "us", "i", "me", "my", "this", "that", "these",
    "those", "as", "using", "used", "built", "developed", "led", "managed",
    "created", "implemented", "deployed", "handling", "handling", "based",
})


def _extract_domain_phrases(text: str) -> set[str]:
    """
    Extract meaningful 1- and 2-word phrases from a project description,
    stripping stop-words and short tokens.
    """
    words = re.findall(r"[a-z][a-z0-9\-\.]*", text.lower())
    meaningful = [w for w in words if w not in _STOP_WORDS and len(w) > 2]

    phrases: set[str] = set(meaningful)
    # Add bigrams for domain phrases like "e-commerce", "rest api", "real-time"
    for i in range(len(meaningful) - 1):
        phrases.add(f"{meaningful[i]} {meaningful[i+1]}")
    return phrases


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
        self.job_types: list[str] = [jt.lower().strip() for jt in target.get("job_types", ["full-time"])]
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.target_industries: list[str] = [i.lower() for i in target.get("industries", [])]

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
        self.primary_skills_lower: set[str] = {s.lower() for s in self.primary_skills}

        # Projects – technologies + domain phrases from descriptions
        self.projects: list[dict] = projects
        project_techs: list[str] = []
        project_domain_phrases: set[str] = set()
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
            desc = proj.get("description", "")
            project_domain_phrases |= _extract_domain_phrases(desc)
            # Also include the project name words as signals
            project_domain_phrases |= _extract_domain_phrases(proj.get("name", ""))

        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}
        self.project_domain_phrases: set[str] = project_domain_phrases

        # Combined keyword pool for matching (skills + project technologies)
        self.all_keywords: set[str] = self.skills_lower | self.project_tech_lower

        # Experience description keywords (from job history)
        experience_phrases: set[str] = set()
        for hist in self.experience_history:
            experience_phrases |= _extract_domain_phrases(hist.get("description", ""))
        self.experience_phrases: set[str] = experience_phrases

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

    def get_matching_skills(self, text: str) -> list[str]:
        """Return a sorted list of skills from this profile that appear in *text*."""
        text_lower = text.lower()
        matched = []
        for skill in sorted(self.all_skills):
            skill_lower = skill.lower()
            if len(skill_lower) <= 3:
                if re.search(rf"\b{re.escape(skill_lower)}\b", text_lower):
                    matched.append(skill)
            elif skill_lower in text_lower:
                matched.append(skill)
        return matched

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
