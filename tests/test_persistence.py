from rn_opportunity_radar.models import OpportunityLead, SourceReport
from rn_opportunity_radar.persistence import build_summary, is_kept_lead, merge_with_history
from rn_opportunity_radar.utils import today_iso


def build_lead(lead_key: str, title: str, *, lead_type: str = "job", source_key: str = "test") -> OpportunityLead:
    return OpportunityLead(
        lead_key=lead_key,
        lead_type=lead_type,
        source_key=source_key,
        source_name="Test Source",
        company="Test Co",
        title=title,
        detail_url=f"https://example.com/{lead_key}",
        source_url="https://example.com",
        bucket="watch",
        score=60,
    )


def test_merge_marks_missing_leads_as_expired() -> None:
    previous = build_lead("lead-1", "RN Case Manager")
    previous.first_seen = "2026-04-01"
    previous.last_seen = "2026-04-09"

    current = [build_lead("lead-2", "RN ICU")]
    merged = merge_with_history(current, {"lead-1": previous}, [])
    statuses = {lead.lead_key: lead.status for lead in merged}

    assert statuses["lead-1"] == "expired"
    assert statuses["lead-2"] == "active"


def test_merge_preserves_first_seen_and_increments_seen_count() -> None:
    previous = build_lead("lead-1", "RN ICU")
    previous.first_seen = "2026-04-03"
    previous.last_seen = "2026-04-09"
    previous.seen_count = 4

    current = [build_lead("lead-1", "RN ICU")]
    merged = merge_with_history(current, {"lead-1": previous}, [])
    merged_lead = next(lead for lead in merged if lead.lead_key == "lead-1")

    assert merged_lead.first_seen == "2026-04-03"
    assert merged_lead.seen_count == 5


def test_merge_keeps_last_good_lead_when_source_errors() -> None:
    previous = build_lead("lead-1", "Clinical Informatics RN", source_key="ohsu_rn")
    previous.first_seen = "2026-04-01"
    previous.last_seen = "2026-04-09"
    previous.status = "active"

    reports = [
        SourceReport(
            source_key="ohsu_rn",
            source_name="OHSU Registered Nurse Jobs",
            source_url="https://example.com",
            status="error",
        )
    ]

    merged = merge_with_history([], {"lead-1": previous}, reports)
    merged_lead = next(lead for lead in merged if lead.lead_key == "lead-1")

    assert merged_lead.status == "active"
    assert merged_lead.stale_source is True
    assert merged_lead.stale_since == today_iso()


def test_frontier_low_fit_is_not_counted_as_kept_or_summary_total() -> None:
    core = build_lead("lead-1", "RN Case Manager")
    frontier = build_lead("lead-2", "Software Engineer")
    frontier.track = "frontier_ecosystem"
    frontier.bucket = "low_fit"

    summary = build_summary("2026-04-11T07:07:00-07:00", [core, frontier], [], [])

    assert is_kept_lead(core) is True
    assert is_kept_lead(frontier) is False
    assert summary["total_jobs"] == 1
    assert summary["frontier_track"]["low_fit_count"] == 1
