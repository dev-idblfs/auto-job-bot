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

# Multi-word domain phrases to look for in project descriptions.
# Order matters – longer phrases first to avoid false sub-matches.
_DOMAIN_PHRASE_PATTERNS: list[str] = [
    r"real[\-\s]time analytics",
    r"real[\-\s]time dashboard",
    r"real[\-\s]time (data|stream|processing)",
    r"machine learning",
    r"deep learning",
    r"natural language processing",
    r"computer vision",
    r"data pipeline",
    r"data engineering",
    r"data warehouse",
    r"data lake",
    r"streaming (data|pipeline|architecture)",
    r"event[\-\s]driven architecture",
    r"microservices? architecture",
    r"micro[\-\s]service",
    r"service[\-\s]oriented architecture",
    r"distributed systems?",
    r"cloud[\-\s]native",
    r"serverless",
    r"e[\-\s]commerce platform",
    r"payment (gateway|integration|processing)",
    r"recommendation (engine|system)",
    r"search (engine|platform|infrastructure)",
    r"api gateway",
    r"graphql (api|backend|service)",
    r"rest(ful)? api",
    r"websocket",
    r"message (queue|broker|bus)",
    r"ci[/\-\s]cd (pipeline|workflow)",
    r"infrastructure as code",
    r"devops (pipeline|workflow|automation)",
    r"container orchestration",
    r"load balancing",
    r"high availability",
    r"fault tolerance",
    r"auto[\-\s]scaling",
    r"multi[\-\s]tenant",
    r"saas platform",
    r"full[\-\s]stack (web|application|development)",
    r"mobile (app|application|backend)",
    r"ios (app|development)",
    r"android (app|development)",
    r"cross[\-\s]platform",
]

_COMPILED_PHRASES = [re.compile(p, re.IGNORECASE) for p in _DOMAIN_PHRASE_PATTERNS]


def _extract_domain_phrases(text: str) -> list[str]:
    """Return unique multi-word domain phrases found in `text`."""
    found: list[str] = []
    for rx in _COMPILED_PHRASES:
        m = rx.search(text)
        if m:
            found.append(m.group(0).lower())
    return list(dict.fromkeys(found))  # preserve order, deduplicate


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
        self.job_types: list[str] = [jt.lower() for jt in target.get("job_types", ["full-time"])]
        self.experience_level: str = target.get("experience_level", "mid")
        self.min_salary: int = target.get("min_salary", 0)
        self.target_industries: list[str] = [i.lower() for i in target.get("industries", [])]

        # Experience
        self.years_experience: int = experience.get("years_total", 0)
        self.current_title: str = experience.get("current_title", "")

        # Build experience description corpus for phrase extraction
        exp_corpus = " ".join(
            h.get("description", "") for h in experience.get("history", [])
        )

        # Skills – flatten to a single set of lowercase strings
        all_skills: list[str] = []
        for category in ["primary", "secondary", "cloud", "tools", "soft"]:
            all_skills.extend(skills_data.get(category, []))
        self.all_skills: list[str] = all_skills
        self.skills_lower: set[str] = {s.lower() for s in all_skills}
        self.primary_skills: list[str] = skills_data.get("primary", [])

        # Projects – technologies + domain phrases from descriptions
        project_techs: list[str] = []
        project_desc_corpus = ""
        self.projects: list[dict] = projects
        for proj in projects:
            project_techs.extend(proj.get("technologies", []))
            project_desc_corpus += " " + proj.get("name", "") + " " + proj.get("description", "")

        self.project_technologies: list[str] = list(dict.fromkeys(project_techs))
        self.project_tech_lower: set[str] = {t.lower() for t in self.project_technologies}

        # Domain phrases extracted from both project descriptions and experience history
        full_corpus = project_desc_corpus + " " + exp_corpus
        self.project_domain_phrases: list[str] = _extract_domain_phrases(full_corpus)

        # Combined keyword pool for skill matching
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
            f"location={self.location_display!r} "
            f"domain_phrases={self.project_domain_phrases}>"
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
        "Loaded resume for: %s (%s) | job_types=%s | domain_phrases=%s",
        profile.name,
        profile.location_display,
        profile.job_types,
        profile.project_domain_phrases,
    )
    return profile
