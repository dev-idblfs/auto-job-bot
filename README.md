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
3. **Scores each job** 0–100 based on title match, skills overlap, location fit, and experience level.
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

Open `resume.json` and fill in your details:

```jsonc
{
  "personal": {
    "name": "Your Name",
    "location": { "city": "Bengaluru", "country": "India", "remote_ok": true }
  },
  "target": {
    "job_titles": ["Backend Engineer", "Python Developer", "SDE-2"],
    "experience_level": "mid"   // junior | mid | senior
  },
  "skills": {
    "primary":   ["Python", "Django", "FastAPI", "PostgreSQL"],
    "secondary": ["React", "AWS", "Docker"]
  }
  // ... see resume.json for full schema
}
```

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

Each job gets a 0–100 relevance score:

| Component | Weight | Logic |
|---|---|---|
| **Title match** | 30 pts | Exact/partial match vs. your target titles |
| **Skills match** | 40 pts | % of your skills/tech stack found in the job |
| **Location match** | 15 pts | City match, remote flag, or relocation willingness |
| **Experience level** | 15 pts | Junior/mid/senior keywords match your profile |

Weights are configurable in `config.yaml → scoring`.

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
- **Too few results?** Lower `min_relevance_score` to 30, or add more titles to `resume.json → target.job_titles`.
- **Add a company:** Edit `company_careers.json` and add an entry with `name`, `careers_url`, and optionally a CSS `job_list_selector`.
- **LinkedIn posts:** Enable `linkedin_posts: true` in config.yaml and set `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` secrets.
- **Remote jobs only:** Set `filters.remote_only: true` in config.yaml.
