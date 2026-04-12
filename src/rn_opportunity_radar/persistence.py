from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from rn_opportunity_radar.config import KEEP_EXPIRED_DAYS, TIMEZONE_NAME
from rn_opportunity_radar.models import OpportunityLead, SourceReport
from rn_opportunity_radar.utils import now_iso, repo_path, today_iso


def is_kept_lead(lead: OpportunityLead) -> bool:
    frontier_floor = lead.track == "frontier_ecosystem" and lead.bucket == "low_fit"
    core_floor = lead.track != "frontier_ecosystem" and lead.bucket == "discard"
    return not frontier_floor and not core_floor


def _bucket_sort_rank(lead: OpportunityLead) -> int:
    if lead.track == "frontier_ecosystem":
        order = {"target": 0, "strategic_watch": 1, "ecosystem_signal": 2, "low_fit": 3}
    else:
        order = {"priority": 0, "bridge": 1, "watch": 2, "long_shot": 3, "discard": 4}
    return order.get(lead.bucket, 9)


def _load_leads(path: Path) -> list[OpportunityLead]:
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    leads = payload.get("jobs") or payload.get("signals") or payload.get("leads") or []
    return [OpportunityLead.from_dict(item) for item in leads]


def load_previous_jobs(root: Path) -> dict[str, OpportunityLead]:
    path = repo_path(root, "data", "current", "jobs.json")
    return {lead.lead_key: lead for lead in _load_leads(path)}


def load_previous_signals(root: Path) -> dict[str, OpportunityLead]:
    path = repo_path(root, "data", "current", "signals.json")
    return {lead.lead_key: lead for lead in _load_leads(path)}


def load_previous_leads(root: Path) -> dict[str, OpportunityLead]:
    merged = {}
    merged.update(load_previous_jobs(root))
    merged.update(load_previous_signals(root))
    return merged


def load_previous_reports(root: Path) -> dict[str, SourceReport]:
    reports_path = repo_path(root, "data", "current", "reports.json")
    if not reports_path.exists():
        return {}

    payload = json.loads(reports_path.read_text(encoding="utf-8"))
    reports = [SourceReport.from_dict(item) for item in payload.get("reports", [])]
    return {report.source_key: report for report in reports}


def merge_with_history(
    current_leads: list[OpportunityLead],
    previous_leads: dict[str, OpportunityLead],
    reports: list[SourceReport],
) -> list[OpportunityLead]:
    today = date.fromisoformat(today_iso())
    merged: dict[str, OpportunityLead] = {}
    stale_sources = {report.source_key for report in reports if report.status == "error"}

    for lead in current_leads:
        previous = previous_leads.get(lead.lead_key)
        if previous:
            lead.first_seen = previous.first_seen or today.isoformat()
            lead.seen_count = previous.seen_count + 1
        else:
            lead.first_seen = today.isoformat()
            lead.seen_count = 1

        lead.last_seen = today.isoformat()
        lead.expired_on = ""
        lead.status = "active"
        lead.stale_source = False
        lead.stale_since = ""
        merged[lead.lead_key] = lead

    keep_until = today - timedelta(days=KEEP_EXPIRED_DAYS)

    for key, previous in previous_leads.items():
        if key in merged:
            continue

        if previous.status == "expired":
            expired_date = (
                date.fromisoformat(previous.expired_on)
                if previous.expired_on
                else date.fromisoformat(previous.last_seen or today.isoformat())
            )
            if expired_date < keep_until:
                continue
            merged[key] = previous
            continue

        if previous.source_key in stale_sources and previous.bucket != "discard":
            stale_copy = OpportunityLead.from_dict(previous.to_dict())
            stale_copy.status = "active"
            stale_copy.expired_on = ""
            stale_copy.stale_source = True
            stale_copy.stale_since = previous.stale_since or today.isoformat()
            merged[key] = stale_copy
            continue

        expired_copy = OpportunityLead.from_dict(previous.to_dict())
        expired_copy.status = "expired"
        expired_copy.stale_source = False
        expired_copy.stale_since = ""
        expired_copy.expired_on = today.isoformat()
        merged[key] = expired_copy

    return sorted(
        merged.values(),
        key=lambda lead: (
            lead.status != "active",
            lead.track == "frontier_ecosystem",
            lead.stale_source,
            _bucket_sort_rank(lead),
            -lead.score,
            lead.company.lower(),
            lead.title.lower(),
        ),
    )


