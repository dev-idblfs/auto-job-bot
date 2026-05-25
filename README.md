# auto-job-bot 🤖

**Daily India Job Alert System** – scrapes jobs from LinkedIn, Naukri, Indeed India, Foundit, Hirist, Cutshort, company career pages and more; ranks them against your resume; and emails a curated digest every day.

---

## How It Works

```
resume.json  +  config.yaml
       │
       ▼
 Job Scrapers (9 sources)
       │
       ▼
 Filter & Score (0–100 relevance)
       │
       ▼
 HTML Email Digest  →  your inbox
```

1. **Reads your resume** from `resume.json` to extract skills, experience level, location, and target job titles.
2. **Scrapes 9 job sources** in parallel (LinkedIn, Naukri, Indeed India, Foundit, Hirist, Cutshort, Internshala, LinkedIn Posts, Company Career Pages).
3. **Scores each job** 0–100 based on title match, skills overlap, project domain alignment, location fit, and experience level.
4. **Deduplicates** – remembers which jobs were already sent so you never see the same posting twice.
5. **Emails a digest** with rich HTML cards including company, location, salary, match score, and a direct **Apply Now** link.

Runs daily via GitHub Actions cron (`30 3 * * *` UTC = ~9 AM IST).

---

## Quick Start

### 1 · Clone and install

```bash
git clone <your-repo>
cd auto-job-bot
pip install -r requirements.txt
```

### 2 · Edit your resume

Open `resume.json` and fill in your details. **The more detail you add, the better the matching:**

```jsonc
{
  "personal": {
    "name": "Your Name",
    "location": { "city": "Bengaluru", "state": "Karnataka", "country": "India",
                  "remote_ok": true, "willing_to_relocate": false }
  },
  "target": {
    "job_titles": ["Backend Engineer", "Python Developer", "SDE-2"],
    "job_types": ["full-time"],          // full-time | part-time | contract | internship
    "experience_level": "mid",           // junior | mid | senior
    "industries": ["fintech", "saas"]    // used for bonus scoring
  },
  "skills": {
    "primary":   ["Python", "Django", "FastAPI", "PostgreSQL"],
    "secondary": ["React", "AWS", "Docker"]
  },
  "projects": [
    {
      "name": "E-Commerce Platform",
      "description": "Full-stack e-commerce site with React, Django REST, PostgreSQL, Redis.",
      "technologies": ["Python", "Django", "React", "PostgreSQL", "Redis", "AWS"]
    }
  ]
  // ... see resume.json for full schema
}
```

**Key matching fields:**

| Field | How it affects matching |
|---|---|
| `location.city` | Jobs in this city score higher; also used in hard location filter |
| `location.remote_ok` | Remote jobs score full points when `true` |
| `target.job_types` | Jobs with a conflicting type (e.g. internship when you want full-time) are filtered out |
| `target.experience_level` | Sets the experience band matched in job descriptions |
| `target.industries` | Provides up to 5 bonus points for matching industry keywords |
| `skills.primary` | Heavily weighted in scoring; drives search queries |
| `projects[].technologies` | Counted alongside skills; richer project descriptions = better domain matching |
| `projects[].description` | Domain phrases extracted (e.g. "real-time analytics", "microservices") and matched against job text |

### 3 · Set environment variables

Copy `.env.example` to `.env` and fill in your email credentials:

```bash
cp .env.example .env
# Edit .env
```

