# auto-job-bot 🤖

**Profile-Based Daily Job Alert System** – reads your `resume.json`, scrapes jobs from LinkedIn, Naukri, Indeed India, Foundit, Hirist, Cutshort, company career pages and more; ranks every posting against your location, experience level, skills, and project technologies; then emails a curated digest every day with direct **Apply Now** links.

---

## How It Works

```
resume.json  +  config.yaml
       │
       ▼
 Job Scrapers (9 sources)
       │
       ▼
 Profile-Based Filter & Score (0–100)
   ├── Title match      (25 pts)
   ├── Skills match     (35 pts)
   ├── Project tech     (15 pts)   ← NEW
   ├── Location fit     (15 pts)
   └── Experience level (10 pts)
       │
       ▼
 HTML Email Digest  →  your inbox
   ├── Job card per posting
   ├── ✅ Matched skills highlight
   ├── 🛠 Project tech highlight
   └── Apply Now → direct link
```

1. **Reads your resume** from `resume.json` – extracts skills, project technologies, experience level, years, location, and target job titles/types.
2. **Scrapes 9 job sources** (LinkedIn, Naukri, Indeed India, Foundit, Hirist, Cutshort, Internshala, LinkedIn Posts, Company Career Pages).
3. **Profile-based filtering** – hard-filters by location, job type, remote preference, salary, and excluded keywords before scoring.
4. **Scores each job 0–100** across five dimensions (title, skills, project tech, location, experience).
5. **Deduplicates** – never re-sends a posting you've already seen.
6. **Emails a rich HTML digest** with per-card skill/project match highlights and a direct **Apply Now** link for every job.

Runs daily via GitHub Actions cron (`30 3 * * *` UTC = ~9 AM IST).

---

## Quick Start

### 1 · Clone and install

```bash
git clone <your-repo>
cd auto-job-bot
pip install -r requirements.txt
```

### 2 · Fill in your resume

Open `resume.json` and replace the placeholder data with your own:

```jsonc
{
  "personal": {
    "name": "Priya Sharma",
    "email": "priya@example.com",
    "location": {
      "city": "Bengaluru",
      "country": "India",
      "remote_ok": true,
      "willing_to_relocate": false
    }
  },
  "target": {
    "job_titles": ["Backend Engineer", "Python Developer", "SDE-2"],
    "job_types":  ["full-time", "contract"],   // filters applied at search time
    "experience_level": "mid",                 // junior | mid | senior
    "min_salary": 1500000,                     // annual, in resume currency
    "salary_currency": "INR"
  },
  "experience": {
    "years_total": 4,
    "current_title": "Software Engineer"
  },
  "skills": {
    "primary":   ["Python", "FastAPI", "Django", "PostgreSQL"],
    "secondary": ["React", "TypeScript", "Node.js"],
    "cloud":     ["AWS", "Docker", "Kubernetes"],
    "tools":     ["Git", "Redis", "RabbitMQ"]
  },
  "projects": [
    {
      "name": "E-Commerce Platform",
      "description": "Full-stack platform with Django + React, 10k daily users",
      "technologies": ["Python", "Django", "React", "PostgreSQL", "Redis", "AWS"]
    }
    // Add your real projects – their technologies boost relevance scoring
  ]
}
```

### 3 · Configure email and secrets

Copy `.env.example` to `.env` and fill in your email credentials:

```bash
cp .env.example .env
# Edit .env
```

