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


def _source_color(source: str) -> str:
    for key, color in SOURCE_COLORS.items():
        if key.lower() in source.lower():
            return color
    return DEFAULT_COLOR


def _score_badge(score: int) -> tuple[str, str]:
    """Return (color, label) for the given relevance score."""
    if score >= 80:
        return "#1b5e20", "Excellent Match"
    if score >= 65:
        return "#2e7d32", "Strong Match"
    if score >= 50:
        return "#f57f17", "Good Match"
    return "#e65100", "Partial Match"


def _score_badge_html(score: int) -> str:
    color, label = _score_badge(score)
    return (
        f'<span style="background:{color};color:#fff;'
        f"font-size:11px;padding:2px 8px;border-radius:12px;"
        f'font-weight:600;">{label} {score}%</span>'
    )


def _matched_skills_html(skills: list[str]) -> str:
    """Render small skill pills for skills that matched the job description."""
    if not skills:
        return ""
    pills = "".join(
        f'<span style="background:#e8f5e9;color:#2e7d32;font-size:11px;'
        f'padding:2px 7px;border-radius:10px;margin:2px 2px 0 0;'
        f'display:inline-block;font-weight:500;">{skill}</span>'
        for skill in skills[:12]  # cap at 12 to keep card compact
    )
    return (
        f'<div style="margin-top:10px;">'
        f'<span style="font-size:11px;color:#888;font-weight:600;">MATCHED SKILLS &amp; TECH: </span>'
        f'{pills}'
        f'</div>'
    )


def _job_card_html(job: JobPosting) -> str:
    remote_badge = (
        '<span style="background:#e3f2fd;color:#1565c0;font-size:11px;'
        'padding:2px 7px;border-radius:10px;font-weight:600;margin-left:6px;">Remote</span>'
        if job.remote
        else ""
    )
    salary_line = (
        f'<div style="color:#388e3c;font-size:13px;margin-top:4px;">💰 {job.salary}</div>'
        if job.salary
        else ""
    )
    source_color = _source_color(job.source)
    posted = job.posted_at[:10] if job.posted_at and len(job.posted_at) >= 10 else job.posted_at

    # matched_skills is attached by filter_and_rank_jobs; graceful fallback if absent
    matched_skills: list[str] = getattr(job, "matched_skills", [])
    skills_html = _matched_skills_html(matched_skills)

    job_type_badge = ""
    if job.job_type:
        job_type_badge = (
            f'<span style="background:#f3e5f5;color:#6a1b9a;font-size:11px;'
            f'padding:2px 7px;border-radius:10px;font-weight:600;margin-left:6px;">'
            f'{job.job_type.title()}</span>'
        )

    return f"""
<div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;
            padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.07);">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;">
    <div style="flex:1;min-width:0;">
      <div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-bottom:4px;">
        {job.title}{remote_badge}{job_type_badge}
      </div>
      <div style="font-size:14px;color:#444;margin-bottom:2px;">
        🏢 <strong>{job.company}</strong>
        &nbsp;·&nbsp; 📍 {job.location}
      </div>
      {salary_line}
    </div>
    <div style="text-align:right;min-width:145px;margin-left:12px;">
      <span style="background:{source_color};color:#fff;font-size:11px;
                   padding:2px 9px;border-radius:10px;font-weight:600;">
        {job.source}
      </span>
      <div style="margin-top:6px;">{_score_badge_html(job.relevance_score)}</div>
      {"<div style='color:#999;font-size:11px;margin-top:4px;'>📅 " + posted + "</div>" if posted else ""}
    </div>
  </div>
  {"<p style='font-size:13px;color:#555;margin:12px 0 4px;line-height:1.6;'>" + job.short_description + "</p>" if job.short_description else ""}
  {skills_html}
  <div style="margin-top:14px;">
    <a href="{job.apply_url}"
       style="background:#1a1a2e;color:#fff;text-decoration:none;
              padding:9px 20px;border-radius:6px;font-size:13px;font-weight:600;
              display:inline-block;">
      Apply Now →
    </a>
    <span style="font-size:12px;color:#aaa;margin-left:10px;">{job.apply_url[:60]}{'…' if len(job.apply_url) > 60 else ''}</span>
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

    # Group by source for the summary header
    source_counts: dict[str, int] = {}
    for job in jobs:
        source_counts[job.source] = source_counts.get(job.source, 0) + 1

    source_pills = "".join(
        f'<span style="background:{_source_color(src)};color:#fff;'
        f"font-size:12px;padding:3px 10px;border-radius:12px;"
        f'font-weight:600;margin:2px;">{src} ({cnt})</span>'
        for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1])
    )

    # Match quality distribution
    excellent = sum(1 for j in jobs if j.relevance_score >= 80)
    strong    = sum(1 for j in jobs if 65 <= j.relevance_score < 80)
    good      = sum(1 for j in jobs if 50 <= j.relevance_score < 65)
    partial   = sum(1 for j in jobs if j.relevance_score < 50)

    match_summary = "".join([
        f'<span style="color:#1b5e20;font-weight:600;">{excellent} Excellent</span>, ' if excellent else "",
        f'<span style="color:#2e7d32;font-weight:600;">{strong} Strong</span>, '         if strong    else "",
        f'<span style="color:#f57f17;font-weight:600;">{good} Good</span>, '             if good      else "",
        f'<span style="color:#e65100;font-weight:600;">{partial} Partial</span>'         if partial   else "",
    ]).rstrip(", ")

    cards_html = "\n".join(_job_card_html(j) for j in jobs)

    location_display = profile.location_display or "India"
    level_display = f"{profile.experience_level.title()} · {profile.years_experience} yrs"
    skills_display = ", ".join(profile.primary_skills[:6])
    job_types_display = ", ".join(t.title() for t in profile.job_types) if profile.job_types else "Any"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Daily Job Alert – {today}</title>
</head>
<body style="margin:0;padding:0;background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:700px;margin:0 auto;padding:24px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 60%,#0f3460 100%);
              border-radius:14px;padding:28px 32px;margin-bottom:24px;color:#fff;">
    <div style="font-size:13px;opacity:.7;margin-bottom:6px;">📅 {today}</div>
    <div style="font-size:26px;font-weight:800;margin-bottom:4px;">
      🎯 Your Daily Job Digest
    </div>
    <div style="font-size:15px;opacity:.85;">
      Hi <strong>{profile.name or "there"}</strong> — found
      <strong>{job_count} new job{"s" if job_count != 1 else ""}</strong>
      matching your profile today!
    </div>
    <div style="margin-top:6px;font-size:13px;opacity:.75;">
      Match quality: {match_summary}
    </div>
    <div style="margin-top:14px;line-height:1.8;">{source_pills}</div>
  </div>

  <!-- Profile summary strip -->
  <div style="background:#fff;border:1px solid #e0e0e0;border-radius:10px;
              padding:14px 20px;margin-bottom:24px;font-size:13px;color:#555;">
    <div style="font-weight:700;color:#1a1a2e;margin-bottom:6px;">Your Profile (filter criteria)</div>
    <table style="border-collapse:collapse;width:100%;">
      <tr>
        <td style="padding:3px 12px 3px 0;white-space:nowrap;">📍 <strong>Location</strong></td>
        <td style="padding:3px 0;">{location_display}{"&nbsp;·&nbsp; Remote OK" if profile.remote_ok else ""}</td>
      </tr>
      <tr>
        <td style="padding:3px 12px 3px 0;white-space:nowrap;">💼 <strong>Experience</strong></td>
        <td style="padding:3px 0;">{level_display}</td>
      </tr>
      <tr>
        <td style="padding:3px 12px 3px 0;white-space:nowrap;">🔑 <strong>Primary Skills</strong></td>
        <td style="padding:3px 0;">{skills_display}</td>
      </tr>
      <tr>
        <td style="padding:3px 12px 3px 0;white-space:nowrap;">📋 <strong>Job Types</strong></td>
        <td style="padding:3px 0;">{job_types_display}</td>
      </tr>
      <tr>
        <td style="padding:3px 12px 3px 0;white-space:nowrap;">🎯 <strong>Targets</strong></td>
        <td style="padding:3px 0;">{", ".join(profile.target_titles[:4])}</td>
      </tr>
    </table>
  </div>

  <!-- Job cards -->
  {cards_html if jobs else _empty_state_html()}

  <!-- Footer -->
  <div style="text-align:center;color:#aaa;font-size:12px;margin-top:32px;padding-top:16px;
              border-top:1px solid #e8e8e8;">
    <p>
      Powered by <strong>auto-job-bot</strong> · India Job Alert System<br>
      Matched on: location · experience level · skills · project domains<br>
      Sources: LinkedIn, Naukri, Indeed India, Foundit, Hirist, Cutshort &amp; Company Career Pages<br>
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
  <div style="font-size:40px;margin-bottom:12px;">🔍</div>
  <div style="font-size:16px;font-weight:600;margin-bottom:8px;">No new jobs today</div>
  <div style="font-size:13px;">
    Try lowering <code>min_relevance_score</code> in config.yaml,
    adding more job titles, or enabling more sources.
  </div>
</div>"""


