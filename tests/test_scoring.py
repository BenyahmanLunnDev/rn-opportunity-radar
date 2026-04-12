from rn_opportunity_radar.models import OpportunityLead
from rn_opportunity_radar.scoring import evaluate_lead


def make_lead(
    title: str,
    *,
    lead_type: str = "job",
    company: str = "OHSU",
    source_key: str = "ohsu_rn",
    source_name: str = "OHSU Registered Nurse Jobs",
    description: str = "",
    location: str = "",
    source_context: str = "",
    track: str = "core_rn_oregon",
    subtrack: str = "health_system",
    horizon: str = "post_reinstatement",
    geo_scope: str = "oregon",
    rn_leverage_type: str = "direct_clinical",
    relocation_risk: str = "none",
    metadata: dict | None = None,
) -> OpportunityLead:
    return OpportunityLead(
        lead_key="test",
        lead_type=lead_type,
        source_key=source_key,
        source_name=source_name,
        company=company,
        title=title,
        detail_url="https://example.com/job",
        source_url="https://example.com/source",
        description=description,
        location=location,
        source_context=source_context,
        track=track,
        subtrack=subtrack,
        horizon=horizon,
        geo_scope=geo_scope,
        rn_leverage_type=rn_leverage_type,
        relocation_risk=relocation_risk,
        metadata=metadata or {},
    )


