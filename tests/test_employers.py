from rn_opportunity_radar.employers import build_employer_rollups
from rn_opportunity_radar.models import OpportunityLead
from rn_opportunity_radar.profiles import apply_profile_overlays


def build_lead(
    lead_key: str,
    title: str,
    *,
    company: str,
    track: str,
    bucket: str,
    lead_type: str = "job",
    rn_leverage_type: str = "direct_clinical",
) -> OpportunityLead:
    return OpportunityLead(
        lead_key=lead_key,
        lead_type=lead_type,
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
        rn_leverage_type=rn_leverage_type,
    )


def test_employer_rollups_aggregate_correctly_across_tracks() -> None:
    leads = [
        build_lead("1", "RN ICU", company="Providence", track="core_rn_oregon", bucket="priority"),
        build_lead("2", "Clinical Informatics RN", company="Providence", track="core_rn_oregon", bucket="bridge", rn_leverage_type="informatics"),
        build_lead("3", "Clinical Success Manager", company="Providence", track="frontier_ecosystem", bucket="target", rn_leverage_type="clinical_success"),
        build_lead("4", "Transformation Signal", company="Providence", track="frontier_ecosystem", bucket="ecosystem_signal", lead_type="signal", rn_leverage_type="implementation"),
    ]
    apply_profile_overlays(leads, {})

    employers = build_employer_rollups("2026-04-11T07:07:00-07:00", leads)
    providence = next(employer for employer in employers if employer.employer_name == "Providence")

    assert providence.appears_in_both_tracks is True
    assert providence.innovation_signal_presence is True
    assert providence.bucket_counts["priority"] == 1
    assert providence.bucket_counts["target"] == 1
    assert "high_priority_employer" in providence.employer_tags
    assert "frontier_employer" in providence.employer_tags
