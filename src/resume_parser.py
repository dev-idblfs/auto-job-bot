"""
Resume parser: loads resume.json and builds a structured profile
used for job matching and scoring.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maps generic industry names → text signals found in job postings
INDUSTRY_KEYWORD_MAP: dict[str, list[str]] = {
    "technology": ["software", "tech", "saas", "platform", "api", "cloud"],
    "fintech": ["fintech", "finance", "financial", "payment", "banking", "insurtech", "lending"],
    "saas": ["saas", "software as a service", "cloud", "b2b", "enterprise software"],
    "startup": ["startup", "early-stage", "seed stage", "series a", "venture-backed"],
    "ecommerce": ["ecommerce", "e-commerce", "retail tech", "marketplace", "d2c"],
    "healthcare": ["healthtech", "health tech", "medtech", "medical", "hospital", "clinical"],
    "edtech": ["edtech", "ed-tech", "education tech", "learning platform", "lms"],
    "logistics": ["logistics", "supply chain", "freight", "delivery tech", "warehouse"],
    "gaming": ["gaming", "game development", "game studio", "mobile game"],
    "media": ["media", "streaming", "content platform", "digital media"],
}

# Job type signals for detecting type in job descriptions
JOB_TYPE_SIGNALS: dict[str, list[str]] = {
    "full-time": ["full-time", "full time", "permanent", "regular", "direct hire", "salaried"],
    "contract": ["contract", "freelance", "consultant", "c2c", "corp-to-corp", "fixed-term", "temporary"],
    "part-time": ["part-time", "part time", "flexible hours", "20 hours"],
    "internship": ["intern", "internship", "trainee", "apprentice"],
    "remote": ["remote", "work from home", "wfh", "work-from-home", "fully remote", "distributed"],
}


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
        self.job_types_lower: set[str] = {jt.lower() for jt in self.job_types}
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.target_industries: list[str] = target.get("industries", [])

        # Industry keywords derived from target industries
        self.industry_keywords: list[str] = self._build_industry_keywords()

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
        self.primary_skills_lower: set[str] = {s.lower() for s in self.primary_skills}

        # Project keywords
        project_techs: list[str] = []
        self.projects: list[dict] = projects
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
        self.project_technologies: list[str] = list(set(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}

        # Combined keyword pool for matching
        self.all_keywords: set[str] = self.skills_lower | self.project_tech_lower

        # Location strings for matching
        self.location_terms: list[str] = [
            t.lower()
            for t in [self.city, self.state, self.country]
            if t
        ]

    def _build_industry_keywords(self) -> list[str]:
        """Derive searchable keyword list from target industry names."""
        keywords: list[str] = []
        for industry in self.target_industries:
            mapped = INDUSTRY_KEYWORD_MAP.get(industry.lower(), [industry.lower()])
            keywords.extend(mapped)
        return list(set(keywords))

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