def build_plain_text(jobs: list[JobPosting], profile: Any) -> str:
    """Fallback plain-text version of the email."""
    today = datetime.now(tz=timezone.utc).strftime("%A, %B %d, %Y")
    lines = [
        f"Daily Job Digest – {today}",
        f"Hi {profile.name or 'there'}, found {len(jobs)} new job(s) today!",
        "",
        f"Profile: {profile.location_display or 'India'} | "
        f"{profile.experience_level.title()} {profile.years_experience} yrs | "
        f"Types: {', '.join(t.title() for t in profile.job_types)}",
        "=" * 65,
    ]
    for i, job in enumerate(jobs, 1):
        matched: list[str] = getattr(job, "matched_skills", [])
        lines += [
            f"\n{i}. [{job.relevance_score}%] {job.title}",
            f"   Company    : {job.company}",
            f"   Location   : {job.location}{'  [REMOTE]' if job.remote else ''}",
            f"   Job Type   : {job.job_type or 'Not specified'}",
            f"   Salary     : {job.salary or 'Not specified'}",
            f"   Source     : {job.source}",
            f"   Posted     : {job.posted_at[:10] if job.posted_at else 'Unknown'}",
            f"   Matched    : {', '.join(matched[:8]) if matched else 'N/A'}",
            f"   Apply Link : {job.apply_url}",
        ]
        if job.short_description:
            lines.append(f"   Summary    : {job.short_description[:200]}")
    lines += ["", "=" * 65, "Powered by auto-job-bot · India Job Alert System"]
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
    subject = (
        f"{subject_prefix} {len(jobs)} New Job{'s' if len(jobs) != 1 else ''} "
        f"for {profile.name or 'You'} – {today}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Job Alert Bot <{sender}>"
    msg["To"] = ", ".join(recipients)

    html_body = build_html_email(jobs, profile, config)
    plain_body = build_plain_text(jobs, profile)

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.sendmail(sender, recipients, msg.as_bytes())
        logger.info(
            "Email sent to %s with %d jobs (subject: %s)",
            recipients, len(jobs), subject,
        )
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
