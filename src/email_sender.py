"""
Email sender: composes a beautiful HTML daily job digest and delivers it
via SMTP (Gmail, Outlook, or any SMTP provider).

Environment variables required:
  EMAIL_SENDER      – address to send from (e.g. you@gmail.com)
  EMAIL_PASSWORD    – SMTP password / app password
  EMAIL_RECIPIENT   – comma-separated list of recipient addresses

Optional:
  EMAIL_SMTP_HOST   – defaults to smtp.gmail.com
  EMAIL_SMTP_PORT   – defaults to 587
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .job_searcher import JobPosting
from .job_filter import get_matched_skills, get_matched_projects

logger = logging.getLogger(__name__)

# Source badge colours
SOURCE_COLORS: dict[str, str] = {
    "LinkedIn":        "#0a66c2",
    "LinkedIn Post":   "#0a66c2",
    "Naukri":          "#ff7555",
    "Indeed India":    "#003a9b",
    "Foundit":         "#e8433a",
    "Hirist":          "#6c3ec9",
    "Cutshort":        "#1c75bc",
    "Internshala":     "#00aeef",
    "Company Careers": "#2e7d32",
}

DEFAULT_COLOR = "#555555"

JOB_TYPE_LABELS: dict[str, str] = {
    "full-time":   "Full-Time",
    "full time":   "Full-Time",
    "part-time":   "Part-Time",
    "part time":   "Part-Time",
    "contract":    "Contract",
    "freelance":   "Freelance",
    "internship":  "Internship",
    "remote":      "Remote",
}


def _source_color(source: str) -> str:
    for key, color in SOURCE_COLORS.items():
        if key.lower() in source.lower():
            return color
    return DEFAULT_COLOR


def _score_badge(score: int) -> str:
    if score >= 80:
        color, label = "#1b5e20", "Excellent Match"
    elif score >= 65:
        color, label = "#2e7d32", "Strong Match"
    elif score >= 50:
        color, label = "#f57f17", "Good Match"
    else:
        color, label = "#e65100", "Partial Match"
    return (
        f'<span style="background:{color};color:#fff;'
        f"font-size:11px;padding:2px 8px;border-radius:12px;"
        f'font-weight:600;">{label} {score}%</span>'
    )


def _job_type_badge(job_type: str) -> str:
    if not job_type:
        return ""
    normalized = job_type.lower().replace("-", " ").replace("_", " ")
    label = JOB_TYPE_LABELS.get(normalized, job_type.title())
    return (
        f'<span style="background:#ede7f6;color:#4527a0;font-size:11px;'
        f'padding:2px 7px;border-radius:10px;font-weight:600;margin-left:6px;">'
        f'{label}</span>'
    )


def _skills_pills(skills: list[str]) -> str:
    if not skills:
        return ""
    pills = "".join(
        f'<span style="background:#e8f5e9;color:#1b5e20;font-size:11px;'
        f'padding:2px 8px;border-radius:10px;margin:2px;display:inline-block;">'
        f'{skill}</span>'
        for skill in skills
    )
    return (
        f'<div style="margin-top:10px;">'
        f'<span style="font-size:11px;color:#888;font-weight:600;">MATCHED SKILLS: </span>'
        f'{pills}</div>'
    )


def _projects_line(projects: list[str]) -> str:
    if not projects:
        return ""
    names = ", ".join(projects)
    return (
        f'<div style="margin-top:6px;font-size:12px;color:#555;">'
        f'<span style="font-weight:600;color:#1565c0;">Projects: </span>{names}</div>'
    )


def _job_card_html(job: JobPosting, profile: Any) -> str:
    remote_badge = (
        '<span style="background:#e3f2fd;color:#1565c0;font-size:11px;'
        'padding:2px 7px;border-radius:10px;font-weight:600;margin-left:6px;">Remote</span>'
        if job.remote
        else ""
    )
    type_badge = _job_type_badge(job.job_type) if job.job_type else ""
    salary_line = (
        f'<div style="color:#388e3c;font-size:13px;margin-top:4px;">&#x1F4B0; {job.salary}</div>'
        if job.salary
        else ""
    )
    source_color = _source_color(job.source)
    posted = job.posted_at[:10] if job.posted_at and len(job.posted_at) >= 10 else job.posted_at

    # Matched skills and projects for "why this matched" section
    matched_skills = get_matched_skills(job, profile) if profile else []
    matched_projs = get_matched_projects(job, profile) if profile else []
    skills_html = _skills_pills(matched_skills)
    projs_html = _projects_line(matched_projs)

    return f"""
