from rn_opportunity_radar.audit import build_score_audit, render_score_audit_html
from rn_opportunity_radar.employers import build_employer_rollups
from rn_opportunity_radar.models import OpportunityLead, SourceReport
from rn_opportunity_radar.profiles import apply_profile_overlays


def make_lead(
    lead_key: str,
    *,
    bucket: str,
    score: int,
    source_key: str,
    track: str = "core_rn_oregon",
    lead_type: str = "job",
    subtrack: str = "health_system",
    geo_scope: str = "oregon",
    rn_leverage_type: str = "direct_clinical",
) -> OpportunityLead:
    return OpportunityLead(
        lead_key=lead_key,
        lead_type=lead_type,
        source_key=source_key,
        source_name=source_key,
        company="Test Co",
        title=f"Lead {lead_key}",
        detail_url="https://example.com/job",
        source_url="https://example.com/source",
        bucket=bucket,
        score=score,
        track=track,
        subtrack=subtrack,
        geo_scope=geo_scope,
        rn_leverage_type=rn_leverage_type,
        score_breakdown={"penalty_score": -20 if bucket in {"discard", "low_fit"} else 0, "bucket_decision_notes": []},
    )


def test_build_score_audit_includes_profiles_employers_and_movement() -> None:
    leads = [
        make_lead("p1", bucket="priority", score=110, source_key="ohsu"),
        make_lead("b1", bucket="bridge", score=72, source_key="ohsu", rn_leverage_type="informatics"),
        make_lead("f1", bucket="target", score=132, source_key="viz", track="frontier_ecosystem", subtrack="vendor", geo_scope="remote_us", rn_leverage_type="implementation"),
        make_lead("f2", bucket="strategic_watch", score=66, source_key="ohsu_ai_center", track="frontier_ecosystem", lead_type="signal", subtrack="research_innovation", geo_scope="oregon", rn_leverage_type="research"),
    ]
    apply_profile_overlays(leads, {})
    employers = build_employer_rollups("2026-04-11T07:07:00-07:00", leads)
    reports = [
        SourceReport(source_key="ohsu", source_name="OHSU", source_url="https://example.com/ohsu"),
        SourceReport(source_key="viz", source_name="Viz.ai", source_url="https://example.com/viz", track="frontier_ecosystem"),
    ]
    movement = {"summary": {"new_high_signal_count": 1, "promoted_count": 1}}

    audit = build_score_audit(
        "2026-04-11T07:07:00-07:00",
        leads,
        [],
        reports,
        employers,
        movement,
        {"saved_leads": 1, "dismissed_leads": 0, "pinned_leads": 1, "starred_employers": 1},
    )

    assert "oregon_now" in audit["profile_leaderboards"]
    assert "bridge_to_informatics" in audit["profile_leaderboards"]
    assert audit["movement_summary"]["new_high_signal_count"] == 1
    assert len(audit["frontier_employer_leaderboard"]) >= 1
    assert audit["summary"]["saved_count"] == 1


def test_render_score_audit_html_includes_new_decision_support_sections() -> None:
    leads = [
        make_lead("p1", bucket="priority", score=110, source_key="ohsu"),
        make_lead("f1", bucket="target", score=132, source_key="viz", track="frontier_ecosystem", subtrack="vendor", geo_scope="remote_us", rn_leverage_type="implementation"),
    ]
    apply_profile_overlays(leads, {})
    employers = build_employer_rollups("2026-04-11T07:07:00-07:00", leads)
    audit = build_score_audit(
        "2026-04-11T07:07:00-07:00",
        leads,
        [],
        [SourceReport(source_key="viz", source_name="Viz.ai", source_url="https://example.com/viz", track="frontier_ecosystem")],
        employers,
        {"summary": {}},
        {"saved_leads": 0, "dismissed_leads": 0, "pinned_leads": 0, "starred_employers": 0},
    )

    html = render_score_audit_html("2026-04-11T07:07:00-07:00", audit)

    assert "Decision-support audit" in html
    assert "Top Leads By Oregon Now" in html
    assert "Employer Leaderboard By Track" in html
    assert "Employers Appearing Across Both Rails" in html
    assert "Movement Summary" in html
