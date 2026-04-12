from __future__ import annotations

import json
from datetime import datetime
from email.utils import format_datetime
from html import escape
from urllib.parse import quote

from rn_opportunity_radar.models import EmployerRollup, OpportunityLead, SourceReport
from rn_opportunity_radar.persistence import is_kept_lead
from rn_opportunity_radar.profiles import PROFILE_LENSES, sorted_leads_for_profile


def _favicon_data_uri() -> str:
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<rect width='64' height='64' rx='16' fill='#17334f'/>"
        "<path d='M20 46h24V18H20z' fill='#f7f9fe' opacity='0.92'/>"
        "<path d='M31 22h2v20h-2zM22 31h20v2H22z' fill='#f37d67'/>"
        "</svg>"
    )
    return f"data:image/svg+xml,{quote(svg)}"


def _format_date_label(value: str) -> str:
    raw = value.strip()
    if not raw:
        return "Date not listed"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def _format_datetime_label(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    tz_name = parsed.strftime("%Z") or "local"
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} at {hour}:{parsed.strftime('%M')} {parsed.strftime('%p')} {tz_name}"


def _display_label(value: str) -> str:
    return value.replace("_", " ").title()


def _track_label(track: str) -> str:
    return "Core RN Oregon" if track == "core_rn_oregon" else "Frontier Ecosystem"


def _badge(label: str, tone: str) -> str:
    return f'<span class="badge badge-{escape(tone)}">{escape(label)}</span>'


def _list_tags(values: list[str]) -> str:
    if not values:
        return ""
    return "".join(_badge(value, "tag") for value in values[:6])


def _render_reasons(reasons: list[str], label: str = "Why this matters") -> str:
    if not reasons:
        return "<p class='muted'>Explainable reasons will appear here after scoring.</p>"
    return (
        f"<p class='reason-title'><strong>{escape(label)}:</strong></p>"
        + "<ul class='reason-list'>"
        + "".join(f"<li>{escape(reason)}</li>" for reason in reasons[:5])
        + "</ul>"
    )


def _section_title(title: str, count: int, anchor: str, subtitle: str) -> str:
    return (
        f"<section id='{anchor}' class='section'>"
        f"<div class='section-head'><div><p class='eyebrow'>{anchor.replace('-', ' ')}</p>"
        f"<h2>{escape(title)}</h2><p class='section-copy'>{escape(subtitle)}</p></div>"
        f"<div class='section-count'>{count}</div></div>"
    )


def _lead_status_badges(lead: OpportunityLead) -> str:
    track_tone = "core-track" if lead.track == "core_rn_oregon" else "frontier-track"
    badges = [
        _badge(_track_label(lead.track), track_tone),
        _badge(_display_label(lead.bucket), lead.bucket),
    ]
    if lead.lead_type == "signal":
        badges.append(_badge("Signal", "signal"))
    if lead.starred:
        badges.append(_badge("Starred", "starred"))
    if lead.saved:
        badges.append(_badge("Saved", "saved"))
    if lead.dismissed:
        badges.append(_badge("Dismissed", "dismissed"))
    if lead.status == "expired":
        badges.append(_badge("Vanished", "expired"))
    elif lead.stale_source:
        badges.append(_badge("Stale Source", "stale"))
    return "".join(badges)


def _render_meta_row(lead: OpportunityLead) -> str:
    meta_bits = [
        f"<span><strong>Employer</strong> {escape(lead.company)}</span>",
        f"<span><strong>Location</strong> {escape(lead.location or 'Not listed')}</span>",
        f"<span><strong>Posted</strong> {escape(_format_date_label(lead.posted_date))}</span>",
        f"<span><strong>Base Score</strong> {lead.score}</span>",
    ]
    return f"<p class='meta-row'>{' · '.join(meta_bits)}</p>"


def _render_profile_row(lead: OpportunityLead) -> str:
    bits = [
        f"<span><strong>Horizon</strong> {escape(_display_label(lead.horizon))}</span>",
        f"<span><strong>Geo Scope</strong> {escape(_display_label(lead.geo_scope))}</span>",
        f"<span><strong>RN Leverage</strong> {escape(_display_label(lead.rn_leverage_type))}</span>",
        f"<span><strong>Relocation</strong> {escape(_display_label(lead.relocation_risk))}</span>",
    ]
    return f"<p class='profile-row'>{' · '.join(bits)}</p>"


