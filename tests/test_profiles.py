from rn_opportunity_radar.models import OpportunityLead
from rn_opportunity_radar.profiles import apply_profile_overlays


def make_lead(
    title: str,
    *,
    score: int,
    bucket: str,
    track: str = "core_rn_oregon",
    geo_scope: str = "oregon",
    rn_leverage_type: str = "direct_clinical",
    relocation_risk: str = "none",
    lead_type: str = "job",
) -> OpportunityLead:
    return OpportunityLead(
        lead_key=title.lower().replace(" ", "-"),
        lead_type=lead_type,
        source_key="test",
        source_name="Test Source",
        company="Test Co",
        title=title,
        detail_url="https://example.com/job",
        source_url="https://example.com/source",
        score=score,
        bucket=bucket,
        track=track,
        geo_scope=geo_scope,
        rn_leverage_type=rn_leverage_type,
        relocation_risk=relocation_risk,
    )


def test_profile_overlays_do_not_break_base_scoring() -> None:
    lead = make_lead("RN ICU", score=120, bucket="priority")

    apply_profile_overlays([lead], {})

    assert lead.score == 120
    assert lead.bucket == "priority"
    assert set(lead.profile_scores) == {"oregon_now", "bridge_to_informatics", "frontier_transition"}


def test_oregon_direct_rn_role_ranks_higher_in_oregon_now_than_frontier_transition() -> None:
    lead = make_lead("RN Case Manager", score=102, bucket="priority", rn_leverage_type="direct_clinical")

    apply_profile_overlays([lead], {})

    assert lead.profile_scores["oregon_now"] > lead.profile_scores["frontier_transition"]


def test_implementation_role_ranks_strongly_in_bridge_to_informatics() -> None:
    lead = make_lead(
        "Clinical Informatics Specialist",
        score=88,
        bucket="bridge",
        rn_leverage_type="informatics",
    )

    apply_profile_overlays([lead], {})

    assert lead.profile_scores["bridge_to_informatics"] > lead.profile_scores["oregon_now"]
    assert lead.profile_scores["bridge_to_informatics"] > lead.score


def test_clinical_success_vendor_role_ranks_strongly_in_frontier_transition() -> None:
    lead = make_lead(
        "Clinical Success Manager",
        score=96,
        bucket="target",
        track="frontier_ecosystem",
        geo_scope="remote_us",
        rn_leverage_type="clinical_success",
    )

    apply_profile_overlays([lead], {})

    assert lead.profile_scores["frontier_transition"] > lead.profile_scores["oregon_now"]
    assert lead.profile_scores["frontier_transition"] > lead.profile_scores["bridge_to_informatics"]


def test_manual_preferences_influence_overlay_scores_without_changing_base_score() -> None:
    lead = make_lead(
        "RN ICU Implementation Specialist",
        score=94,
        bucket="bridge",
        geo_scope="oregon",
        rn_leverage_type="implementation",
    )
    preferences = {
        "default_preferences": {
            "preferred_geo_scopes": ["oregon"],
            "preferred_rn_leverage_types": ["implementation"],
            "preferred_tracks": ["core_rn_oregon"],
            "horizon_preference": "post_reinstatement",
            "relocation_preference": "prefer_none",
            "focus_icu_adjacency": True,
            "profile_weight_overrides": {
                "preferred_geo_scope_bonus": 9,
                "preferred_leverage_bonus": 11,
                "preferred_track_bonus": 5,
                "icu_bonus": 13,
            },
        },
        "profile_overrides": {},
        "employer_interest_overrides": {},
    }

    apply_profile_overlays([lead], preferences)

    assert lead.score == 94
    assert lead.profile_preference_deltas["bridge_to_informatics"] > 0
    assert any(
        "profile preferences favor this RN leverage type" in reason
        for reason in lead.profile_reasons["bridge_to_informatics"]
    )
