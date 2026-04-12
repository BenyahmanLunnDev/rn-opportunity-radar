from __future__ import annotations

import json
from collections import Counter, defaultdict
from html import escape

from rn_opportunity_radar.models import EmployerRollup, OpportunityLead, SourceReport
from rn_opportunity_radar.persistence import is_kept_lead
from rn_opportunity_radar.profiles import PROFILE_LENSES


def _counter_items_html(counter: Counter[str]) -> str:
    if not counter:
        return "<p class='empty'>No counts in this slice.</p>"
    return "<ul>" + "".join(f"<li>{escape(key)}: {value}</li>" for key, value in sorted(counter.items())) + "</ul>"


def _lead_snapshot(lead: OpportunityLead) -> dict[str, object]:
    return {
        "lead_key": lead.lead_key,
        "lead_type": lead.lead_type,
        "track": lead.track,
        "subtrack": lead.subtrack,
        "horizon": lead.horizon,
        "geo_scope": lead.geo_scope,
        "rn_leverage_type": lead.rn_leverage_type,
        "relocation_risk": lead.relocation_risk,
        "bucket": lead.bucket,
        "score": lead.score,
        "title": lead.title,
        "company": lead.company,
        "source_key": lead.source_key,
        "source_name": lead.source_name,
        "location": lead.location,
        "detail_url": lead.detail_url,
        "reasons": lead.reasons,
        "tags": lead.tags,
        "profile_scores": lead.profile_scores,
        "profile_reasons": lead.profile_reasons,
        "profile_preference_deltas": lead.profile_preference_deltas,
        "starred": lead.starred,
        "saved": lead.saved,
        "dismissed": lead.dismissed,
        "pinned_reason": lead.pinned_reason,
        "manual_notes": lead.manual_notes,
        "score_breakdown": lead.score_breakdown,
    }


def _employer_snapshot(employer: EmployerRollup) -> dict[str, object]:
    return {
        "employer_key": employer.employer_key,
        "employer_name": employer.employer_name,
        "tracks": employer.tracks,
        "bucket_counts": employer.bucket_counts,
        "track_counts": employer.track_counts,
        "locations_seen": employer.locations_seen,
        "innovation_signal_presence": employer.innovation_signal_presence,
        "rn_leverage_types": employer.rn_leverage_types,
        "employer_tags": employer.employer_tags,
        "why_it_matters": employer.why_it_matters,
        "profile_highlights": employer.profile_highlights,
        "manual_starred": employer.manual_starred,
        "manual_interest_level": employer.manual_interest_level,
        "pinned_reason": employer.pinned_reason,
        "manual_notes": employer.manual_notes,
        "employer_score": employer.employer_score,
    }