def _render_manual_note(lead: OpportunityLead) -> str:
    if not lead.manual_notes and not lead.pinned_reason:
        return ""
    notes = lead.manual_notes[:2]
    if lead.pinned_reason and f"Pinned: {lead.pinned_reason}" not in notes:
        notes.insert(0, f"Pinned: {lead.pinned_reason}")
    return f"<p class='manual-note'><strong>Manual note:</strong> {escape(' | '.join(notes))}</p>"


def _render_preference_effect(lead: OpportunityLead, profile_name: str | None) -> str:
    if not profile_name:
        return ""
    delta = lead.profile_preference_deltas.get(profile_name, 0)
    if delta == 0:
        return ""

    effect_label = "Preference boost" if delta > 0 else "Preference downweight"
    effect_class = "boost" if delta > 0 else "downweight"
    reasons = [
        reason
        for reason in lead.profile_reasons.get(profile_name, [])
        if "profile preferences" in reason or "manual employer interest" in reason
    ]
    reason_copy = f" {' | '.join(reasons[:2])}" if reasons else ""
    return (
        f"<p class='manual-note preference-effect preference-{effect_class}'>"
        f"<strong>{escape(effect_label)}:</strong> {delta:+d}.{escape(reason_copy)}"
        "</p>"
    )


def _render_lead_card(lead: OpportunityLead, *, profile_name: str | None = None) -> str:
    actions_label = "Open source" if lead.lead_type == "signal" else "Open listing"
    track_class = "frontier-card" if lead.track == "frontier_ecosystem" else "core-card"

    profile_block = ""
    reasons = lead.reasons
    reasons_label = "Why this matters"
    if profile_name:
        lens = PROFILE_LENSES[profile_name]
        profile_score = lead.profile_scores.get(profile_name, lead.score)
        profile_reasons = lead.profile_reasons.get(profile_name, []) or lead.reasons
        reasons = profile_reasons
        reasons_label = f"{lens.title} lens"
        profile_block = (
            f"<p class='lens-row'><strong>Lens</strong> {escape(lens.title)} · "
            f"<strong>Lens Score</strong> {profile_score}</p>"
        )

    return (
        f"<article class='card {track_class}'>"
        f"<div class='card-top'>{_lead_status_badges(lead)}</div>"
        f"<h3>{escape(lead.title)}</h3>"
        f"{_render_meta_row(lead)}"
        f"{_render_profile_row(lead)}"
        f"{profile_block}"
        f"<p class='source-row'>Source: <a href='{escape(lead.source_url)}'>{escape(lead.source_name)}</a></p>"
        f"{_render_manual_note(lead)}"
        f"{_render_preference_effect(lead, profile_name)}"
        f"<div class='tag-row'>{_list_tags(lead.tags)}</div>"
        f"{_render_reasons(reasons, reasons_label)}"
        f"<p class='card-actions'><a href='{escape(lead.detail_url)}'>{actions_label}</a></p>"
        "</article>"
    )


def _render_card_grid(leads: list[OpportunityLead], empty_copy: str, *, profile_name: str | None = None) -> str:
    html = "".join(_render_lead_card(lead, profile_name=profile_name) for lead in leads)
    if not html:
        html = f"<p class='empty'>{escape(empty_copy)}</p>"
    return f"<div class='card-grid'>{html}</div>"


def _render_employer_card(employer: EmployerRollup) -> str:
    highlights = []
    for lead in employer.highlighted_leads[:3]:
        highlights.append(
            f"<li><a href='{escape(str(lead['detail_url']))}'>{escape(str(lead['title']))}</a> "
            f"<span class='muted'>({_display_label(str(lead['bucket']))})</span></li>"
        )

    manual_badges = []
    if employer.manual_starred:
        manual_badges.append(_badge("Starred Employer", "starred"))
    if employer.manual_interest_level != "none":
        manual_badges.append(_badge(_display_label(employer.manual_interest_level), "saved"))
    if employer.pinned_reason:
        manual_badges.append(_badge("Pinned", "watch"))

    manual_bits = ""
    if employer.manual_notes:
        manual_bits = f"<p class='manual-note'><strong>Manual note:</strong> {escape(' | '.join(employer.manual_notes[:2]))}</p>"

    return (
        "<article class='card employer-card'>"
        f"<div class='card-top'>{''.join(manual_badges)}{_list_tags(employer.employer_tags)}</div>"
        f"<h3>{escape(employer.employer_name)}</h3>"
        f"<p class='meta-row'><span><strong>Tracks</strong> {escape(', '.join(_track_label(track) for track in employer.tracks))}</span> · "
        f"<span><strong>Employer Score</strong> {employer.employer_score}</span></p>"
        f"<p class='profile-row'><span><strong>Locations</strong> {escape(', '.join(employer.locations_seen[:4]) or 'Not listed')}</span> · "
        f"<span><strong>Leverage</strong> {escape(', '.join(_display_label(item) for item in employer.rn_leverage_types[:3]) or 'Mixed')}</span></p>"
        f"{manual_bits}"
        f"{_render_reasons(employer.why_it_matters, 'Why this employer matters')}"
        f"<p class='reason-title'><strong>Highlighted leads:</strong></p><ul class='reason-list'>{''.join(highlights) or '<li>No highlighted leads yet.</li>'}</ul>"
        "</article>"
    )