| Variable | Required | Description |
|---|---|---|
| `EMAIL_SENDER` | ✅ | Your Gmail (or SMTP sender) address |
| `EMAIL_PASSWORD` | ✅ | [Gmail App Password](https://myaccount.google.com/apppasswords) |
| `EMAIL_RECIPIENT` | ✅ | Recipient(s), comma-separated |
| `LINKEDIN_EMAIL` | Optional | LinkedIn email (for post scraping) |
| `LINKEDIN_PASSWORD` | Optional | LinkedIn password (for post scraping) |

> **Gmail users:** Enable 2FA and generate an [App Password](https://myaccount.google.com/apppasswords). Do **not** use your main Google password.

### 4 · Test it

```bash
# Dry run – prints matches, no email sent
python main.py --dry-run --verbose

# Send a test email (ignores deduplication)
python main.py --send-test

# Full run
python main.py
```

---

## Profile-Based Filtering

### Location Filtering

The system matches jobs in three ways:
- **Exact city/country match** – uses `personal.location.city` and `.country` from resume.json
- **Remote match** – if `remote_ok: true`, remote/WFH jobs score full location points
- **Willing to relocate** – partial credit even for non-local jobs
- **Override** – set `filters.locations` in `config.yaml` to force specific cities (e.g. `["Bengaluru", "Pune", "Remote"]`)

### Experience Filtering

Matches are scored by:
- Experience-level keywords (junior/mid/senior) found in job text
- Numeric years range (e.g. "3-5 years") matched against `experience.years_total`
- Configured via `filters.experience_levels` in config.yaml

### Skills & Project Matching

Every job is scored against **two separate keyword pools**:

| Pool | Weight | Source |
|---|---|---|
| Resume skills | 35 pts | `skills.primary` + `.secondary` + `.cloud` + `.tools` |
| Project technologies | 15 pts | `projects[].technologies` across all your projects |

The email digest shows which skills and project technologies matched each posting.

### Job Type Filtering

Set `target.job_types` in `resume.json` (e.g. `["full-time", "contract"]`). Only jobs whose detected type matches are included. Override per-run in `config.yaml → filters.job_types`.

### Salary Filtering

Set `target.min_salary` in `resume.json`. Enable filtering by setting `filters.apply_salary_filter: true` in `config.yaml`. Jobs whose detected maximum salary is below your minimum are excluded (only when salary is detectable in the posting).

---

## Job Sources

| Source | Type | Auth Required |
|---|---|---|
| **LinkedIn Jobs** | Public guest API | ❌ None |
| **LinkedIn Posts** | Posts with job links | ✅ `LINKEDIN_EMAIL` + `LINKEDIN_PASSWORD` |
| **Naukri.com** | Internal API | ❌ None |
| **Indeed India** | HTML scraping | ❌ None |
| **Foundit.in** | Internal API (Monster India) | ❌ None |
| **Hirist.tech** | JSON API | ❌ None |
| **Cutshort.io** | GraphQL API | ❌ None |
| **Internshala** | HTML scraping | ❌ None (junior/fresher roles) |
| **Company Career Pages** | HTML scraping | ❌ None |

### Company Career Pages

Visits career pages of 30+ top Indian tech companies:

Razorpay · PhonePe · Groww · CRED · Meesho · Swiggy · Zomato · Flipkart · Paytm · Ola · BrowserStack · Freshworks · Zoho · Infosys · TCS · Wipro · HCL · Atlassian India · Postman · ShareChat · and more.

Add more by editing `company_careers.json`.

---

## Scoring System

Each job receives a 0–100 relevance score composed of five dimensions:

| Component | Weight | Logic |
|---|---|---|
| **Title match** | 25 pts | Exact / partial / word-overlap match vs. target titles |
| **Skills match** | 35 pts | % of resume skills (primary weighted 40%) found in job text |
| **Project tech match** | 15 pts | % of your project technologies found in job text |
| **Location match** | 15 pts | City match, remote flag, or relocation willingness |
| **Experience level** | 10 pts | Junior/mid/senior keywords + years range match |

All weights are configurable in `config.yaml → scoring`.

---

## Configuration Reference

### `config.yaml`

```yaml
search:
  days_back: 1               # Only jobs posted in the last N days
  min_relevance_score: 40    # 0–100; raise for tighter matches
  max_results_per_source: 50 # Raw fetch cap per source
  sources:
    linkedin_jobs: true
    naukri: true
    # ... toggle any source on/off

filters:
  locations: []              # Override location (empty = use resume.json)
  remote_only: false         # true = only remote/WFH jobs
  job_titles: []             # Override job titles (empty = use resume.json)
  job_types: []              # Override job types (empty = use resume.json)
  apply_salary_filter: false # Enable min-salary hard filter
  excluded_keywords:
    - "10+ years"
    - "unpaid"
  required_keywords: []      # Every job MUST contain all of these

scoring:
  title_match_weight: 25
  skills_match_weight: 35
  project_match_weight: 15
  location_match_weight: 15
  experience_match_weight: 10
```

### `resume.json` schema

| Field | Purpose |
|---|---|
| `personal.location` | City/country for location scoring; `remote_ok`; `willing_to_relocate` |
| `target.job_titles` | Primary search queries and title-match scoring |
| `target.job_types` | Hard filter: `full-time`, `contract`, `internship`, etc. |
| `target.experience_level` | `junior` / `mid` / `senior` – drives level-keyword scoring |
| `target.min_salary` | Minimum acceptable annual salary |
| `experience.years_total` | Used for numeric years-range matching |
| `skills.*` | All skill categories contribute to skills-match scoring |
| `projects[].technologies` | Project tech pool used for separate project-match scoring |

---

## Running on GitHub Actions (Automated Daily Emails)

The cron job in `.github/workflows/daily-job-alert.yml` runs every day at **3:30 AM UTC (≈ 9:00 AM IST)**.

Add these secrets to your GitHub repository (`Settings → Secrets → Actions`):

- `EMAIL_SENDER`
- `EMAIL_PASSWORD`
- `EMAIL_RECIPIENT`
- `LINKEDIN_EMAIL` *(optional)*
- `LINKEDIN_PASSWORD` *(optional)*

You can also trigger a manual run from the **Actions** tab with dry-run or send-test options.

---

## Project Structure

```
auto-job-bot/
├── main.py                  # Entry point & CLI
├── resume.json              # ← Edit this: your resume/profile
├── config.yaml              # ← Edit this: search & filter preferences
├── company_careers.json     # Curated list of company career pages
├── .env.example             # Environment variable template
├── requirements.txt
├── seen_jobs.json           # Auto-generated: deduplication history
└── src/
    ├── resume_parser.py     # Parse resume.json → ResumeProfile
    ├── job_searcher.py      # Aggregator + query builder for all scrapers
    ├── job_filter.py        # Profile-based hard filters + relevance scoring
    ├── email_sender.py      # HTML email composition + SMTP delivery
    └── scrapers/
        ├── linkedin.py      # LinkedIn Jobs (guest API) + Posts
        ├── naukri.py        # Naukri.com internal API
        ├── indeed.py        # Indeed India HTML scraping
        ├── foundit.py       # Foundit.in (Monster India) API
        ├── hirist.py        # Hirist.tech API
        ├── cutshort.py      # Cutshort.io GraphQL
        ├── internshala.py   # Internshala (entry-level)
        ├── company_careers.py # Company career page scraper
        └── base.py          # Base class + shared HTTP utilities
```

---

## Tips

- **Too many results?** Raise `min_relevance_score` to 55–65.
- **Too few results?** Lower `min_relevance_score` to 30, or add more titles to `resume.json → target.job_titles`.
- **Add your real projects:** The more `projects[].technologies` you list, the better the project-match scoring works.
- **Remote jobs only:** Set `filters.remote_only: true` in config.yaml.
- **Add a company:** Edit `company_careers.json` with a `name`, `careers_url`, and optional `job_list_selector`.
- **LinkedIn posts:** Enable `linkedin_posts: true` in config.yaml and set `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` secrets.
- **Multiple recipients:** Set `EMAIL_RECIPIENT=you@gmail.com,friend@gmail.com` (comma-separated).
