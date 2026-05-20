"""India job scrapers package."""

from .linkedin import LinkedInJobsScraper, LinkedInPostsScraper
from .naukri import NaukriScraper
from .indeed import IndeedIndiaScraper
from .foundit import FounditScraper
from .hirist import HiristScraper
from .cutshort import CutshortScraper
from .internshala import IntershalaJobsScraper
from .company_careers import CompanyCareersScraper

__all__ = [
    "LinkedInJobsScraper",
    "LinkedInPostsScraper",
    "NaukriScraper",
    "IndeedIndiaScraper",
    "FounditScraper",
    "HiristScraper",
    "CutshortScraper",
    "IntershalaJobsScraper",
    "CompanyCareersScraper",
]
