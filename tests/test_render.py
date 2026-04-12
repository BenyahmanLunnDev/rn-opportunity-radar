from rn_opportunity_radar.employers import build_employer_rollups
from rn_opportunity_radar.models import OpportunityLead, SourceReport
from rn_opportunity_radar.profiles import apply_profile_overlays
from rn_opportunity_radar.render import render_index


def test_render_includes_decision_support_sections() -> None:
    core_job = OpportunityLead(
        lead_key="core-job-1",
        lead_type="job",
        source_key="ohsu_rn",
        source_name="OHSU Registered Nurse Jobs",
        company="OHSU",
        title="RN, Pediatric Intensive Care Unit",
        detail_url="https://example.com/job",
        source_url="https://example.com/source",
        location="Portland, OR",
        posted_date="2026-04-10",
        reasons=["title matches 'registered nurse'", "critical-care context includes 'icu'"],
        tags=["RN", "ICU"],
        bucket="priority",
        score=118,
        track="core_rn_oregon",
        geo_scope="oregon",
        rn_leverage_type="direct_clinical",
    )
    frontier_job = OpportunityLead(
        lead_key="frontier-job-1",
        lead_type="job",
        source_key="viz_ai_frontier_roles",
        source_name="Viz.ai Frontier Roles",
        company="Viz.ai",
        title="Care Pathway Lead - Cardiology",
        detail_url="https://example.com/frontier-job",
        source_url="https://example.com/frontier-source",
        location="Remote, United States",
        posted_date="2026-04-10",
        reasons=["frontier title matches 'care pathway lead'", "role maps to RN leverage via implementation"],
        tags=["Frontier", "Remote"],
        bucket="target",
        score=142,
        track="frontier_ecosystem",
        subtrack="vendor",
        horizon="stretch",
        geo_scope="remote_us",
        rn_leverage_type="implementation",
        relocation_risk="none",
    )
    apply_profile_overlays([core_job, frontier_job], {})
    employers = build_employer_rollups("2026-04-11T07:07:00-07:00", [core_job, frontier_job])
    report = SourceReport(
        source_key="ohsu_rn",
        source_name="OHSU Registered Nurse Jobs",
        source_url="https://example.com",
        status="ok",
        total_fetched=12,
        total_relevant=7,
    )

    html = render_index(
        "2026-04-11T07:07:00-07:00",
        [core_job, frontier_job],
        [],
        [report],
        {"healthy_source_count": 1, "failed_source_count": 0},
        employers,
        {"summary": {"new_high_signal_count": 1, "promoted_count": 1}},
        {"saved_leads": 0, "dismissed_leads": 0, "pinned_leads": 0, "starred_employers": 0},
        {"active_profile": "oregon_now", "profile_overrides": {}},
    )

    assert "Top Picks Right Now" in html
    assert "Best Bridge Bets" in html
    assert "Frontier Bets" in html
    assert "Employers To Watch" in html
    assert "What Changed Since Last Run" in html
    assert "Saved Leads / Starred Employers" in html
    assert "Source Health" in html
    assert "Oregon Now" in html
    assert "Bridge To Informatics" in html
    assert "Frontier Transition" in html