<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;
            padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.07);">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
    <div style="flex:1;min-width:0;">
      <div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">
        {job.title}{remote_badge}{type_badge}
      </div>
      <div style="font-size:14px;color:#444;margin-bottom:2px;">
        &#x1F3E2; <strong>{job.company}</strong>
        &nbsp;&middot;&nbsp; &#x1F4CD; {job.location}
      </div>
      {salary_line}
    </div>
    <div style="text-align:right;flex-shrink:0;">
      <span style="background:{source_color};color:#fff;font-size:11px;
                   padding:2px 9px;border-radius:10px;font-weight:600;">
        {job.source}
      </span>
      <div style="margin-top:6px;">{_score_badge(job.relevance_score)}</div>
      {"<div style='color:#999;font-size:11px;margin-top:4px;'>&#x1F4C5; " + posted + "</div>" if posted else ""}
    </div>
  </div>
  {"<p style='font-size:13px;color:#555;margin:12px 0 4px;line-height:1.6;'>" + job.short_description + "</p>" if job.short_description else ""}
  {skills_html}
  {projs_html}
  <div style="margin-top:14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <a href="{job.apply_url}"
       style="background:linear-gradient(135deg,#1a1a2e,#0f3460);color:#fff;text-decoration:none;
              padding:10px 22px;border-radius:6px;font-size:13px;font-weight:700;
              letter-spacing:.3px;display:inline-block;">
      Apply Now &rarr;
    </a>
    <span style="font-size:11px;color:#aaa;word-break:break-all;">{job.apply_url[:80]}{"..." if len(job.apply_url) > 80 else ""}</span>
  </div>
</div>"""


def build_html_email(
    jobs: list[JobPosting],
    profile: Any,
    config: dict,
) -> str:
    """Compose the full HTML email body."""
    today = datetime.now(tz=timezone.utc).strftime("%A, %B %-d, %Y")
    job_count = len(jobs)

    source_counts: dict[str, int] = {}
    for job in jobs:
        source_counts[job.source] = source_counts.get(job.source, 0) + 1

    source_pills = "".join(
        f'<span style="background:{_source_color(src)};color:#fff;'
        f"font-size:12px;padding:3px 10px;border-radius:12px;"
        f'font-weight:600;margin:2px;">{src} ({cnt})</span>'
        for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1])
    )

    job_types_display = ", ".join(
        JOB_TYPE_LABELS.get(jt.lower().replace("-", " "), jt.title())
        for jt in (profile.job_types if profile else [])
    ) or "Any"

    cards_html = "\n".join(_job_card_html(j, profile) for j in jobs)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Daily Job Alert &#x2013; {today}</title>
</head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 60%,#0f3460 100%);
              border-radius:14px;padding:28px 32px;margin-bottom:24px;color:#fff;">
    <div style="font-size:13px;opacity:.7;margin-bottom:6px;">&#x1F4C5; {today}</div>
    <div style="font-size:26px;font-weight:800;margin-bottom:4px;">
      &#x1F3AF; Your Daily Job Digest
    </div>
    <div style="font-size:15px;opacity:.85;">
      Hi <strong>{profile.name if profile else "there"}</strong> &#x2014; we found
      <strong>{job_count} new job{"s" if job_count != 1 else ""}</strong>
      matching your profile today!
    </div>
    <div style="margin-top:14px;line-height:1.8;">{source_pills}</div>
  </div>

  <!-- Profile summary strip -->
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;
              padding:14px 20px;margin-bottom:24px;font-size:13px;color:#555;line-height:2;">
    <strong>Your Profile:</strong><br>
    &#x1F4CD; {profile.location_display if profile else "India"}
    &nbsp;&middot;&nbsp;
    &#x1F4BC; {profile.experience_level.title() if profile else "Mid"} &middot; {profile.years_experience if profile else 0} yrs experience
    &nbsp;&middot;&nbsp;
    &#x1F4CB; Looking for: <strong>{job_types_display}</strong>
    <br>
    &#x1F511; Top skills: {", ".join(profile.primary_skills[:6]) if profile else "N/A"}
  </div>

  <!-- Job cards -->
  {cards_html if jobs else _empty_state_html()}

  <!-- Footer -->
  <div style="text-align:center;color:#aaa;font-size:12px;margin-top:32px;padding-top:16px;
              border-top:1px solid #e8e8e8;">
    <p>
      Powered by <strong>auto-job-bot</strong> &#x2013; India Job Alert System<br>
      Scraped from LinkedIn, Naukri, Indeed India, Foundit, Hirist, Cutshort &amp; Company Career Pages<br>
      To update your preferences, edit <code>resume.json</code> and <code>config.yaml</code>
    </p>
  </div>
</div>
</body>
</html>"""