def _render_employer_grid(employers: list[EmployerRollup], empty_copy: str) -> str:
    html = "".join(_render_employer_card(employer) for employer in employers)
    if not html:
        html = f"<p class='empty'>{escape(empty_copy)}</p>"
    return f"<div class='card-grid'>{html}</div>"


def _render_report_row(report: SourceReport) -> str:
    status = report.status.title()
    if report.status == "ok":
        badge = _badge(status, "ok")
    elif report.status == "error":
        badge = _badge("Failed", "expired")
    else:
        badge = _badge(status, "watch")

    notes = " | ".join(report.notes + report.errors) if (report.notes or report.errors) else "No extra notes."
    return (
        "<tr>"
        f"<td>{escape(report.source_name)}</td>"
        f"<td>{escape(_track_label(report.track))}</td>"
        f"<td>{badge}</td>"
        f"<td>{report.total_fetched}</td>"
        f"<td>{report.total_relevant}</td>"
        f"<td>{'Yes' if report.used_browser else 'No'}</td>"
        f"<td>{escape(notes)}</td>"
        "</tr>"
    )


def _movement_lead_list(items: list[dict[str, object]], profile_name: str) -> str:
    if not items:
        return "<p class='empty'>No changes in this slice.</p>"
    cards = []
    lens = PROFILE_LENSES[profile_name]
    for item in items[:6]:
        profile_scores = item.get("profile_scores", {})
        score = int(profile_scores.get(profile_name, 0)) if isinstance(profile_scores, dict) else 0
        extra = []
        if "from_bucket" in item and "to_bucket" in item:
            extra.append(f"Moved from {_display_label(str(item['from_bucket']))} to {_display_label(str(item['to_bucket']))}")
        if "current_status" in item and item.get("current_status") == "expired":
            extra.append("No longer seen in the latest run")
        if item.get("pinned_reason"):
            extra.append(str(item["pinned_reason"]))
        cards.append(
            "<article class='mini-card'>"
            f"<p class='mini-kicker'>{escape(_track_label(str(item['track'])))}</p>"
            f"<h4>{escape(str(item['title']))}</h4>"
            f"<p class='meta'>{escape(str(item['company']))} · {_display_label(str(item['bucket']))} · {lens.title} {score}</p>"
            f"<p class='muted'>{escape(' | '.join(extra) or 'Movement item')}</p>"
            f"<p><a href='{escape(str(item['detail_url']))}'>Open lead</a></p>"
            "</article>"
        )
    return "<div class='mini-grid'>" + "".join(cards) + "</div>"


def _movement_employer_list(items: list[dict[str, object]]) -> str:
    if not items:
        return "<p class='empty'>No employer movement in this slice.</p>"
    cards = []
    for item in items[:6]:
        cards.append(
            "<article class='mini-card'>"
            f"<p class='mini-kicker'>{escape(', '.join(_track_label(track) for track in item.get('tracks', [])))}</p>"
            f"<h4>{escape(str(item['employer_name']))}</h4>"
            f"<p class='meta'>Active leads {item['previous_active_count']} → {item['current_active_count']} · delta {item['delta']}</p>"
            f"<p class='muted'>{escape(', '.join(str(tag) for tag in item.get('employer_tags', [])) or 'Employer activity increased')}</p>"
            "</article>"
        )
    return "<div class='mini-grid'>" + "".join(cards) + "</div>"


