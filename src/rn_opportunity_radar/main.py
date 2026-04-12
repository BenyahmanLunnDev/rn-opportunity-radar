from __future__ import annotations

import argparse
from pathlib import Path

from rn_opportunity_radar.audit import build_score_audit, render_score_audit_html, render_score_audit_json
from rn_opportunity_radar.employers import (
    build_employer_rollups,
    load_previous_employer_rollups,
    save_employer_rollups,
)
from rn_opportunity_radar.manual import apply_employer_curation, apply_lead_curation, load_manual_state
from rn_opportunity_radar.movement import build_movement_summary, save_movement_summary
from rn_opportunity_radar.persistence import (
    build_summary,
    is_kept_lead,
    load_previous_leads,
    load_previous_reports,
    merge_with_history,
    save_artifacts,
)
from rn_opportunity_radar.profiles import apply_profile_overlays
from rn_opportunity_radar.render import render_feed_xml, render_index, render_latest_json
from rn_opportunity_radar.scoring import evaluate_lead
from rn_opportunity_radar.sources import scrape_all_sources
from rn_opportunity_radar.utils import make_session, now_iso, repo_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RN Opportunity Radar pipeline.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to write generated files into.",
    )
    parser.add_argument(
        "--browser-path",
        default=None,
        help="Optional explicit browser binary for hydrated DOM fallback.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    session = make_session()
    generated_at = now_iso()

    scraped_leads, reports = scrape_all_sources(
        session,
        browser_path=args.browser_path,
        browser_profile_dir=root / ".codex" / "playwright-profile",
    )
    scored_leads = [evaluate_lead(lead) for lead in scraped_leads]
    previous_leads = load_previous_leads(root)
    previous_reports = load_previous_reports(root)
    previous_employers = load_previous_employer_rollups(root)
    manual_state = load_manual_state(root)

    for report in reports:
        previous = previous_reports.get(report.source_key)
        report.last_attempt_at = generated_at
        report.last_success_at = generated_at if report.status == "ok" else (previous.last_success_at if previous else "")

    merged_leads = merge_with_history(scored_leads, previous_leads, reports)
    curation_summary = apply_lead_curation(merged_leads, manual_state)
    profile_preferences = manual_state.get("profile_preferences", {})
    apply_profile_overlays(merged_leads, profile_preferences if isinstance(profile_preferences, dict) else {})
    employers = build_employer_rollups(generated_at, merged_leads)
    curation_summary.update(apply_employer_curation(employers, manual_state))

    jobs = [lead for lead in merged_leads if lead.lead_type == "job"]
    signals = [lead for lead in merged_leads if lead.lead_type == "signal"]

    for report in reports:
        report.total_relevant = sum(
            1
            for lead in merged_leads
            if lead.source_key == report.source_key and lead.status == "active" and is_kept_lead(lead)
        )

    summary = build_summary(generated_at, jobs, signals, reports)
    save_artifacts(root, jobs, signals, reports, summary)
    save_employer_rollups(root, generated_at, employers)
    movement = build_movement_summary(generated_at, merged_leads, previous_leads, employers, previous_employers)
    save_movement_summary(root, movement)
    score_audit = build_score_audit(generated_at, jobs, signals, reports, employers, movement, curation_summary)

    docs_dir = repo_path(root, "docs")
    docs_dir.mkdir(parents=True, exist_ok=True)
    repo_path(root, "data", "current", "score_audit.json").write_text(
        render_score_audit_json(score_audit),
        encoding="utf-8",
    )
    repo_path(root, "docs", "index.html").write_text(
        render_index(generated_at, jobs, signals, reports, summary, employers, movement, curation_summary, profile_preferences if isinstance(profile_preferences, dict) else {}),
        encoding="utf-8",
    )
    repo_path(root, "docs", "latest.json").write_text(
        render_latest_json(generated_at, jobs, signals, reports, summary, employers, movement, curation_summary, profile_preferences if isinstance(profile_preferences, dict) else {}),
        encoding="utf-8",
    )
    repo_path(root, "docs", "feed.xml").write_text(
        render_feed_xml(generated_at, jobs, signals),
        encoding="utf-8",
    )
    repo_path(root, "docs", "score-audit.html").write_text(
        render_score_audit_html(generated_at, score_audit),
        encoding="utf-8",
    )

    return 0
