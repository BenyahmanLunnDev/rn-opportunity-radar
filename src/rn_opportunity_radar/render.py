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


def _render_detail_list(items: list[tuple[str, str]], class_name: str = "detail-list") -> str:
    return (
        f"<dl class='{escape(class_name)}'>"
        + "".join(
            "<div>"
            f"<dt>{escape(label)}</dt>"
            f"<dd>{escape(value)}</dd>"
            "</div>"
            for label, value in items
        )
        + "</dl>"
    )


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
        ("Employer", lead.company),
        ("Location", lead.location or "Not listed"),
        ("Posted", _format_date_label(lead.posted_date)),
        ("Base Score", str(lead.score)),
    ]
    return _render_detail_list(meta_bits)


def _render_profile_row(lead: OpportunityLead) -> str:
    bits = [
        ("Horizon", _display_label(lead.horizon)),
        ("Geo Scope", _display_label(lead.geo_scope)),
        ("RN Leverage", _display_label(lead.rn_leverage_type)),
        ("Relocation", _display_label(lead.relocation_risk)),
    ]
    return _render_detail_list(bits, "detail-list detail-list-secondary")


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
        f"{_render_detail_list([('Tracks', ', '.join(_track_label(track) for track in employer.tracks) or 'None'), ('Employer Score', str(employer.employer_score))])}"
        f"{_render_detail_list([('Locations', ', '.join(employer.locations_seen[:4]) or 'Not listed'), ('Leverage', ', '.join(_display_label(item) for item in employer.rn_leverage_types[:3]) or 'Mixed')], 'detail-list detail-list-secondary')}"
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
    core_active_count = sum(1 for lead in active_leads if lead.track == "core_rn_oregon")
    core_priority_count = sum(
        1 for lead in active_leads if lead.track == "core_rn_oregon" and lead.bucket == "priority"
    )
    core_bridge_count = sum(1 for lead in active_leads if lead.track == "core_rn_oregon" and lead.bucket == "bridge")
    frontier_active_count = sum(1 for lead in active_leads if lead.track == "frontier_ecosystem")
    frontier_target_count = sum(
        1 for lead in active_leads if lead.track == "frontier_ecosystem" and lead.bucket == "target"
    )
    frontier_watch_count = sum(
        1
        for lead in active_leads
        if lead.track == "frontier_ecosystem" and lead.bucket in {"strategic_watch", "ecosystem_signal"}
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RN Opportunity Radar</title>
  <meta name="description" content="Career navigation dashboard for Oregon RN leads and frontier nursing-tech transition work.">
  <link rel="icon" href="{_favicon_data_uri()}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Manrope:wght@500;700;800&family=Public+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --paper: #f7f3ea;
      --paper-strong: #efe7da;
      --paper-soft: #fcfaf6;
      --panel: rgba(255, 255, 255, 0.82);
      --panel-strong: rgba(255, 255, 255, 0.92);
      --ink: #1f2928;
      --muted: #61716c;
      --line: rgba(27, 48, 34, 0.12);
      --pine: #1f4f46;
      --pine-deep: #173932;
      --sage: #7f9f90;
      --slate: #4f6270;
      --mist: #dce7ea;
      --sand: #dcc9b2;
      --clay: #b66553;
      --gold: #b08a52;
      --shadow: 0 24px 60px rgba(20, 36, 30, 0.08);
      --soft-shadow: 0 10px 28px rgba(20, 36, 30, 0.05);
      --radius: 28px;
      --radius-lg: 38px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(127, 159, 144, 0.18), transparent 28rem),
        radial-gradient(circle at top right, rgba(79, 98, 112, 0.18), transparent 24rem),
        linear-gradient(180deg, #fdfaf6 0%, var(--paper) 36%, #f3ece1 100%);
      font-family: "Public Sans", "Segoe UI", sans-serif;
      line-height: 1.55;
    }}
    a {{
      color: var(--pine);
      text-underline-offset: 0.14em;
    }}
    .shell {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 18px 72px;
    }}
    .hero {{
      position: relative;
      overflow: hidden;
      padding: 34px;
      border-radius: var(--radius-lg);
      background:
        radial-gradient(circle at 86% 10%, rgba(127, 159, 144, 0.18), transparent 18rem),
        radial-gradient(circle at 100% 0%, rgba(79, 98, 112, 0.12), transparent 16rem),
        linear-gradient(145deg, rgba(255,255,255,0.88), rgba(245, 239, 229, 0.78));
      box-shadow: var(--shadow);
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: 18px;
      border-radius: calc(var(--radius-lg) - 14px);
      border: 1px solid rgba(255, 255, 255, 0.45);
      pointer-events: none;
    }}
    .hero-layout {{
      position: relative;
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.92fr);
      gap: 22px;
      align-items: stretch;
    }}
    .hero-copy-wrap,
    .hero-panel-stack {{
      position: relative;
      z-index: 1;
    }}
    .eyebrow {{
      margin: 0 0 8px;
      text-transform: uppercase;
      letter-spacing: 0.16em;
      font-size: 0.72rem;
      color: var(--pine);
      font-weight: 800;
    }}
    h1 {{
      margin: 0;
      max-width: 11ch;
      font-family: "Fraunces", Georgia, serif;
      font-size: clamp(2.7rem, 5vw, 4.9rem);
      font-weight: 700;
      line-height: 0.96;
      letter-spacing: -0.04em;
    }}
    .hero-copy {{
      max-width: 64ch;
      color: var(--muted);
      margin: 18px 0 0;
      font-size: 1.03rem;
    }}
    .hero-meta-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 22px 0 0;
    }}
    .hero-meta-list span {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 9px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.68);
      color: var(--muted);
      font-size: 0.9rem;
      box-shadow: inset 0 0 0 1px rgba(27, 48, 34, 0.06);
    }}
    .hero-panel-stack {{
      display: grid;
      gap: 14px;
    }}
    .hero-panel {{
      padding: 18px 20px;
      border-radius: calc(var(--radius) - 6px);
      background: rgba(255, 255, 255, 0.78);
      backdrop-filter: blur(14px);
      box-shadow: var(--soft-shadow);
    }}
    .panel-label {{
      margin: 0;
      color: var(--pine);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.74rem;
      font-weight: 800;
    }}
    .panel-value {{
      margin: 10px 0 0;
      font-family: "Fraunces", Georgia, serif;
      font-size: 1.65rem;
      line-height: 1.08;
      letter-spacing: -0.03em;
    }}
    .panel-copy {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .rail-snapshot-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .rail-snapshot {{
      padding: 18px;
      border-radius: calc(var(--radius) - 10px);
      min-height: 172px;
      box-shadow: var(--soft-shadow);
    }}
    .rail-snapshot.core {{
      background: linear-gradient(180deg, rgba(231, 242, 237, 0.95), rgba(255, 255, 255, 0.82));
    }}
    .rail-snapshot.frontier {{
      background: linear-gradient(180deg, rgba(229, 235, 239, 0.95), rgba(255, 255, 255, 0.82));
    }}
    .rail-snapshot strong {{
      display: block;
      margin-top: 12px;
      font-family: "Manrope", "Public Sans", sans-serif;
      font-size: 2.1rem;
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .rail-snapshot p {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.93rem;
    }}
    .jump-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 24px;
    }}
    .jump-nav a {{
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.72);
      box-shadow: inset 0 0 0 1px rgba(27, 48, 34, 0.07);
      color: var(--ink);
      font-weight: 700;
      transition: transform 160ms ease, background 160ms ease, color 160ms ease;
    }}
    .jump-nav a:hover {{
      transform: translateY(-1px);
      background: rgba(31, 79, 70, 0.12);
      color: var(--pine-deep);
    }}
    .summary-grid, .lens-grid, .movement-summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 14px;
      margin-top: 24px;
    }}
    .summary-card, .lens-card, .card, .report-panel, .mini-card {{
      border-radius: var(--radius);
      background: var(--panel);
      backdrop-filter: blur(14px);
      box-shadow: var(--soft-shadow);
    }}
    .summary-card, .lens-card {{
      padding: 18px 18px 16px;
    }}
    .summary-card span {{
      display: block;
      color: var(--muted);
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }}
    .summary-card strong {{
      display: block;
      margin-top: 10px;
      font-family: "Manrope", "Public Sans", sans-serif;
      font-size: 2rem;
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .lens-card strong {{
      display: block;
      font-size: 1rem;
      font-family: "Manrope", "Public Sans", sans-serif;
    }}
    .lens-card p {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .rail-band {{
      margin-top: 28px;
      padding: 22px;
      border-radius: var(--radius-lg);
      box-shadow: var(--soft-shadow);
    }}
    .rail-band-core {{
      background: linear-gradient(180deg, rgba(239, 246, 242, 0.78), rgba(255, 255, 255, 0.6));
    }}
    .rail-band-frontier {{
      background: linear-gradient(180deg, rgba(233, 238, 242, 0.84), rgba(255, 255, 255, 0.62));
    }}
    .rail-band-neutral {{
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(247, 243, 234, 0.76));
    }}
    .rail-intro {{
      display: grid;
      gap: 10px;
      margin-bottom: 12px;
    }}
    .rail-intro h2 {{
      margin: 0;
      font-family: "Fraunces", Georgia, serif;
      font-size: clamp(1.9rem, 3vw, 2.9rem);
      line-height: 1.03;
      letter-spacing: -0.03em;
    }}
    .rail-intro-copy {{
      margin: 0;
      color: var(--muted);
      max-width: 70ch;
    }}
    .section {{
      margin-top: 28px;
    }}
    .section-head {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .section-head h2 {{
      margin: 0;
      font-family: "Fraunces", Georgia, serif;
      font-size: clamp(1.55rem, 2vw, 2.15rem);
      line-height: 1.08;
      letter-spacing: -0.03em;
    }}
    .section-copy {{
      margin: 6px 0 0;
      color: var(--muted);
      max-width: 68ch;
    }}
    .section-count {{
      font-family: "Manrope", "Public Sans", sans-serif;
      font-size: 2.4rem;
      font-weight: 800;
      color: rgba(31, 79, 70, 0.18);
      letter-spacing: -0.05em;
    }}
    .card-grid, .mini-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}
    .mini-grid {{
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    }}
    .card, .mini-card {{
      position: relative;
      overflow: hidden;
      padding: 20px;
    }}
    .card::before, .mini-card::before {{
      content: "";
      position: absolute;
      inset: 0 0 auto 0;
      height: 4px;
    }}
    .core-card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(247, 252, 249, 0.84));
    }}
    .core-card::before {{
      background: linear-gradient(90deg, rgba(31, 79, 70, 0.9), rgba(127, 159, 144, 0.28));
    }}
    .frontier-card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(245, 248, 250, 0.86));
    }}
    .frontier-card::before {{
      background: linear-gradient(90deg, rgba(79, 98, 112, 0.9), rgba(176, 138, 82, 0.28));
    }}
    .employer-card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(250, 247, 241, 0.86));
    }}
    .employer-card::before {{
      background: linear-gradient(90deg, rgba(176, 138, 82, 0.9), rgba(127, 159, 144, 0.24));
    }}
    .card-top {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .card h3, .mini-card h4 {{
      margin: 0 0 10px;
      font-family: "Manrope", "Public Sans", sans-serif;
      font-size: 1.16rem;
      line-height: 1.28;
      letter-spacing: -0.025em;
    }}
    .mini-card h4 {{
      font-size: 1rem;
    }}
    .source-row, .lens-row, .manual-note, .reason-title {{
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .detail-list {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 12px;
      margin: 0 0 12px;
    }}
    .detail-list div {{
      padding: 10px 12px;
      border-radius: 16px;
      background: rgba(247, 243, 234, 0.64);
    }}
    .detail-list-secondary div {{
      background: rgba(239, 246, 242, 0.54);
    }}
    .detail-list dt {{
      margin: 0;
      color: var(--muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      font-weight: 700;
    }}
    .detail-list dd {{
      margin: 8px 0 0;
      color: var(--ink);
      font-size: 0.94rem;
      font-weight: 600;
      line-height: 1.35;
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
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .badge-priority {{ background: rgba(182, 101, 83, 0.14); color: #934d3f; }}
    .badge-bridge {{ background: rgba(31, 79, 70, 0.14); color: var(--pine); }}
    .badge-watch, .badge-strategic_watch {{ background: rgba(176, 138, 82, 0.18); color: #83633a; }}
    .badge-long_shot, .badge-low_fit {{ background: rgba(79, 98, 112, 0.14); color: var(--slate); }}
    .badge-target {{ background: rgba(79, 98, 112, 0.14); color: var(--slate); }}
    .badge-ecosystem_signal, .badge-signal {{ background: rgba(127, 159, 144, 0.18); color: #4c685c; }}
    .badge-saved {{ background: rgba(31, 79, 70, 0.16); color: var(--pine); }}
    .badge-starred {{ background: rgba(176, 138, 82, 0.2); color: #7d6037; }}
    .badge-dismissed {{ background: rgba(128, 86, 74, 0.12); color: #85564a; }}
    .badge-tag {{ background: rgba(31, 79, 70, 0.08); color: var(--ink); }}
    .badge-expired {{ background: rgba(128, 86, 74, 0.12); color: #85564a; }}
    .badge-stale {{ background: rgba(176, 138, 82, 0.16); color: #83633a; }}
    .badge-ok {{ background: rgba(31, 79, 70, 0.14); color: var(--pine); }}
    .badge-core-track {{ background: rgba(31, 79, 70, 0.1); color: var(--pine); }}
    .badge-frontier-track {{ background: rgba(79, 98, 112, 0.1); color: var(--slate); }}
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
    .card-actions a {{
      text-decoration: none;
    }}
    .mini-kicker {{
      margin: 0 0 8px;
      color: var(--pine);
      text-transform: uppercase;
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0.12em;
    }}
    .subsection-kicker {{
      margin: 24px 0 12px;
      color: var(--pine);
      text-transform: uppercase;
      font-size: 0.74rem;
      font-weight: 800;
      letter-spacing: 0.14em;
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
      color: var(--pine);
    }}
    .preference-downweight {{
      color: #85564a;
    }}
    @media (max-width: 980px) {{
      .hero-layout {{
        grid-template-columns: 1fr;
      }}
      .rail-snapshot-grid {{
        grid-template-columns: 1fr 1fr;
      }}
    }}
    @media (max-width: 720px) {{
      .shell {{ padding: 14px 12px 52px; }}
      .hero, .rail-band {{ padding: 18px; }}
      h1 {{ font-size: clamp(2.25rem, 12vw, 3.2rem); }}
      .hero-meta-list {{
        gap: 8px;
      }}
      .summary-grid, .lens-grid, .movement-summary, .card-grid, .mini-grid, .rail-snapshot-grid {{
        grid-template-columns: 1fr;
      }}
      .detail-list {{
        grid-template-columns: 1fr;
      }}
      .section-head {{
        flex-direction: column;
        align-items: start;
      }}
      .section-count {{
        font-size: 1.8rem;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="hero-layout">
        <div class="hero-copy-wrap">
          <p class="eyebrow">Career navigation dashboard</p>
          <h1>Oregon-now nursing decisions on one rail. Frontier transition bets on the other.</h1>
          <p class="hero-copy">
            RN Opportunity Radar is no longer just a collector. It separates practical Oregon RN moves from longer-horizon nursing-tech movement,
            then layers employer dossiers, manual curation, and profile lenses on top so the next step is easier to see.
          </p>
          <div class="hero-meta-list">
            <span>Generated {_format_datetime_label(generated_at)}</span>
            <span>Active profile {escape(_display_label(active_profile))}</span>
            <span>Preferred geos {escape(preferred_geo_scopes or 'Not set')}</span>
            <span>Sources healthy {summary.get('healthy_source_count', 0)}</span>
            <span>Failed {summary.get('failed_source_count', 0)}</span>
          </div>
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
        </div>
        <aside class="hero-panel-stack">
          <div class="hero-panel">
            <p class="panel-label">Current posture</p>
            <p class="panel-value">Practical Oregon fit first. Frontier leverage second.</p>
            <p class="panel-copy">
              The dashboard is tuned for realistic RN options while reinstatement is in focus, without losing sight of implementation, informatics,
              clinical-success, and product-clinical paths that matter next.
            </p>
          </div>
          <div class="rail-snapshot-grid">
            <div class="rail-snapshot core">
              <p class="panel-label">Core Oregon RN</p>
              <strong>{core_active_count}</strong>
              <p>{core_priority_count} priority leads and {core_bridge_count} bridge leads remain in the practical rail.</p>
            </div>
            <div class="rail-snapshot frontier">
              <p class="panel-label">Frontier Ecosystem</p>
              <strong>{frontier_active_count}</strong>
              <p>{frontier_target_count} target roles and {frontier_watch_count} strategic watch or ecosystem signals are separated here.</p>
            </div>
          </div>
        </aside>
      </div>
      <div class="summary-grid">{summary_cards}</div>
      <div class="lens-grid">{lens_cards}</div>
    </section>

    <div class="rail-band rail-band-core">
      <div class="rail-intro">
        <p class="eyebrow">Core rail</p>
        <h2>Core Oregon RN Opportunities</h2>
        <p class="rail-intro-copy">
          This is the practical rail: local RN work, ICU-adjacent roles, care coordination, and bridge positions that are actionable without letting the frontier track blur the near-term picture.
        </p>
      </div>

      {_section_title("Top Picks Right Now", len(top_picks), "top-picks-right-now", "This uses the Oregon Now lens to surface the most realistic near-term work without changing the underlying base score.")}
        {_render_card_grid(top_picks, "No top picks are available in this run.", profile_name="oregon_now")}
      </section>

      {_section_title("Best Bridge Bets", len(bridge_bets), "best-bridge-bets", "These are the strongest implementation, informatics, workflow, quality, and clinical-systems bets under the Bridge To Informatics lens.")}
        {_render_card_grid(bridge_bets, "No bridge bets are available in this run.", profile_name="bridge_to_informatics")}
      </section>
    </div>

    <div class="rail-band rail-band-frontier">
      <div class="rail-intro">
        <p class="eyebrow">Frontier rail</p>
        <h2>Frontier Transition And Employer Strategy</h2>
        <p class="rail-intro-copy">
          This rail stays intentionally separate so remote-friendly clinical-success, implementation, product-clinical, and ecosystem signals can guide long-horizon movement without contaminating Oregon-now decisions.
        </p>
      </div>

      {_section_title("Frontier Bets", len(frontier_bets), "frontier-bets", "These are the strongest RN-leveraged vendor and frontier roles under the Frontier Transition lens.")}
        {_render_card_grid(frontier_bets, "No frontier bets are available in this run.", profile_name="frontier_transition")}
      </section>

      {_section_title("Employers To Watch", len(employers_to_watch), "employers-to-watch", "Employer dossiers combine both rails so you can see which organizations matter most, where they show up, and why they are worth attention.")}
        {_render_employer_grid(employers_to_watch, "No employer rollups are available in this run.")}
      </section>
    </div>

    <div class="rail-band rail-band-neutral">
      {_section_title("What Changed Since Last Run", int(movement_summary.get('new_high_signal_count', 0)) + int(movement_summary.get('promoted_count', 0)), "what-changed-since-last-run", "Daily movement tracking highlights new high-signal leads, promotions, frontier additions, vanishing leads, and employers with growing activity.")}
        <div class="movement-summary">
          <div class="summary-card"><span>New high-signal leads</span><strong>{movement_summary.get('new_high_signal_count', 0)}</strong></div>
          <div class="summary-card"><span>Promoted leads</span><strong>{movement_summary.get('promoted_count', 0)}</strong></div>
          <div class="summary-card"><span>Vanished leads</span><strong>{movement_summary.get('vanished_count', 0)}</strong></div>
          <div class="summary-card"><span>Growing employers</span><strong>{movement_summary.get('growing_employer_count', 0)}</strong></div>
          <div class="summary-card"><span>New frontier targets</span><strong>{movement_summary.get('new_frontier_target_count', 0)}</strong></div>
          <div class="summary-card"><span>Saved lead changes</span><strong>{movement_summary.get('saved_lead_change_count', 0)}</strong></div>
        </div>
        <h3 class="subsection-kicker">New high-signal leads</h3>
        {_movement_lead_list(movement.get('new_high_signal_leads', []), 'oregon_now')}
        <h3 class="subsection-kicker">Promoted leads</h3>
        {_movement_lead_list(movement.get('promoted_leads', []), 'bridge_to_informatics')}
        <h3 class="subsection-kicker">New frontier targets</h3>
        {_movement_lead_list(movement.get('new_frontier_targets', []), 'frontier_transition')}
        <h3 class="subsection-kicker">Employers with increasing activity</h3>
        {_movement_employer_list(movement.get('employers_with_increasing_activity', []))}
        <h3 class="subsection-kicker">Recently vanished</h3>
        {_movement_lead_list(movement.get('vanished_leads', []), 'oregon_now')}
      </section>
    </div>

    <div class="rail-band rail-band-neutral">
      {_section_title("Saved Leads / Starred Employers", len(saved_leads) + len(starred_employers), "saved-leads-starred-employers", "Manual curation keeps the tool personal: save leads, hide noise, and mark employers that matter to you.")}
        <h3 class="subsection-kicker" style="margin-top:0;">Saved leads</h3>
        {_render_card_grid(saved_leads[:8], "No saved leads yet. Add them in data/manual/saved_leads.json.")}
        <h3 class="subsection-kicker">Starred employers</h3>
        {_render_employer_grid(starred_employers[:6], "No starred employers yet. Add them in data/manual/employer_notes.json.")}
      </section>
    </div>

    <div class="rail-band rail-band-neutral">
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
    </div>
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