def build_summary(
    generated_at: str,
    jobs: Iterable[OpportunityLead],
    signals: Iterable[OpportunityLead],
    reports: Iterable[SourceReport],
) -> dict[str, object]:
    job_list = list(jobs)
    signal_list = list(signals)
    report_list = list(reports)
    active_jobs = [lead for lead in job_list if lead.status == "active" and is_kept_lead(lead)]
    active_signals = [lead for lead in signal_list if lead.status == "active" and is_kept_lead(lead)]
    core_jobs = [lead for lead in active_jobs if lead.track == "core_rn_oregon"]
    frontier_jobs = [lead for lead in active_jobs if lead.track == "frontier_ecosystem"]
    frontier_signals = [lead for lead in active_signals if lead.track == "frontier_ecosystem"]

    return {
        "generated_at": generated_at,
        "total_jobs": len(active_jobs),
        "total_signals": len(active_signals),
        "new_today": sum(1 for lead in active_jobs if lead.first_seen == lead.last_seen),
        "priority_count": sum(1 for lead in core_jobs if lead.bucket == "priority"),
        "bridge_count": sum(1 for lead in core_jobs if lead.bucket == "bridge"),
        "watch_count": sum(1 for lead in [*core_jobs, *active_signals] if lead.bucket == "watch"),
        "long_shot_count": sum(1 for lead in [*core_jobs, *active_signals] if lead.bucket == "long_shot"),
        "track_counts": {
            "core_rn_oregon": sum(1 for lead in [*active_jobs, *active_signals] if lead.track == "core_rn_oregon"),
            "frontier_ecosystem": sum(1 for lead in [*active_jobs, *active_signals] if lead.track == "frontier_ecosystem"),
        },
        "core_track": {
            "total_jobs": len(core_jobs),
            "priority_count": sum(1 for lead in core_jobs if lead.bucket == "priority"),
            "bridge_count": sum(1 for lead in core_jobs if lead.bucket == "bridge"),
            "watch_count": sum(1 for lead in core_jobs if lead.bucket == "watch"),
            "long_shot_count": sum(1 for lead in core_jobs if lead.bucket == "long_shot"),
        },
        "frontier_track": {
            "total_jobs": len(frontier_jobs),
            "total_signals": len(frontier_signals),
            "target_count": sum(1 for lead in frontier_jobs if lead.bucket == "target"),
            "strategic_watch_count": sum(1 for lead in [*frontier_jobs, *frontier_signals] if lead.bucket == "strategic_watch"),
            "ecosystem_signal_count": sum(1 for lead in frontier_signals if lead.bucket == "ecosystem_signal"),
            "low_fit_count": sum(1 for lead in [*job_list, *signal_list] if lead.status == "active" and lead.track == "frontier_ecosystem" and lead.bucket == "low_fit"),
        },
        "stale_source_count": sum(1 for report in report_list if report.status == "error"),
        "healthy_source_count": sum(1 for report in report_list if report.status == "ok"),
        "failed_source_count": sum(1 for report in report_list if report.status != "ok"),
    }


def save_artifacts(
    root: Path,
    jobs: list[OpportunityLead],
    signals: list[OpportunityLead],
    reports: list[SourceReport],
    summary: dict[str, object],
) -> None:
    generated_at = now_iso()
    today = today_iso()

    current_dir = repo_path(root, "data", "current")
    history_dir = repo_path(root, "data", "history")
    docs_dir = repo_path(root, "docs")
    current_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    jobs_payload = {
        "generated_at": generated_at,
        "timezone": TIMEZONE_NAME,
        "jobs": [lead.to_dict() for lead in jobs],
    }
    (current_dir / "jobs.json").write_text(json.dumps(jobs_payload, indent=2), encoding="utf-8")

    signals_payload = {
        "generated_at": generated_at,
        "timezone": TIMEZONE_NAME,
        "signals": [lead.to_dict() for lead in signals],
    }
    (current_dir / "signals.json").write_text(
        json.dumps(signals_payload, indent=2),
        encoding="utf-8",
    )

    reports_payload = {
        "generated_at": generated_at,
        "reports": [report.to_dict() for report in reports],
    }
    (current_dir / "reports.json").write_text(
        json.dumps(reports_payload, indent=2),
        encoding="utf-8",
    )

    (current_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    history_payload = {
        "generated_at": generated_at,
        "timezone": TIMEZONE_NAME,
        "jobs": [lead.to_dict() for lead in jobs],
        "signals": [lead.to_dict() for lead in signals],
        "reports": [report.to_dict() for report in reports],
        "summary": summary,
    }
    (history_dir / f"{today}.json").write_text(
        json.dumps(history_payload, indent=2),
        encoding="utf-8",
    )