| Variable | Required | Description |
|---|---|---|
| `EMAIL_SENDER` | ✅ | Your Gmail address |
| `EMAIL_PASSWORD` | ✅ | [Gmail App Password](https://myaccount.google.com/apppasswords) |
| `EMAIL_RECIPIENT` | ✅ | Recipient(s), comma-separated |
| `LINKEDIN_EMAIL` | Optional | LinkedIn email (for post scraping) |
| `LINKEDIN_PASSWORD` | Optional | LinkedIn password (for post scraping) |

> **Gmail users:** Enable 2FA and generate an [App Password](https://myaccount.google.com/apppasswords). Do **not** use your main Google password.

### 4 · Test it

```bash
# Dry run – prints matches, no email
python main.py --dry-run --verbose

# Send a test email (ignores deduplication)
python main.py --send-test

# Full run
python main.py
```

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
| **Internshala** | HTML scraping | ❌ None (junior/fresher only) |
| **Company Career Pages** | HTML scraping | ❌ None |

### Company Career Pages

The scraper visits the career pages of 30+ top Indian tech companies including:

Razorpay · PhonePe · Groww · CRED · Meesho · Swiggy · Zomato · Flipkart · Paytm · Ola · BrowserStack · Freshworks · Zoho · Infosys · TCS · Wipro · HCL · Atlassian India · Postman · ShareChat · and more.

Add more companies by editing `company_careers.json`.

---

## Configuration

All settings live in `config.yaml`:

```yaml
search:
  days_back: 1             # Only jobs posted today
  min_relevance_score: 40  # 0-100; raise for tighter matches
  sources:
    naukri: true
    linkedin_jobs: true
    # ... toggle any source on/off

filters:
  locations: []            # Override to e.g. ["Bengaluru", "Remote"]
  remote_only: false       # Only remote/WFH jobs
  excluded_keywords:       # Jobs with these are dropped
    - "10+ years"
    - "unpaid"
```

---

## Scoring System

Each job gets a 0–100 relevance score built from five components:

| Component | Default Weight | Logic |
|---|---|---|
| **Title match** | 30 pts | Exact/partial/word-overlap match vs. your `target.job_titles` |
| **Skills match** | 30 pts | % of your skills & project technologies found in the job text |
| **Projects match** | 10 pts | Project domain phrases (e.g. "real-time analytics", "microservices") found in job description |
| **Location match** | 15 pts | City/region match, remote flag, or `willing_to_relocate` fallback |
| **Experience level** | 15 pts | Junior/mid/senior keywords match your `experience_level` |
| **Industry bonus** | +0–5 pts | Target industries found in job text (capped at 100 total) |

All weights are configurable in `config.yaml → scoring` (title + skills + projects + location + experience must sum to 100).

### Hard filters (applied before scoring)

Jobs that fail any hard filter are dropped entirely, regardless of score:

| Filter | Config key | Default |
|---|---|---|
| Excluded keywords | `filters.excluded_keywords` | Blocks VP/Director/10+ year roles |
| Required keywords | `filters.required_keywords` | Empty (match anything) |
| Remote only | `filters.remote_only` | `false` |
| Location whitelist | `filters.locations` | Uses resume city |
| Job type | `resume.json → target.job_types` | `["full-time"]` |

### Email digest highlights

Each job card in the daily email shows:
- Match score badge (Excellent / Strong / Good / Partial)
- Job type badge (Full-Time / Contract / etc.)
- Salary (when available)
- **"MATCHED SKILLS & TECH"** — the specific skills/technologies from your profile that appeared in the job description
- Direct **Apply Now →** button with the full apply link

---

## Running on GitHub Actions (Automated Daily Emails)

The cron job in `.github/workflows/daily-job-alert.yml` runs every day at 3:30 AM UTC (≈ 9:00 AM IST).

Add these secrets to your GitHub repository (`Settings → Secrets → Actions`):

- `EMAIL_SENDER`
- `EMAIL_PASSWORD`
- `EMAIL_RECIPIENT`
- `LINKEDIN_EMAIL` *(optional)*
- `LINKEDIN_PASSWORD` *(optional)*

---

## Project Structure

```
auto-job-bot/
├── main.py                  # Entry point
├── resume.json              # ← Edit this: your resume/profile
├── config.yaml              # ← Edit this: search preferences
├── company_careers.json     # Curated list of company career pages
├── .env.example             # Environment variable template
├── requirements.txt
├── seen_jobs.json           # Auto-generated: deduplication history
└── src/
    ├── resume_parser.py     # Parse resume.json → ResumeProfile
    ├── job_searcher.py      # Aggregator for all scrapers
    ├── job_filter.py        # Filter + relevance scoring
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
- **Too few results?** Lower `min_relevance_score` to 30, add more titles to `resume.json → target.job_titles`, or set `search.days_back: 3`.
- **Contract / freelance jobs:** Add `"contract"` to `resume.json → target.job_types`.
- **Only want remote jobs:** Set `filters.remote_only: true` in config.yaml.
- **Specific cities:** Add them to `filters.locations`, e.g. `["Bengaluru", "Hyderabad", "Remote"]`.
- **Add a company:** Edit `company_careers.json` and add an entry with `name`, `careers_url`, and optionally a CSS `job_list_selector`.
- **LinkedIn posts:** Enable `linkedin_posts: true` in config.yaml and set `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` secrets.
- **Better project matching:** Write detailed descriptions in `resume.json → projects[].description` — domain phrases like "real-time analytics", "payment gateway", "microservices architecture" are extracted and matched against job descriptions.
