from rn_opportunity_radar.employers import build_employer_rollups
from rn_opportunity_radar.models import EmployerRollup, OpportunityLead
from rn_opportunity_radar.movement import build_movement_summary
from rn_opportunity_radar.profiles import apply_profile_overlays


def build_lead(
    lead_key: str,
    title: str,
    *,
    company: str,
    track: str,
    bucket: str,
    saved: bool = False,
) -> OpportunityLead:
    return OpportunityLead(
        lead_key=lead_key,
        lead_type="job",
        source_key="test",
        source_name="Test Source",
        company=company,
        title=title,
        detail_url=f"https://example.com/{lead_key}",
        source_url="https://example.com/source",
        score=100,
        bucket=bucket,
        track=track,
        geo_scope="oregon" if track == "core_rn_oregon" else "remote_us",
        rn_leverage_type="implementation" if track == "frontier_ecosystem" else "direct_clinical",
        saved=saved,
    )


def test_movement_tracking_identifies_new_promoted_and_vanished_leads() -> None:
    previous_promoted = build_lead("promoted", "Clinical Informatics RN", company="Providence", track="core_rn_oregon", bucket="bridge", saved=True)
    previous_vanished = build_lead("vanished", "RN ICU", company="Legacy", track="core_rn_oregon", bucket="priority")
    previous_employer = EmployerRollup(
        employer_key="providence",
        employer_name="Providence",
        tracks=["core_rn_oregon"],
        track_counts={"core_rn_oregon": 1},
    )
    previous = {lead.lead_key: lead for lead in [previous_promoted, previous_vanished]}

    current_promoted = build_lead("promoted", "Clinical Informatics RN", company="Providence", track="core_rn_oregon", bucket="priority", saved=True)
    current_new = build_lead("new-frontier", "Clinical Success Manager", company="Viz.ai", track="frontier_ecosystem", bucket="target")
    current = [current_promoted, current_new]
    apply_profile_overlays(current, {})
    employers = build_employer_rollups("2026-04-11T07:07:00-07:00", current)

    movement = build_movement_summary(
        "2026-04-11T07:07:00-07:00",
        current,
        previous,
        employers,
        {"providence": previous_employer},
    )

    assert movement["summary"]["new_high_signal_count"] == 1
    assert movement["summary"]["promoted_count"] == 1
    assert movement["summary"]["vanished_count"] == 1
    assert movement["summary"]["new_frontier_target_count"] == 1
    assert movement["summary"]["saved_lead_change_count"] == 1