def test_standard_ohsu_rn_posting_scores_as_bridge_or_priority() -> None:
    lead = make_lead(
        "RN, Acute Care Float Pool",
        description="Current RN license required. Float to acute care units across the hospital.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket in {"bridge", "priority"}
    assert evaluated.score >= 68
    assert evaluated.track == "core_rn_oregon"


def test_epic_analyst_role_with_rn_preference_scores_as_bridge_or_priority() -> None:
    lead = make_lead(
        "Epic Analyst",
        company="Providence",
        source_key="providence_oregon_nursing",
        source_name="Providence Oregon Nursing Jobs",
        description="RN required. Workflow optimization, implementation support, and Epic build experience preferred.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket in {"bridge", "priority"}
    assert "Bridge" in evaluated.tags


def test_providence_transformation_signal_stays_in_frontier_watch_or_signal() -> None:
    lead = make_lead(
        "Providence Office of Transformation",
        lead_type="signal",
        company="Providence",
        source_key="providence_office_of_transformation",
        source_name="Providence Office of Transformation",
        description="Transformation, workflow redesign, and digital care initiatives across the system.",
        source_context="office of transformation",
        track="frontier_ecosystem",
        subtrack="health_system",
        horizon="post_reinstatement",
        geo_scope="oregon",
        rn_leverage_type="implementation",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket in {"strategic_watch", "ecosystem_signal"}
    assert evaluated.bucket != "target"
    assert evaluated.track == "frontier_ecosystem"


def test_cna_only_posting_is_discarded() -> None:
    lead = make_lead(
        "Certified Nursing Assistant",
        description="Certified nursing assistant role. No RN required.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "discard"


def test_unrelated_admin_posting_is_discarded() -> None:
    lead = make_lead(
        "Administrative Assistant",
        description="Calendar management, executive support, and office coordination.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "discard"


def test_generic_bedside_rn_without_extra_context_is_not_priority() -> None:
    lead = make_lead(
        "Registered Nurse",
        description="Direct bedside patient care on a hospital unit. Current RN license required.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket in {"bridge", "watch"}
    assert evaluated.bucket != "priority"
    assert evaluated.score_breakdown["demoted_generic_rn_volume"] is True


def test_icu_rn_posting_can_reach_priority() -> None:
    lead = make_lead(
        "RN, Pediatric Intensive Care Unit",
        description="Current RN license required. Critical care and ICU experience preferred.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "priority"
    assert evaluated.score_breakdown["icu_score"] > 0


def test_medical_assistant_posting_is_discarded() -> None:
    lead = make_lead(
        "Medical Assistant",
        description="Medical assistant role in outpatient clinic. No RN required.",
        location="Portland, OR",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "discard"
    assert evaluated.score_breakdown["penalty_score"] < 0


def test_board_bridge_role_is_usually_capped_below_priority() -> None:
    lead = make_lead(
        "Clinical Informatics Specialist",
        company="Regional Health System",
        source_key="amia_jobs",
        source_name="AMIA Jobs Board",
        description="RN preferred. Clinical systems, workflow redesign, and implementation support.",
        location="Portland, OR",
        metadata={"source_class": "board", "source_priority": 78},
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "bridge"
    assert "Board" in evaluated.tags


def test_board_generic_low_fit_role_does_not_surface_too_high() -> None:
    lead = make_lead(
        "Coding Manager",
        company="Remote Vendor",
        source_key="ania_jobs",
        source_name="ANIA Jobs Board",
        description="Manage coding operations and denials. RN not required.",
        location="US Remote",
        metadata={"source_class": "board", "source_priority": 76},
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket in {"watch", "long_shot", "discard"}
    assert evaluated.bucket != "priority"


def test_frontier_target_role_does_not_leak_into_core_priority_bucket() -> None:
    lead = make_lead(
        "Care Pathway Lead - Cardiology",
        company="Viz.ai",
        source_key="viz_ai_frontier_roles",
        source_name="Viz.ai Frontier Roles",
        description="Remote role driving implementation, workflow adoption, clinical success, and care coordination with provider teams.",
        location="Remote, United States",
        source_context="AI-powered intelligent care coordination and clinical workflow platform",
        track="frontier_ecosystem",
        subtrack="vendor",
        horizon="stretch",
        geo_scope="remote_us",
        rn_leverage_type="implementation",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "target"
    assert evaluated.bucket not in {"priority", "bridge", "watch"}
    assert evaluated.track == "frontier_ecosystem"


def test_frontier_nursing_implementation_role_can_rank_highly() -> None:
    lead = make_lead(
        "Clinical Success Manager",
        company="Pearl Health",
        source_key="pearl_health_frontier_roles",
        source_name="Pearl Health Frontier Roles",
        description="Remote role helping practices with workflow adoption, implementation, clinical operations, and value-based care performance.",
        location="Remote, United States",
        source_context="remote-friendly value-based care employer with customer success, product, operations, and clinical performance roles",
        track="frontier_ecosystem",
        subtrack="vendor",
        horizon="stretch",
        geo_scope="remote_us",
        rn_leverage_type="clinical_success",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket == "target"
    assert evaluated.score_breakdown["leverage_score"] > 0


def test_general_engineering_role_ranks_below_rn_leveraged_frontier_role() -> None:
    target = evaluate_lead(
        make_lead(
            "Care Pathway Lead - Neurology",
            company="Viz.ai",
            source_key="viz_ai_frontier_roles",
            source_name="Viz.ai Frontier Roles",
            description="Remote role leading implementation, care coordination, workflow adoption, and clinical success.",
            location="Remote, United States",
            source_context="AI-powered intelligent care coordination and clinical workflow platform",
            track="frontier_ecosystem",
            subtrack="vendor",
            horizon="stretch",
            geo_scope="remote_us",
            rn_leverage_type="implementation",
        )
    )
    engineering = evaluate_lead(
        make_lead(
            "Senior Software Engineer, Platform",
            company="Viz.ai",
            source_key="viz_ai_frontier_roles",
            source_name="Viz.ai Frontier Roles",
            description="Build backend services and distributed systems for the clinical platform.",
            location="Remote, United States",
            source_context="AI-powered intelligent care coordination and clinical workflow platform",
            track="frontier_ecosystem",
            subtrack="vendor",
            horizon="stretch",
            geo_scope="remote_us",
            rn_leverage_type="product_clinical",
        )
    )

    assert target.bucket == "target"
    assert engineering.bucket in {"low_fit", "ecosystem_signal"}
    assert engineering.score < target.score


def test_research_innovation_signal_becomes_frontier_watch_or_signal() -> None:
    lead = make_lead(
        "Center for AI-enabled Learning Health Science",
        lead_type="signal",
        company="OHSU",
        source_key="ohsu_ai_center",
        source_name="OHSU AI Center",
        description="AI, research, and innovation signal page for learning health science and clinical operations.",
        source_context="ai-enabled learning health science",
        location="Portland, OR",
        track="frontier_ecosystem",
        subtrack="research_innovation",
        horizon="stretch",
        geo_scope="oregon",
        rn_leverage_type="research",
    )
    evaluated = evaluate_lead(lead)
    assert evaluated.bucket in {"strategic_watch", "ecosystem_signal"}
    assert evaluated.bucket != "target"