def build_score_audit(
    generated_at: str,
    jobs: list[OpportunityLead],
    signals: list[OpportunityLead],
    reports: list[SourceReport],
    employers: list[EmployerRollup],
    movement: dict[str, object],
    curation_summary: dict[str, object],
) -> dict[str, object]:
    all_leads = [*jobs, *signals]
    active_leads = [lead for lead in all_leads if lead.status == "active"]
    active_kept = [lead for lead in active_leads if is_kept_lead(lead)]
    visible_kept = [lead for lead in active_kept if not lead.dismissed]

    core_kept = [lead for lead in visible_kept if lead.track == "core_rn_oregon"]
    frontier_kept = [lead for lead in visible_kept if lead.track == "frontier_ecosystem"]
    top_priority = [lead for lead in core_kept if lead.bucket == "priority"]
    top_bridge = [lead for lead in core_kept if lead.bucket == "bridge"]
    frontier_targets = [lead for lead in frontier_kept if lead.bucket == "target"]
    frontier_watchlist = [lead for lead in frontier_kept if lead.bucket == "strategic_watch"]
    frontier_signals = [lead for lead in frontier_kept if lead.bucket == "ecosystem_signal"]
    borderline_priority = sorted(
        [lead for lead in core_kept if lead.lead_type == "job" and abs(lead.score - 104) <= 12],
        key=lambda lead: (abs(lead.score - 104), -lead.score, lead.company.lower(), lead.title.lower()),
    )[:25]
    strongest_discards = sorted(
        [lead for lead in active_leads if lead.bucket in {"discard", "low_fit"}],
        key=lambda lead: (lead.score_breakdown.get("penalty_score", 0), lead.score, lead.track, lead.title.lower()),
    )[:25]

    grouped: dict[str, list[OpportunityLead]] = defaultdict(list)
    for lead in all_leads:
        grouped[lead.source_key].append(lead)

    source_rollup: dict[str, dict[str, object]] = {}
    for report in reports:
        leads = grouped.get(report.source_key, [])
        bucket_counts = Counter(lead.bucket for lead in leads if lead.status == "active")
        active_scores = [lead.score for lead in leads if lead.status == "active"]
        source_rollup[report.source_key] = {
            "source_name": report.source_name,
            "track": report.track,
            "source_class": report.source_class,
            "bucket_counts": dict(bucket_counts),
            "average_score": round(sum(active_scores) / len(active_scores), 2) if active_scores else 0,
            "active_kept_count": sum(1 for lead in leads if lead.status == "active" and is_kept_lead(lead)),
            "discarded_count": sum(1 for lead in leads if lead.status == "active" and lead.bucket in {"discard", "low_fit"}),
            "deduped_away_count": report.deduped_away,
        }

    track_counts = Counter(lead.track for lead in visible_kept)
    frontier_bucket_counts = Counter(lead.bucket for lead in frontier_kept)
    subtrack_counts = Counter(lead.subtrack for lead in frontier_kept)
    geo_scope_counts = Counter(lead.geo_scope for lead in frontier_kept)
    leverage_counts = Counter(lead.rn_leverage_type for lead in frontier_kept)
    frontier_remote_vs_relocation = Counter(
        "remote_friendly" if lead.geo_scope == "remote_us" or lead.relocation_risk == "none" else "relocation_likely"
        for lead in frontier_targets
    )
    profile_leaderboards = {
        profile_name: [
            _lead_snapshot(lead)
            for lead in sorted(
                visible_kept,
                key=lambda lead: (
                    -lead.profile_scores.get(profile_name, 0),
                    -lead.score,
                    lead.company.lower(),
                    lead.title.lower(),
                ),
            )[:20]
        ]
        for profile_name in PROFILE_LENSES
    }
    core_employers = [_employer_snapshot(employer) for employer in employers if "core_rn_oregon" in employer.tracks][:12]
    frontier_employers = [_employer_snapshot(employer) for employer in employers if "frontier_ecosystem" in employer.tracks][:12]
    cross_track_employers = [_employer_snapshot(employer) for employer in employers if employer.appears_in_both_tracks][:12]

    return {
        "generated_at": generated_at,
        "summary": {
            "priority_count": len(top_priority),
            "bridge_count": len(top_bridge),
            "target_count": len(frontier_targets),
            "strategic_watch_count": len(frontier_watchlist),
            "ecosystem_signal_count": len(frontier_signals),
            "starred_count": curation_summary.get("starred_leads", 0),
            "dismissed_count": curation_summary.get("dismissed_leads", 0),
            "saved_count": curation_summary.get("saved_leads", 0),
            "pinned_count": curation_summary.get("pinned_leads", 0),
            "starred_employers": curation_summary.get("starred_employers", 0),
            "high_interest_employers": curation_summary.get("high_interest_employers", 0),
        },
        "track_counts": dict(track_counts),
        "frontier_bucket_counts": dict(frontier_bucket_counts),
        "subtrack_counts": dict(subtrack_counts),
        "geo_scope_counts": dict(geo_scope_counts),
        "rn_leverage_type_counts": dict(leverage_counts),
        "frontier_remote_vs_relocation": dict(frontier_remote_vs_relocation),
        "movement_summary": movement.get("summary", {}),
        "top_priority": [_lead_snapshot(lead) for lead in sorted(top_priority, key=lambda lead: (-lead.score, lead.company.lower(), lead.title.lower()))[:25]],
        "top_bridge": [_lead_snapshot(lead) for lead in sorted(top_bridge, key=lambda lead: (-lead.score, lead.company.lower(), lead.title.lower()))[:25]],
        "frontier_targets": [_lead_snapshot(lead) for lead in sorted(frontier_targets, key=lambda lead: (-lead.score, lead.company.lower(), lead.title.lower()))[:25]],
        "frontier_watchlist": [_lead_snapshot(lead) for lead in sorted(frontier_watchlist, key=lambda lead: (-lead.score, lead.company.lower(), lead.title.lower()))[:25]],
        "frontier_signals": [_lead_snapshot(lead) for lead in sorted(frontier_signals, key=lambda lead: (-lead.score, lead.company.lower(), lead.title.lower()))[:25]],
        "borderline_priority": [_lead_snapshot(lead) for lead in borderline_priority],
        "strongest_discards": [_lead_snapshot(lead) for lead in strongest_discards],
        "profile_leaderboards": profile_leaderboards,
        "core_employer_leaderboard": core_employers,
        "frontier_employer_leaderboard": frontier_employers,
        "cross_track_employers": cross_track_employers,
        "bucket_distribution_by_source": source_rollup,
        "movement": movement,
    }