def render_index(
    generated_at: str,
    jobs: list[OpportunityLead],
    signals: list[OpportunityLead],
    reports: list[SourceReport],
    summary: dict[str, object],
    employers: list[EmployerRollup],
    movement: dict[str, object],
    curation_summary: dict[str, object],
    profile_preferences: dict[str, object],
) -> str:
    active_leads = [lead for lead in [*jobs, *signals] if lead.status == "active" and is_kept_lead(lead) and not lead.dismissed]
    active_jobs = [lead for lead in jobs if lead.status == "active" and is_kept_lead(lead) and not lead.dismissed]
    saved_leads = [lead for lead in active_leads if lead.saved]
    starred_employers = [employer for employer in employers if employer.manual_starred]

    top_picks = [lead for lead in sorted_leads_for_profile(active_jobs, "oregon_now")][:8]
    bridge_bets = [
        lead
        for lead in sorted_leads_for_profile(active_jobs, "bridge_to_informatics")
        if lead.rn_leverage_type in {"implementation", "informatics", "research", "product_clinical", "clinical_success"}
        or lead.bucket in {"bridge", "target", "strategic_watch"}
    ][:8]
    frontier_bets = [
        lead
        for lead in sorted_leads_for_profile(active_jobs, "frontier_transition")
        if lead.track == "frontier_ecosystem"
    ][:8]
    employers_to_watch = employers[:8]

    movement_summary = movement.get("summary", {})
    stats = [
        ("Top Picks", len(top_picks)),
        ("Bridge Bets", len(bridge_bets)),
        ("Frontier Bets", len(frontier_bets)),
        ("Employers To Watch", len(employers_to_watch)),
        ("Starred Leads", curation_summary.get("starred_leads", 0)),
        ("Saved Leads", curation_summary.get("saved_leads", 0)),
        ("Starred Employers", curation_summary.get("starred_employers", 0)),
    ]
    summary_cards = "".join(
        f"<div class='summary-card'><span>{escape(label)}</span><strong>{value}</strong></div>"
        for label, value in stats
    )

    lens_cards = "".join(
        (
            "<div class='lens-card'>"
            f"<strong>{escape(lens.title)}</strong>"
            f"<p>{escape(lens.description)}</p>"
            "</div>"
        )
        for lens in PROFILE_LENSES.values()
    )
    active_profile = str(profile_preferences.get("active_profile", "oregon_now"))
    default_preferences = profile_preferences.get("default_preferences", {})
    preferred_geo_scopes = ""
    if isinstance(default_preferences, dict):
        geo_values = default_preferences.get("preferred_geo_scopes", [])
        if isinstance(geo_values, list):
            preferred_geo_scopes = ", ".join(_display_label(str(item)) for item in geo_values)
    report_rows = "".join(_render_report_row(report) for report in reports)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RN Opportunity Radar</title>
  <meta name="description" content="Career navigation dashboard for Oregon RN leads and frontier nursing-tech transition work.">
  <link rel="icon" href="{_favicon_data_uri()}">
  <style>
    :root {{
      --bg: #f7f9fe;
      --panel: rgba(255, 255, 255, 0.88);
      --ink: #14324c;
      --muted: #5a7189;
      --line: rgba(19, 51, 79, 0.12);
      --navy: #17334f;
      --teal: #2a6f7d;
      --coral: #f37d67;
      --gold: #d4a85f;
      --shadow: 0 18px 48px rgba(23, 51, 79, 0.08);
      --radius: 24px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(42, 111, 125, 0.12), transparent 32rem),
        radial-gradient(circle at top right, rgba(243, 125, 103, 0.12), transparent 26rem),
        linear-gradient(180deg, #fbfcff 0%, var(--bg) 48%, #eef4fb 100%);
      font-family: "Manrope", "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    a {{ color: var(--navy); }}
    .shell {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 72px;
    }}
    .hero {{
      padding: 28px;
      border: 1px solid var(--line);
      border-radius: calc(var(--radius) + 8px);
      background: linear-gradient(145deg, rgba(255,255,255,0.92), rgba(240,246,252,0.88));
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      margin: 0 0 8px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.75rem;
      color: var(--teal);
      font-weight: 700;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2.1rem, 5vw, 4rem);
      line-height: 0.98;
      max-width: 13ch;
    }}
    .hero-copy {{
      max-width: 78ch;
      color: var(--muted);
      margin: 16px 0 16px;
    }}
    .hero-meta {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .jump-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    .jump-nav a {{
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(23, 51, 79, 0.06);
      border: 1px solid rgba(23, 51, 79, 0.08);
      color: var(--ink);
      font-weight: 700;
    }}
    .summary-grid, .lens-grid, .movement-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .summary-card, .lens-card, .card, .report-panel, .mini-card {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      backdrop-filter: blur(12px);
      box-shadow: var(--shadow);
    }}
    .summary-card, .lens-card {{
      padding: 16px 18px;
    }}
    .summary-card span {{
      display: block;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .summary-card strong {{
      display: block;
      margin-top: 4px;
      font-size: 1.8rem;
      line-height: 1;
    }}
    .lens-card strong {{
      display: block;
      font-size: 1rem;
    }}
    .lens-card p {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .section {{
      margin-top: 32px;
    }}
    .section-head {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 1.8rem;
    }}
    .section-copy {{
      margin: 6px 0 0;
      color: var(--muted);
      max-width: 68ch;
    }}
    .section-count {{
      font-size: 2.2rem;
      font-weight: 800;
      color: rgba(23, 51, 79, 0.22);
    }}
    .card-grid, .mini-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 16px;
    }}
    .mini-grid {{
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    }}
    .card, .mini-card {{
      padding: 18px;
    }}
    .core-card {{
      border-left: 4px solid rgba(42, 111, 125, 0.18);
    }}
    .frontier-card {{
      border-left: 4px solid rgba(243, 125, 103, 0.2);
    }}
    .employer-card {{
      border-left: 4px solid rgba(212, 168, 95, 0.24);
    }}
    .card-top {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .card h3, .mini-card h4 {{
      margin: 0 0 10px;
      font-size: 1.15rem;
      line-height: 1.25;
    }}
    .mini-card h4 {{
      font-size: 1rem;
    }}
    .meta-row, .profile-row, .source-row, .lens-row, .manual-note, .reason-title {{
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 0.77rem;
      font-weight: 800;
      letter-spacing: 0.02em;
    }}
    .badge-priority {{ background: rgba(243, 125, 103, 0.14); color: #9e3f2e; }}
    .badge-bridge {{ background: rgba(42, 111, 125, 0.14); color: #1f626f; }}
    .badge-watch, .badge-strategic_watch {{ background: rgba(212, 168, 95, 0.18); color: #8f6426; }}
    .badge-long_shot, .badge-low_fit {{ background: rgba(90, 113, 137, 0.14); color: #506476; }}
    .badge-target {{ background: rgba(19, 51, 79, 0.14); color: #17334f; }}
    .badge-ecosystem_signal, .badge-signal {{ background: rgba(243, 125, 103, 0.12); color: #9e3f2e; }}
    .badge-saved {{ background: rgba(42, 111, 125, 0.18); color: #1f626f; }}
    .badge-starred {{ background: rgba(212, 168, 95, 0.22); color: #8f6426; }}
    .badge-dismissed {{ background: rgba(120, 69, 69, 0.12); color: #8f4d4d; }}
    .badge-tag {{ background: rgba(23, 51, 79, 0.06); color: var(--ink); }}
    .badge-expired {{ background: rgba(120, 69, 69, 0.12); color: #8f4d4d; }}
    .badge-stale {{ background: rgba(103, 86, 33, 0.16); color: #7a6222; }}
    .badge-ok {{ background: rgba(42, 111, 125, 0.14); color: #1f626f; }}
    .badge-core-track {{ background: rgba(42, 111, 125, 0.12); color: #1f626f; }}
    .badge-frontier-track {{ background: rgba(243, 125, 103, 0.12); color: #9e3f2e; }}
    .reason-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--ink);
    }}
    .reason-list li + li {{
      margin-top: 6px;
    }}
    .card-actions {{
      margin: 14px 0 0;
      font-weight: 800;
    }}
    .mini-kicker {{
      margin: 0 0 8px;
      color: var(--teal);
      text-transform: uppercase;
      font-size: 0.75rem;
      font-weight: 800;
      letter-spacing: 0.08em;
    }}
    .report-panel {{
      padding: 12px;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .muted, .empty {{
      color: var(--muted);
    }}
    .preference-boost {{
      color: #1f626f;
    }}
    .preference-downweight {{
      color: #8f4d4d;
    }}
    @media (max-width: 720px) {{
      .shell {{ padding: 18px 14px 56px; }}
      .hero {{ padding: 20px; }}
      .section-head {{ flex-direction: column; align-items: start; }}
      .section-count {{ font-size: 1.6rem; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">Career navigation dashboard</p>
      <h1>Use the radar to decide what to pursue now, what to save, and what to work toward next.</h1>
      <p class="hero-copy">
        The base radar still collects and scores leads across the core Oregon RN rail and the frontier ecosystem rail.
        This layer adds decision support on top: profile lenses, employer dossiers, manual curation, and movement tracking.
      </p>
      <p class="hero-meta">Generated {_format_datetime_label(generated_at)} · Active profile {escape(_display_label(active_profile))} · Preferred geos {escape(preferred_geo_scopes or 'Not set')} · Sources healthy {summary.get('healthy_source_count', 0)} · failed {summary.get('failed_source_count', 0)}</p>
      <nav class="jump-nav" aria-label="Section jumps">
        <a href="#top-picks-right-now">Top Picks Right Now</a>
        <a href="#best-bridge-bets">Best Bridge Bets</a>
        <a href="#frontier-bets">Frontier Bets</a>
        <a href="#employers-to-watch">Employers To Watch</a>
        <a href="#what-changed-since-last-run">What Changed Since Last Run</a>
        <a href="#saved-leads-starred-employers">Saved Leads / Starred Employers</a>
        <a href="#source-health">Source Health</a>
        <a href="score-audit.html">Score Audit</a>
      </nav>
      <div class="summary-grid">{summary_cards}</div>
      <div class="lens-grid">{lens_cards}</div>
    </section>

    {_section_title("Top Picks Right Now", len(top_picks), "top-picks-right-now", "This uses the Oregon Now lens to surface the most realistic near-term work without changing the underlying base score.")}
      {_render_card_grid(top_picks, "No top picks are available in this run.", profile_name="oregon_now")}
    </section>

    {_section_title("Best Bridge Bets", len(bridge_bets), "best-bridge-bets", "These are the strongest implementation, informatics, workflow, quality, and clinical-systems bets under the Bridge To Informatics lens.")}
      {_render_card_grid(bridge_bets, "No bridge bets are available in this run.", profile_name="bridge_to_informatics")}
    </section>

    {_section_title("Frontier Bets", len(frontier_bets), "frontier-bets", "These are the strongest RN-leveraged vendor and frontier roles under the Frontier Transition lens.")}
      {_render_card_grid(frontier_bets, "No frontier bets are available in this run.", profile_name="frontier_transition")}
    </section>

    {_section_title("Employers To Watch", len(employers_to_watch), "employers-to-watch", "Employer dossiers combine both rails so you can see which organizations matter most, where they show up, and why they are worth attention.")}
      {_render_employer_grid(employers_to_watch, "No employer rollups are available in this run.")}
    </section>

    {_section_title("What Changed Since Last Run", int(movement_summary.get('new_high_signal_count', 0)) + int(movement_summary.get('promoted_count', 0)), "what-changed-since-last-run", "Daily movement tracking highlights new high-signal leads, promotions, frontier additions, vanishing leads, and employers with growing activity.")}
      <div class="movement-summary">
        <div class="summary-card"><span>New high-signal leads</span><strong>{movement_summary.get('new_high_signal_count', 0)}</strong></div>
        <div class="summary-card"><span>Promoted leads</span><strong>{movement_summary.get('promoted_count', 0)}</strong></div>
        <div class="summary-card"><span>Vanished leads</span><strong>{movement_summary.get('vanished_count', 0)}</strong></div>
        <div class="summary-card"><span>Growing employers</span><strong>{movement_summary.get('growing_employer_count', 0)}</strong></div>
        <div class="summary-card"><span>New frontier targets</span><strong>{movement_summary.get('new_frontier_target_count', 0)}</strong></div>
        <div class="summary-card"><span>Saved lead changes</span><strong>{movement_summary.get('saved_lead_change_count', 0)}</strong></div>
      </div>
      <h3 class="eyebrow" style="margin-top:20px;">New high-signal leads</h3>
      {_movement_lead_list(movement.get('new_high_signal_leads', []), 'oregon_now')}
      <h3 class="eyebrow" style="margin-top:20px;">Promoted leads</h3>
      {_movement_lead_list(movement.get('promoted_leads', []), 'bridge_to_informatics')}
      <h3 class="eyebrow" style="margin-top:20px;">New frontier targets</h3>
      {_movement_lead_list(movement.get('new_frontier_targets', []), 'frontier_transition')}
      <h3 class="eyebrow" style="margin-top:20px;">Employers with increasing activity</h3>
      {_movement_employer_list(movement.get('employers_with_increasing_activity', []))}
      <h3 class="eyebrow" style="margin-top:20px;">Recently vanished</h3>
      {_movement_lead_list(movement.get('vanished_leads', []), 'oregon_now')}
    </section>

    {_section_title("Saved Leads / Starred Employers", len(saved_leads) + len(starred_employers), "saved-leads-starred-employers", "Manual curation keeps the tool personal: save leads, hide noise, and mark employers that matter to you.")}
      <h3 class="eyebrow" style="margin-top:0;">Saved leads</h3>
      {_render_card_grid(saved_leads[:8], "No saved leads yet. Add them in data/manual/saved_leads.json.")}
      <h3 class="eyebrow" style="margin-top:20px;">Starred employers</h3>
      {_render_employer_grid(starred_employers[:6], "No starred employers yet. Add them in data/manual/employer_notes.json.")}
    </section>

    {_section_title("Source Health", len(reports), "source-health", "Per-source health and volume stay visible so the navigation layer remains grounded in honest collection quality.")}
      <div class="report-panel">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Track</th>
              <th>Status</th>
              <th>Fetched</th>
              <th>Relevant</th>
              <th>Browser</th>
              <th>Notes / Errors</th>
            </tr>
          </thead>
          <tbody>
            {report_rows}
          </tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>"""


def render_latest_json(
    generated_at: str,
    jobs: list[OpportunityLead],
    signals: list[OpportunityLead],
    reports: list[SourceReport],
    summary: dict[str, object],
    employers: list[EmployerRollup],
    movement: dict[str, object],
    curation_summary: dict[str, object],
    profile_preferences: dict[str, object],
) -> str:
    payload = {
        "generated_at": generated_at,
        "summary": summary,
        "jobs": [lead.to_dict() for lead in jobs],
        "signals": [lead.to_dict() for lead in signals],
        "reports": [report.to_dict() for report in reports],
        "employers": [employer.to_dict() for employer in employers],
        "movement": movement,
        "curation_summary": curation_summary,
        "profiles": [
            {
                "name": lens.name,
                "title": lens.title,
                "description": lens.description,
            }
            for lens in PROFILE_LENSES.values()
        ],
        "profile_preferences": profile_preferences,
    }
    return json.dumps(payload, indent=2)


def render_feed_xml(
    generated_at: str,
    jobs: list[OpportunityLead],
    signals: list[OpportunityLead],
) -> str:
    del signals
    active_items = [
        lead
        for lead in jobs
        if lead.status == "active"
        and is_kept_lead(lead)
        and not lead.dismissed
        and (
            lead.bucket in {"priority", "bridge", "target", "strategic_watch"}
            or lead.saved
        )
    ]
    items = sorted(
        active_items,
        key=lambda lead: (
            -lead.profile_scores.get("oregon_now", lead.score),
            -lead.profile_scores.get("frontier_transition", lead.score),
            -lead.score,
            lead.company.lower(),
            lead.title.lower(),
        ),
    )[:30]

    try:
        parsed_generated = datetime.fromisoformat(generated_at)
    except ValueError:
        parsed_generated = datetime.utcnow()

    item_xml = []
    for lead in items:
        try:
            published = datetime.fromisoformat(lead.posted_date) if lead.posted_date else parsed_generated
        except ValueError:
            published = parsed_generated
        description = escape(
            f"{_track_label(lead.track)} · {_display_label(lead.bucket)} · "
            f"Oregon Now {lead.profile_scores.get('oregon_now', lead.score)} · "
            f"Frontier Transition {lead.profile_scores.get('frontier_transition', lead.score)}"
        )
        item_xml.append(
            "<item>"
            f"<title>{escape(f'[{_track_label(lead.track)}] {lead.title} ({lead.company})')}</title>"
            f"<link>{escape(lead.detail_url)}</link>"
            f"<guid>{escape(lead.lead_key)}</guid>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<description>{description}</description>"
            "</item>"
        )

    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<rss version='2.0'><channel>"
        "<title>RN Opportunity Radar</title>"
        "<link>https://example.com/</link>"
        "<description>Decision-support feed for Oregon RN and frontier nursing-tech opportunities.</description>"
        f"<lastBuildDate>{format_datetime(parsed_generated)}</lastBuildDate>"
        f"{''.join(item_xml)}"
        "</channel></rss>"
    )