def _empty_state_html() -> str:
    return """
<div style="text-align:center;padding:40px;color:#888;background:#fff;
            border-radius:10px;border:1px dashed #ddd;">
  <div style="font-size:40px;margin-bottom:12px;">&#x1F50D;</div>
  <div style="font-size:16px;font-weight:600;margin-bottom:8px;">No new jobs today</div>
  <div style="font-size:13px;">Try lowering <code>min_relevance_score</code> in config.yaml
  or adding more job titles to your resume profile.</div>
</div>"""


def build_plain_text(jobs: list[JobPosting], profile: Any) -> str:
    """Fallback plain-text version of the email."""
    today = datetime.now(tz=timezone.utc).strftime("%A, %B %d, %Y")
    job_types_display = ", ".join(profile.job_types if profile else ["full-time"])
    lines = [
        f"Daily Job Digest – {today}",
        f"Hi {profile.name if profile else 'there'}, found {len(jobs)} new job(s) today!",
        f"Looking for: {job_types_display} | Location: {profile.location_display if profile else 'India'}",
        "=" * 60,
    ]
    for i, job in enumerate(jobs, 1):
        lines += [
            f"\n{i}. {job.title}",
            f"   Company  : {job.company}",
            f"   Location : {job.location}{'  [REMOTE]' if job.remote else ''}",
            f"   Job Type : {job.job_type or 'Not specified'}",
            f"   Salary   : {job.salary or 'Not specified'}",
            f"   Source   : {job.source}",
            f"   Score    : {job.relevance_score}%",
            f"   Apply    : {job.apply_url}",
        ]
        if job.short_description:
            lines.append(f"   Summary  : {job.short_description[:200]}")
    lines += [
        "",
        "=" * 60,
        "Powered by auto-job-bot – India Job Alert System",
        "Edit resume.json and config.yaml to update your preferences.",
    ]
    return "\n".join(lines)


def send_email(
    jobs: list[JobPosting],
    profile: Any,
    config: dict,
) -> bool:
    """
    Send the daily job digest email.

    Returns True on success, False on failure.
    """
    email_cfg = config.get("email", {})
    sender = os.getenv("EMAIL_SENDER", email_cfg.get("sender", ""))
    password = os.getenv("EMAIL_PASSWORD", "")
    recipients_env = os.getenv("EMAIL_RECIPIENT", "")
    recipients: list[str] = (
        [r.strip() for r in recipients_env.split(",") if r.strip()]
        if recipients_env
        else email_cfg.get("recipients", [])
    )

    if not sender or not password:
        logger.error(
            "Email not configured – set EMAIL_SENDER and EMAIL_PASSWORD env vars"
        )
        return False

    if not recipients:
        logger.error("No recipients configured – set EMAIL_RECIPIENT env var")
        return False

    smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))

    today = datetime.now(tz=timezone.utc).strftime("%b %-d, %Y")
    subject_prefix = email_cfg.get("subject_prefix", "[Job Alert]")
    subject = f"{subject_prefix} {len(jobs)} New Job{'s' if len(jobs) != 1 else ''} for You – {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Job Alert Bot <{sender}>"
    msg["To"] = ", ".join(recipients)

    html_body = build_html_email(jobs, profile, config)
    plain_body = build_plain_text(jobs, profile)

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        logger.info(
            "Connecting to SMTP %s:%s as %s → recipients: %s",
            smtp_host, smtp_port, sender, recipients,
        )
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.sendmail(sender, recipients, msg.as_bytes())
        logger.info("Email sent to %s with %d jobs", recipients, len(jobs))
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed. For Gmail, use an App Password: "
            "https://myaccount.google.com/apppasswords"
        )
        return False
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False