def render_score_audit_html(generated_at: str, audit: dict[str, object]) -> str:
    summary = audit["summary"]
    distributions = audit["bucket_distribution_by_source"]
    movement_summary = audit["movement_summary"]

    def list_html(items: list[dict[str, object]], *, profile_name: str | None = None) -> str:
        if not items:
            return "<p class='empty'>No items in this slice.</p>"

        cards = []
        for item in items:
            breakdown = item["score_breakdown"]
            notes = breakdown.get("bucket_decision_notes", [])
            profile_line = ""
            if profile_name:
                profile_score = item.get("profile_scores", {}).get(profile_name, 0)
                profile_reasons = item.get("profile_reasons", {}).get(profile_name, [])
                preference_delta = item.get("profile_preference_deltas", {}).get(profile_name, 0)
                profile_line = (
                    f"<p><strong>{escape(PROFILE_LENSES[profile_name].title)}:</strong> {profile_score} · "
                    f"manual delta {preference_delta:+d} · "
                    f"{escape('; '.join(str(reason) for reason in profile_reasons) or 'No profile reasons captured.')}</p>"
                )
            cards.append(
                "<article class='card'>"
                f"<p class='card-kicker'>{escape(str(item.get('track', '')))}</p>"
                f"<h3>{escape(str(item['title']))}</h3>"
                f"<p class='meta'>{escape(str(item['company']))} · {escape(str(item['source_name']))} · {escape(str(item['bucket']))} · score {item['score']}</p>"
                f"<p><strong>Subtrack:</strong> {escape(str(item.get('subtrack', '')))} · "
                f"<strong>Horizon:</strong> {escape(str(item.get('horizon', '')))}</p>"
                f"<p><strong>Geo scope:</strong> {escape(str(item.get('geo_scope', '')))} · "
                f"<strong>RN leverage:</strong> {escape(str(item.get('rn_leverage_type', '')))} · "
                f"<strong>Relocation risk:</strong> {escape(str(item.get('relocation_risk', '')))}</p>"
                f"{profile_line}"
                f"<p><strong>Breakdown:</strong> title {breakdown.get('title_score', 0)}, description {breakdown.get('description_score', 0)}, "
                f"rn {breakdown.get('rn_score', 0)}, location {breakdown.get('location_score', 0)}, employer {breakdown.get('employer_score', 0)}, "
                f"icu {breakdown.get('icu_score', 0)}, bridge {breakdown.get('bridge_score', 0)}, signal {breakdown.get('signal_score', 0)}, "
                f"frontier {breakdown.get('frontier_fit_score', 0)}, leverage {breakdown.get('leverage_score', 0)}, scope {breakdown.get('scope_score', 0)}, penalty {breakdown.get('penalty_score', 0)}</p>"
                f"<p><strong>Decision notes:</strong> {escape('; '.join(str(note) for note in notes) or 'None')}</p>"
                f"<p><strong>Starred / saved / dismissed:</strong> {item.get('starred', False)} / {item.get('saved', False)} / {item.get('dismissed', False)}</p>"
                f"<p><a href='{escape(str(item['detail_url']))}'>Open lead</a></p>"
                "</article>"
            )
        return "".join(cards)

    def employer_html(items: list[dict[str, object]]) -> str:
        if not items:
            return "<p class='empty'>No employers in this slice.</p>"
        cards = []
        for item in items:
            cards.append(
                "<article class='card'>"
                f"<p class='card-kicker'>{escape(', '.join(str(track) for track in item.get('tracks', [])))}</p>"
                f"<h3>{escape(str(item['employer_name']))}</h3>"
                f"<p class='meta'>Employer score {item['employer_score']} · Tags {escape(', '.join(str(tag) for tag in item.get('employer_tags', [])) or 'None')}</p>"
                f"<p><strong>Manual interest:</strong> {escape(str(item.get('manual_interest_level', 'none')))} · <strong>Pinned:</strong> {escape(str(item.get('pinned_reason', '')) or 'No')}</p>"
                f"<p><strong>Locations:</strong> {escape(', '.join(str(location) for location in item.get('locations_seen', [])) or 'None')}</p>"
                f"<p><strong>Why it matters:</strong> {escape('; '.join(str(reason) for reason in item.get('why_it_matters', [])) or 'No reasons captured.')}</p>"
                "</article>"
            )
        return "".join(cards)

    distribution_rows = []
    for _, payload in sorted(distributions.items(), key=lambda item: (str(item[1]["track"]), str(item[1]["source_name"]).lower())):
        counts = payload["bucket_counts"]
        distribution_rows.append(
            "<tr>"
            f"<td>{escape(str(payload['source_name']))}</td>"
            f"<td>{escape(str(payload.get('track', 'core_rn_oregon')))}</td>"
            f"<td>{escape(str(payload.get('source_class', 'official')))}</td>"
            f"<td>{payload['average_score']}</td>"
            f"<td>{payload['active_kept_count']}</td>"
            f"<td>{payload.get('discarded_count', 0)}</td>"
            f"<td>{payload.get('deduped_away_count', 0)}</td>"
            f"<td>{counts.get('priority', 0)}</td>"
            f"<td>{counts.get('bridge', 0)}</td>"
            f"<td>{counts.get('watch', 0)}</td>"
            f"<td>{counts.get('long_shot', 0)}</td>"
            f"<td>{counts.get('target', 0)}</td>"
            f"<td>{counts.get('strategic_watch', 0)}</td>"
            f"<td>{counts.get('ecosystem_signal', 0)}</td>"
            f"<td>{counts.get('discard', 0) + counts.get('low_fit', 0)}</td>"
            "</tr>"
        )

    counter_html = "".join(
        f"<article class='counter-card'><h3>{escape(title)}</h3>{_counter_items_html(Counter(items))}</article>"
        for title, items in [
            ("Counts By Track", audit["track_counts"]),
            ("Frontier Buckets", audit["frontier_bucket_counts"]),
            ("Frontier Geo Scope", audit["geo_scope_counts"]),
            ("RN Leverage Types", audit["rn_leverage_type_counts"]),
            ("Frontier Remote Vs Relocation", audit["frontier_remote_vs_relocation"]),
        ]
    )

    profile_sections = "".join(
        (
            f"<section class='section'><h2>Top Leads By {escape(PROFILE_LENSES[profile_name].title)}</h2>"
            f"<div class='grid'>{list_html(items, profile_name=profile_name)}</div></section>"
        )
        for profile_name, items in audit["profile_leaderboards"].items()
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RN Opportunity Radar Score Audit</title>
  <style>
    :root {{
      --bg: #f6f8fc;
      --ink: #14324c;
      --muted: #5a7189;
      --line: rgba(19, 51, 79, 0.12);
      --panel: rgba(255,255,255,0.88);
      --shadow: 0 16px 44px rgba(23, 51, 79, 0.08);
      --radius: 22px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Manrope", "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #fbfcff 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .shell {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 72px;
    }}
    .hero, .card, .table-wrap, .counter-card {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .hero {{
      padding: 24px;
      margin-bottom: 24px;
    }}
    .eyebrow {{
      margin: 0 0 8px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.75rem;
      color: #2a6f7d;
      font-weight: 700;
    }}
    h1, h2, h3 {{ margin-top: 0; }}
    .summary, .counter-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .summary div {{
      padding: 14px;
      border-radius: 18px;
      background: rgba(19, 51, 79, 0.04);
    }}
    .summary span {{
      display: block;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .summary strong {{
      display: block;
      font-size: 1.8rem;
      line-height: 1.1;
      margin-top: 4px;
    }}
    .section {{
      margin-top: 28px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }}
    .card, .counter-card {{
      padding: 18px;
    }}
    .card-kicker {{
      margin: 0 0 10px;
      color: #2a6f7d;
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .meta {{
      color: var(--muted);
    }}
    .table-wrap {{
      overflow-x: auto;
      padding: 12px;
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
    .empty {{
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">Decision-support audit</p>
      <h1>Profile lenses, employers, movement, and curation on top of the radar</h1>
      <p>Generated {escape(generated_at)}. This page audits the navigation layer, not just the base ranker.</p>
      <div class="summary">
        <div><span>Core Priority</span><strong>{summary['priority_count']}</strong></div>
        <div><span>Core Bridge</span><strong>{summary['bridge_count']}</strong></div>
        <div><span>Frontier Target</span><strong>{summary['target_count']}</strong></div>
        <div><span>Strategic Watch</span><strong>{summary['strategic_watch_count']}</strong></div>
        <div><span>Ecosystem Signal</span><strong>{summary['ecosystem_signal_count']}</strong></div>
        <div><span>Starred Leads</span><strong>{summary['starred_count']}</strong></div>
        <div><span>Saved Leads</span><strong>{summary['saved_count']}</strong></div>
        <div><span>Dismissed Leads</span><strong>{summary['dismissed_count']}</strong></div>
        <div><span>Pinned Leads</span><strong>{summary['pinned_count']}</strong></div>
        <div><span>Starred Employers</span><strong>{summary['starred_employers']}</strong></div>
        <div><span>High Interest Employers</span><strong>{summary['high_interest_employers']}</strong></div>
        <div><span>Movement New</span><strong>{movement_summary.get('new_high_signal_count', 0)}</strong></div>
        <div><span>Movement Promoted</span><strong>{movement_summary.get('promoted_count', 0)}</strong></div>
      </div>
    </section>

    <section class="section">
      <h2>Counts and Frontier Shape</h2>
      <div class="counter-grid">{counter_html}</div>
    </section>

    {profile_sections}

    <section class="section">
      <h2>Employer Leaderboard By Track</h2>
      <h3>Core employers</h3>
      <div class="grid">{employer_html(audit['core_employer_leaderboard'])}</div>
      <h3>Frontier employers</h3>
      <div class="grid">{employer_html(audit['frontier_employer_leaderboard'])}</div>
    </section>

    <section class="section">
      <h2>Employers Appearing Across Both Rails</h2>
      <div class="grid">{employer_html(audit['cross_track_employers'])}</div>
    </section>

    <section class="section">
      <h2>Movement Summary</h2>
      <p>New high-signal: {movement_summary.get('new_high_signal_count', 0)} · Promoted: {movement_summary.get('promoted_count', 0)} · Vanished: {movement_summary.get('vanished_count', 0)} · Growing employers: {movement_summary.get('growing_employer_count', 0)} · New frontier targets: {movement_summary.get('new_frontier_target_count', 0)} · Saved lead changes: {movement_summary.get('saved_lead_change_count', 0)}</p>
    </section>

    <section class="section">
      <h2>Core Buckets and Frontier Buckets</h2>
      <div class="grid">{list_html(audit['top_priority'])}{list_html(audit['top_bridge'])}{list_html(audit['frontier_targets'])}</div>
    </section>

    <section class="section">
      <h2>Frontier Strategic Watch and Signals</h2>
      <div class="grid">{list_html(audit['frontier_watchlist'])}{list_html(audit['frontier_signals'])}</div>
    </section>

    <section class="section">
      <h2>Borderline and Noise Floor</h2>
      <div class="grid">{list_html(audit['borderline_priority'])}{list_html(audit['strongest_discards'])}</div>
    </section>

    <section class="section">
      <h2>Bucket Distribution By Source</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Track</th>
              <th>Class</th>
              <th>Avg Score</th>
              <th>Kept</th>
              <th>Discarded</th>
              <th>Deduped Away</th>
              <th>Priority</th>
              <th>Bridge</th>
              <th>Watch</th>
              <th>Long Shot</th>
              <th>Target</th>
              <th>Strategic Watch</th>
              <th>Ecosystem Signal</th>
              <th>Noise Floor</th>
            </tr>
          </thead>
          <tbody>
            {''.join(distribution_rows)}
          </tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>"""


def render_score_audit_json(audit: dict[str, object]) -> str:
    return json.dumps(audit, indent=2)
