from __future__ import annotations

import re
from dataclasses import dataclass, field

from rn_opportunity_radar.config import (
    AI_FORWARD_EMPLOYERS,
    BRIDGE_TITLE_SIGNALS,
    BUCKET_THRESHOLDS,
    DESCRIPTION_SIGNALS,
    EMPLOYER_SIGNAL_BOOSTS,
    ICU_SIGNALS,
    NEGATIVE_DESCRIPTION_SIGNALS,
    NEGATIVE_TITLE_SIGNALS,
    OREGON_EMPLOYERS,
    PRIORITY_LOCATIONS,
    RN_REQUIREMENT_SIGNALS,
    SECONDARY_LOCATIONS,
    SOURCE_CONTEXT_SIGNALS,
    STRONG_RN_TITLE_SIGNALS,
)
from rn_opportunity_radar.models import OpportunityLead
from rn_opportunity_radar.utils import dedupe_text_list


EMPLOYER_ALIASES = {
    "ohsu": ("ohsu", "oregon health"),
    "providence": ("providence",),
    "peacehealth": ("peacehealth",),
    "legacy": ("legacy", "legacy health"),
    "kaiser": ("kaiser", "kaiser permanente"),
    "pearl_health": ("pearl health", "pearlhealth"),
    "viz_ai": ("viz.ai", "viz ai", "viz"),
}

BRIDGE_TERMS = (
    "informatics",
    "epic",
    "ehr",
    "implementation",
    "workflow",
    "quality",
    "analytics",
    "digital",
    "virtual care",
    "clinical systems",
    "clinical documentation",
    "research",
    "decision support",
)

AI_TERMS = (
    " ai ",
    "artificial intelligence",
    "machine learning",
    "ambient documentation",
    "dax",
    "copilot",
    "applied ai",
)

DIFFERENTIATOR_TERMS = (
    "icu",
    "critical care",
    "intensive care",
    "case manager",
    "care manager",
    "coordinator",
    "educator",
    "quality",
    "implementation",
    "informatics",
    "epic",
    "workflow",
    "documentation",
    "virtual care",
    "research",
)

CARE_COORDINATION_TERMS = (
    "case manager",
    "care manager",
    "coordinator",
    "educator",
    "quality",
    "research coordinator",
    "clinical documentation",
    "population health",
    "utilization management",
)

HARD_NEGATIVE_ROLE_TERMS = {
    "cna",
    "lpn",
    "medical assistant",
    "physician",
    "resident physician",
    "dentist",
}

FRONTIER_TITLE_SIGNALS = {
    "care pathway lead": 42,
    "implementation manager": 38,
    "implementation specialist": 36,
    "implementation": 26,
    "deployment": 22,
    "clinical specialist": 30,
    "clinical sales specialist": 28,
    "customer success": 26,
    "clinical success": 32,
    "informatics": 34,
    "clinical informatics": 40,
    "workflow": 20,
    "product specialist": 24,
    "clinical product": 26,
    "clinical data manager": 24,
    "clinical affairs": 20,
    "research": 16,
    "innovation": 14,
}

FRONTIER_DESCRIPTION_SIGNALS = {
    "implementation": 18,
    "workflow": 18,
    "clinical": 12,
    "provider": 10,
    "care coordination": 18,
    "customer success": 18,
    "clinical success": 20,
    "informatics": 22,
    "digital health": 18,
    "clinical operations": 18,
    "clinical performance": 18,
    "value-based care": 18,
    "population health": 14,
    "research": 14,
    "ai": 16,
    "artificial intelligence": 22,
}

FRONTIER_ENGINEERING_PENALTIES = {
    "software engineer": -42,
    "engineering manager": -36,
    "backend engineer": -40,
    "frontend engineer": -40,
    "full stack": -38,
    "full-stack": -38,
    "data engineer": -34,
    "platform engineer": -34,
    "devops": -34,
    "site reliability": -34,
    "sre": -30,
}

FRONTIER_BUSINESS_PENALTIES = {
    "business development": -18,
    "account executive": -22,
    "recruiter": -26,
    "legal": -18,
    "compliance": -14,
}

FRONTIER_EMPLOYER_BOOSTS = {
    "providence": 16,
    "ohsu": 14,
    "pearl_health": 12,
    "viz_ai": 14,
}

FRONTIER_LEVERAGE_WEIGHTS = {
    "direct_clinical": 18,
    "implementation": 36,
    "clinical_success": 32,
    "informatics": 36,
    "research": 24,
    "product_clinical": 28,
}

FRONTIER_TARGETABLE_TYPES = {
    "implementation",
    "clinical_success",
    "informatics",
    "research",
    "product_clinical",
    "direct_clinical",
}

PRIORITY_FLOOR = 104
BRIDGE_PRIORITY_FLOOR = 96
BRIDGE_PROMOTION_WINDOW = 8
GENERIC_RN_WATCH_FLOOR = 56

FRONTIER_TARGET_FLOOR = 82
FRONTIER_STRATEGIC_WATCH_FLOOR = 54
FRONTIER_ECOSYSTEM_SIGNAL_FLOOR = 34

FRONTIER_EXPLICIT_LEVERAGE_TERMS = (
    "care pathway",
    "implementation",
    "customer success",
    "clinical success",
    "clinical specialist",
    "clinical sales specialist",
    "clinical informatics",
    "informatics",
    "rn required",
    "registered nurse",
    "nurse",
    "product specialist",
    "clinical data manager",
)


@dataclass
class ScoreState:
    lead: OpportunityLead
    employer_slug: str
    track: str
    source_class: str
    title: str
    description: str
    location: str
    context: str
    combined: str
    reasons: list[str] = field(default_factory=lambda: ["job collected from a tracked source"])
    tags: list[str] = field(default_factory=list)
    bucket_decision_notes: list[str] = field(default_factory=list)
    title_score: int = 0
    description_score: int = 0
    rn_score: int = 0
    location_score: int = 0
    employer_score: int = 0
    icu_score: int = 0
    bridge_score: int = 0
    signal_score: int = 0
    frontier_fit_score: int = 0
    leverage_score: int = 0
    scope_score: int = 0
    penalty_score: int = 0
    role_class: str = "unclassified"
    promoted_by_bridge_logic: bool = False
    demoted_generic_rn_volume: bool = False
    direct_rn_fit: bool = False
    bridge_fit: bool = False
    rn_leverage: bool = False
    ai_fit: bool = False
    tech_fit: bool = False
    icu_fit: bool = False
    coordination_fit: bool = False
    oregon_focus: bool = False
    generic_rn_volume: bool = False
    hard_negative_match: bool = False
    engineering_heavy: bool = False

    def add_points(
        self,
        component: str,
        points: int,
        *,
        reason: str | None = None,
        tag: str | None = None,
    ) -> None:
        setattr(self, component, getattr(self, component) + points)
        if reason:
            push_unique(self.reasons, reason)
        if tag:
            push_unique(self.tags, tag)

    @property
    def final_score(self) -> int:
        return (
            self.title_score
            + self.description_score
            + self.rn_score
            + self.location_score
            + self.employer_score
            + self.icu_score
            + self.bridge_score
            + self.signal_score
            + self.frontier_fit_score
            + self.leverage_score
            + self.scope_score
            + self.penalty_score
        )


def _matches_phrase(text: str, phrase: str) -> bool:
    normalized = phrase.strip()
    if not normalized:
        return False

    if phrase in {"rn ", "rn-"} or normalized == "rn":
        return re.search(r"(?<![a-z0-9])rn(?![a-z0-9])", text) is not None

    pattern = r"(?<![a-z0-9])" + re.escape(normalized).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _matching_phrases(text: str, weights: dict[str, int]) -> list[str]:
    return [phrase for phrase in weights if _matches_phrase(text, phrase.lower())]


def push_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _infer_employer_slug(lead: OpportunityLead) -> str:
    combined = f"{lead.company} {lead.source_name} {lead.source_key}".lower()
    for slug, aliases in EMPLOYER_ALIASES.items():
        if any(alias in combined for alias in aliases):
            return slug
    return ""


def _apply_weight_map(
    state: ScoreState,
    text: str,
    weights: dict[str, int],
    *,
    component: str,
    reason_template: str,
    tag: str | None = None,
    max_points: int | None = None,
) -> int:
    total = 0
    seen_aliases: set[str] = set()

    for phrase, points in weights.items():
        alias = phrase.replace("-", " ").strip().lower()
        if alias in seen_aliases:
            continue
        if not _matches_phrase(text, phrase.lower()):
            continue
        seen_aliases.add(alias)
        total += points
        if max_points is not None and total > max_points:
            total = max_points
        push_unique(state.reasons, reason_template.format(phrase=phrase))
        if tag:
            push_unique(state.tags, tag)
        if max_points is not None and total >= max_points:
            break

    if total:
        state.add_points(component, total)
    return total


def _infer_geo_scope(lead: OpportunityLead) -> str:
    location = lead.location.lower()
    default_scope = (lead.geo_scope or "oregon").lower()

    if "remote" in location:
        return "remote_us"
    if any(phrase in location for phrase in ("eugene, or", "springfield, or", "portland, or", "salem, or", "oregon")):
        return "oregon"
    if any(phrase in location for phrase in ("seattle", "washington", "vancouver, wa", "pacific northwest")):
        return "pacific_northwest"
    if any(phrase in location for phrase in ("san francisco", "los angeles", "california", "west coast")):
        return "west_coast"
    return default_scope


def _infer_relocation_risk(lead: OpportunityLead) -> str:
    geo_scope = (lead.geo_scope or "oregon").lower()
    location = lead.location.lower()
    if geo_scope == "oregon" or "remote" in location:
        return "none"
    if geo_scope in {"pacific_northwest", "west_coast"}:
        return "possible"
    return "likely"


def _infer_frontier_rn_leverage_type(state: ScoreState) -> str:
    combined = state.combined
    title = state.title

    if _contains_any(combined, ("implementation", "deployment", "care pathway", "onboarding", "adoption")):
        return "implementation"
    if _contains_any(combined, ("customer success", "clinical success", "clinical sales specialist", "clinical specialist")):
        return "clinical_success"
    if _contains_any(combined, ("informatics", "ehr", "epic", "workflow", "clinical systems")):
        return "informatics"
    if _contains_any(combined, ("research", "clinical affairs", "clinical data manager", "clinical performance")):
        return "research"
    if _contains_any(combined, ("product specialist", "product manager", "product ", "solutions", "solution specialist")):
        return "product_clinical"
    if _contains_any(title, ("registered nurse", "rn ", "rn-", "nurse")) or state.direct_rn_fit:
        return "direct_clinical"
    return ""


def _apply_core_lead_profile(lead: OpportunityLead, state: ScoreState) -> None:
    lead.track = "core_rn_oregon"
    lead.geo_scope = _infer_geo_scope(lead)
    lead.relocation_risk = _infer_relocation_risk(lead)
    lead.horizon = "post_reinstatement"

    if state.role_class == "bridge":
        if _contains_any(state.combined, ("informatics", "epic", "ehr", "workflow")):
            lead.rn_leverage_type = "informatics"
        elif _contains_any(state.combined, ("implementation", "virtual care", "quality")):
            lead.rn_leverage_type = "implementation"
        elif "research" in state.combined:
            lead.rn_leverage_type = "research"
        else:
            lead.rn_leverage_type = "implementation"
    else:
        lead.rn_leverage_type = "direct_clinical"


def _apply_frontier_lead_profile(lead: OpportunityLead, state: ScoreState, leverage_type: str) -> None:
    lead.track = "frontier_ecosystem"
    lead.geo_scope = _infer_geo_scope(lead)
    lead.relocation_risk = _infer_relocation_risk(lead)
    lead.rn_leverage_type = leverage_type or lead.rn_leverage_type or "product_clinical"

    if lead.lead_type == "signal":
        lead.horizon = "stretch"
        return

    if lead.geo_scope == "remote_us" and leverage_type in {"clinical_success", "product_clinical"} and state.final_score >= FRONTIER_STRATEGIC_WATCH_FLOOR:
        lead.horizon = "now"
    elif leverage_type in {"implementation", "informatics", "direct_clinical"}:
        lead.horizon = "post_reinstatement"
    else:
        lead.horizon = "stretch"


def _finalize_lead(lead: OpportunityLead, state: ScoreState, bucket: str) -> OpportunityLead:
    lead.score = state.final_score
    lead.bucket = bucket
    lead.reasons = state.reasons[:8]
    lead.tags = dedupe_text_list(state.tags)
    lead.score_breakdown = {
        "track": lead.track,
        "bucket_universe": lead.track,
        "title_score": state.title_score,
        "description_score": state.description_score,
        "rn_score": state.rn_score,
        "location_score": state.location_score,
        "employer_score": state.employer_score,
        "icu_score": state.icu_score,
        "bridge_score": state.bridge_score,
        "signal_score": state.signal_score,
        "frontier_fit_score": state.frontier_fit_score,
        "leverage_score": state.leverage_score,
        "scope_score": state.scope_score,
        "penalty_score": state.penalty_score,
        "final_score": state.final_score,
        "role_class": state.role_class,
        "bucket_decision_notes": state.bucket_decision_notes,
        "promoted_by_bridge_logic": state.promoted_by_bridge_logic,
        "demoted_generic_rn_volume": state.demoted_generic_rn_volume,
        "generic_rn_volume": state.generic_rn_volume,
        "engineering_heavy": state.engineering_heavy,
    }
    return lead


def _signal_breakdown(
    lead: OpportunityLead,
    employer_slug: str,
    title: str,
    description: str,
    context: str,
) -> OpportunityLead:
    state = ScoreState(
        lead=lead,
        employer_slug=employer_slug,
        track=lead.track,
        source_class=str(lead.metadata.get("source_class", "official")).lower(),
        title=title,
        description=description,
        location=lead.location.lower(),
        context=context,
        combined=f" {title} {description} {context} ",
        reasons=["public employer or ecosystem signal worth monitoring"],
        tags=list(lead.tags),
    )
    state.role_class = "signal"

    if employer_slug:
        boost = EMPLOYER_SIGNAL_BOOSTS.get(employer_slug, 0)
        if boost:
            state.add_points("employer_score", boost, reason=f"employer context boost for {lead.company}", tag=lead.company)
        lead.metadata["employer_priority"] = "high" if employer_slug in AI_FORWARD_EMPLOYERS else "medium"

    _apply_weight_map(
        state,
        state.combined,
        SOURCE_CONTEXT_SIGNALS,
        component="signal_score",
        reason_template="signal context includes '{phrase}'",
        tag="Signal",
    )
    _apply_weight_map(
        state,
        state.combined,
        DESCRIPTION_SIGNALS,
        component="description_score",
        reason_template="signal content references '{phrase}'",
        max_points=24,
    )

    if _contains_any(state.combined, AI_TERMS):
        state.ai_fit = True
        push_unique(state.tags, "AI")
        lead.metadata["ai_signal"] = True
    if _contains_any(state.combined, BRIDGE_TERMS):
        state.bridge_fit = True
        push_unique(state.tags, "Informatics")
        lead.metadata["informatics_signal"] = True

    if employer_slug in AI_FORWARD_EMPLOYERS:
        state.add_points("employer_score", 10, reason="employer has visible AI or informatics momentum")

    if state.final_score >= BUCKET_THRESHOLDS["watch"]:
        bucket = "watch"
        push_unique(state.bucket_decision_notes, "Signals are capped at watch even when the employer context is strong.")
    elif state.final_score >= BUCKET_THRESHOLDS["long_shot"]:
        bucket = "long_shot"
        push_unique(state.bucket_decision_notes, "Signal is interesting but not strong enough for watch.")
    else:
        bucket = "discard"
        push_unique(state.bucket_decision_notes, "Weak signal relevance.")

    _apply_core_lead_profile(lead, state)
    return _finalize_lead(lead, state, bucket)


def _score_job_breakdown(lead: OpportunityLead, employer_slug: str) -> OpportunityLead:
    title = lead.title.lower()
    description = lead.description.lower()
    location = lead.location.lower()
    context = f"{lead.source_context} {lead.discovered_via}".lower()
    combined = f" {title} {description} {location} {context} "

    state = ScoreState(
        lead=lead,
        employer_slug=employer_slug,
        track=lead.track,
        source_class=str(lead.metadata.get("source_class", "official")).lower(),
        title=title,
        description=description,
        location=location,
        context=context,
        combined=combined,
        tags=list(lead.tags),
    )

    matched_negative_titles = _matching_phrases(title, NEGATIVE_TITLE_SIGNALS)
    matched_negative_descriptions = _matching_phrases(description, NEGATIVE_DESCRIPTION_SIGNALS)

    _apply_weight_map(
        state,
        title,
        STRONG_RN_TITLE_SIGNALS,
        component="title_score",
        reason_template="title matches '{phrase}'",
        tag="RN",
    )
    _apply_weight_map(
        state,
        title,
        BRIDGE_TITLE_SIGNALS,
        component="bridge_score",
        reason_template="title suggests '{phrase}' bridge work",
        tag="Bridge",
    )
    _apply_weight_map(
        state,
        description,
        DESCRIPTION_SIGNALS,
        component="description_score",
        reason_template="description includes '{phrase}'",
        max_points=28,
    )
    _apply_weight_map(
        state,
        context,
        SOURCE_CONTEXT_SIGNALS,
        component="employer_score",
        reason_template="employer or source context includes '{phrase}'",
        max_points=24,
    )
    _apply_weight_map(
        state,
        combined,
        RN_REQUIREMENT_SIGNALS,
        component="rn_score",
        reason_template="requirements mention '{phrase}'",
        tag="RN",
    )
    _apply_weight_map(
        state,
        combined,
        ICU_SIGNALS,
        component="icu_score",
        reason_template="critical-care context includes '{phrase}'",
        tag="ICU",
        max_points=28,
    )

    if matched_negative_titles:
        penalty = sum(NEGATIVE_TITLE_SIGNALS[phrase] for phrase in matched_negative_titles)
        state.add_points("penalty_score", penalty, reason=f"title penalty for '{matched_negative_titles[0]}'")
        for phrase in matched_negative_titles[1:]:
            push_unique(state.reasons, f"title penalty for '{phrase}'")

    if matched_negative_descriptions:
        penalty = sum(NEGATIVE_DESCRIPTION_SIGNALS[phrase] for phrase in matched_negative_descriptions)
        state.add_points("penalty_score", penalty, reason=f"description penalty for '{matched_negative_descriptions[0]}'")
        for phrase in matched_negative_descriptions[1:]:
            push_unique(state.reasons, f"description penalty for '{phrase}'")

    for phrase, points in PRIORITY_LOCATIONS.items():
        if phrase in location:
            state.add_points("location_score", points, reason=f"priority location match: {phrase}", tag="Oregon")
            state.oregon_focus = True

    for phrase, points in SECONDARY_LOCATIONS.items():
        if phrase in location:
            state.add_points("location_score", points, reason=f"secondary location match: {phrase}")
            if "remote" in phrase:
                push_unique(state.tags, "Remote")

    if employer_slug:
        employer_boost = EMPLOYER_SIGNAL_BOOSTS.get(employer_slug, 0)
        if employer_boost:
            state.add_points("employer_score", employer_boost, reason=f"employer boost for {lead.company}", tag=lead.company)

    state.direct_rn_fit = state.title_score > 0 or state.rn_score > 0 or "registered nurse" in combined
    state.rn_leverage = state.direct_rn_fit or "nursing degree" in combined or "bsn" in combined
    state.bridge_fit = state.bridge_score > 0 or _contains_any(combined, BRIDGE_TERMS)
    state.ai_fit = _contains_any(combined, AI_TERMS)
    state.tech_fit = state.bridge_fit or state.ai_fit or "workflow" in combined or "implementation" in combined
    state.icu_fit = state.icu_score > 0
    state.coordination_fit = _contains_any(combined, CARE_COORDINATION_TERMS)
    state.oregon_focus = state.oregon_focus or "oregon" in location or employer_slug in OREGON_EMPLOYERS

    if state.direct_rn_fit:
        lead.metadata["rn_required"] = True
        push_unique(state.tags, "RN")
    if state.source_class == "board":
        push_unique(state.tags, "Board")
    if state.bridge_fit:
        lead.metadata["bridge_role"] = True
        push_unique(state.tags, "Bridge")
    if state.ai_fit:
        lead.metadata["ai_signal"] = True
        push_unique(state.tags, "AI")
    if state.bridge_fit or "informatics" in combined or "epic" in combined or "ehr" in combined:
        lead.metadata["informatics_signal"] = True
        push_unique(state.tags, "Informatics")
    if state.oregon_focus:
        lead.metadata["oregon_focus"] = True

    if employer_slug in AI_FORWARD_EMPLOYERS and state.direct_rn_fit:
        state.add_points("employer_score", 8, reason="employer has active AI / informatics footprint")

    if not state.direct_rn_fit and not state.bridge_fit:
        state.add_points("penalty_score", -26, reason="missing clear RN or bridge-role signal")

    if state.direct_rn_fit and state.bridge_fit and employer_slug in OREGON_EMPLOYERS and state.final_score < BUCKET_THRESHOLDS["bridge"]:
        shortfall = BUCKET_THRESHOLDS["bridge"] - state.final_score
        state.add_points("bridge_score", shortfall, reason="bridge-role logic lifts RN plus informatics/workflow fit")
        state.promoted_by_bridge_logic = True

    differentiator_fit = state.icu_fit or state.coordination_fit or state.tech_fit or _contains_any(combined, DIFFERENTIATOR_TERMS)
    state.generic_rn_volume = state.direct_rn_fit and not state.bridge_fit and not differentiator_fit

    if state.generic_rn_volume:
        state.demoted_generic_rn_volume = True
        state.add_points("penalty_score", -18, reason="generic bedside RN volume is intentionally demoted below specialized or bridge roles")

    state.hard_negative_match = any(
        phrase in HARD_NEGATIVE_ROLE_TERMS for phrase in [*matched_negative_titles, *matched_negative_descriptions]
    ) or "no rn required" in matched_negative_descriptions

    if state.bridge_fit and state.rn_leverage:
        state.role_class = "bridge"
    elif state.direct_rn_fit and state.generic_rn_volume:
        state.role_class = "direct_rn_generic"
    elif state.direct_rn_fit:
        state.role_class = "direct_rn_specialized"
    else:
        state.role_class = "unrelated"

    bucket = _decide_job_bucket(state)
    _apply_core_lead_profile(lead, state)
    return _finalize_lead(lead, state, bucket)


def _decide_job_bucket(state: ScoreState) -> str:
    if state.hard_negative_match:
        push_unique(state.bucket_decision_notes, "Hard non-RN mismatch overrides employer or location boosts.")
        return "discard"

    if state.role_class == "unrelated":
        push_unique(state.bucket_decision_notes, "Role lacks RN leverage and bridge-role relevance.")
        return "discard"

    if state.role_class == "bridge":
        if state.final_score >= BRIDGE_PRIORITY_FLOOR and (state.rn_leverage or state.oregon_focus) and state.tech_fit:
            if state.source_class == "board" and (state.final_score < PRIORITY_FLOOR + 18 or state.rn_score < 28 or not state.rn_leverage):
                push_unique(state.bucket_decision_notes, "Board bridge roles need unusually strong RN plus informatics fit to reach priority.")
                return "bridge"
            push_unique(state.bucket_decision_notes, "Bridge role clears the raised priority bar through RN leverage and tech relevance.")
            return "priority"
        if state.final_score >= BUCKET_THRESHOLDS["bridge"]:
            push_unique(state.bucket_decision_notes, "Bridge role outranks ordinary bedside volume and stays in the bridge lane.")
            return "bridge"
        if state.final_score >= BUCKET_THRESHOLDS["bridge"] - BRIDGE_PROMOTION_WINDOW and state.rn_leverage and state.oregon_focus:
            state.promoted_by_bridge_logic = True
            push_unique(state.bucket_decision_notes, "Promoted into bridge because the RN-to-tech fit is stronger than the raw score alone.")
            return "bridge"
        if state.final_score >= BUCKET_THRESHOLDS["watch"]:
            push_unique(state.bucket_decision_notes, "Interesting bridge role, but not strong enough for bridge.")
            return "watch"
        if state.final_score >= BUCKET_THRESHOLDS["long_shot"]:
            return "long_shot"
        return "discard"

    if state.role_class == "direct_rn_specialized":
        if state.final_score >= PRIORITY_FLOOR and (state.icu_fit or state.coordination_fit or state.tech_fit):
            if state.source_class == "board" and not (state.bridge_fit and state.tech_fit and state.rn_leverage and state.rn_score >= 28):
                push_unique(state.bucket_decision_notes, "Board listings are capped below priority unless the RN-to-tech case is unusually strong.")
                return "bridge"
            push_unique(state.bucket_decision_notes, "Specialized RN fit clears the narrower priority gate.")
            return "priority"
        if state.final_score >= BUCKET_THRESHOLDS["bridge"]:
            push_unique(state.bucket_decision_notes, "Strong RN fit, but better treated as bridge than priority.")
            return "bridge"
        if state.final_score >= BUCKET_THRESHOLDS["watch"]:
            return "watch"
        if state.final_score >= BUCKET_THRESHOLDS["long_shot"]:
            return "long_shot"
        return "discard"

    state.demoted_generic_rn_volume = True
    push_unique(state.bucket_decision_notes, "Generic RN volume is capped below priority so bridge roles stay visible.")
    if state.final_score >= BUCKET_THRESHOLDS["bridge"] and state.oregon_focus and state.employer_slug in OREGON_EMPLOYERS:
        return "bridge"
    if state.final_score >= GENERIC_RN_WATCH_FLOOR:
        return "watch"
    if state.final_score >= BUCKET_THRESHOLDS["long_shot"]:
        return "long_shot"
    return "discard"


def _score_frontier_signal(lead: OpportunityLead, employer_slug: str) -> OpportunityLead:
    title = lead.title.lower()
    description = lead.description.lower()
    context = f"{lead.source_context} {lead.discovered_via} {lead.subtrack}".lower()

    state = ScoreState(
        lead=lead,
        employer_slug=employer_slug,
        track=lead.track,
        source_class=str(lead.metadata.get("source_class", "official")).lower(),
        title=title,
        description=description,
        location=lead.location.lower(),
        context=context,
        combined=f" {title} {description} {context} ",
        reasons=["curated frontier ecosystem signal worth monitoring"],
        tags=list(lead.tags),
    )
    state.role_class = "frontier_signal"

    _apply_weight_map(
        state,
        state.combined,
        FRONTIER_DESCRIPTION_SIGNALS,
        component="signal_score",
        reason_template="frontier signal references '{phrase}'",
        max_points=32,
    )
    _apply_weight_map(
        state,
        state.combined,
        SOURCE_CONTEXT_SIGNALS,
        component="signal_score",
        reason_template="source context includes '{phrase}'",
        max_points=26,
    )

    if employer_slug in FRONTIER_EMPLOYER_BOOSTS:
        state.add_points("employer_score", FRONTIER_EMPLOYER_BOOSTS[employer_slug], reason=f"frontier employer signal boost for {lead.company}")

    if _contains_any(state.combined, AI_TERMS):
        state.ai_fit = True
        push_unique(state.tags, "AI")
        lead.metadata["ai_signal"] = True
    if _contains_any(state.combined, BRIDGE_TERMS):
        state.bridge_fit = True
        push_unique(state.tags, "Informatics")
        lead.metadata["informatics_signal"] = True

    lead.geo_scope = _infer_geo_scope(lead)
    lead.relocation_risk = _infer_relocation_risk(lead)
    lead.horizon = "stretch"

    if state.final_score >= FRONTIER_STRATEGIC_WATCH_FLOOR and (lead.geo_scope in {"oregon", "pacific_northwest"} or state.ai_fit or state.bridge_fit):
        bucket = "strategic_watch"
        push_unique(state.bucket_decision_notes, "Strong innovation or workflow signal deserves the strategic watchlist.")
    elif state.final_score >= FRONTIER_ECOSYSTEM_SIGNAL_FLOOR:
        bucket = "ecosystem_signal"
        push_unique(state.bucket_decision_notes, "Signal belongs in the frontier ecosystem rail, not the actionable job rail.")
    else:
        bucket = "low_fit"
        push_unique(state.bucket_decision_notes, "Signal is too weak or generic for the curated frontier rail.")

    return _finalize_lead(lead, state, bucket)


def _score_frontier_job(lead: OpportunityLead, employer_slug: str) -> OpportunityLead:
    title = lead.title.lower()
    description = lead.description.lower()
    location = lead.location.lower()
    context = f"{lead.source_context} {lead.discovered_via} {lead.subtrack} {lead.metadata.get('department', '')} {lead.metadata.get('team_name', '')}".lower()
    combined = f" {title} {description} {location} {context} "

    state = ScoreState(
        lead=lead,
        employer_slug=employer_slug,
        track=lead.track,
        source_class=str(lead.metadata.get("source_class", "official")).lower(),
        title=title,
        description=description,
        location=location,
        context=context,
        combined=combined,
        reasons=["curated frontier ecosystem role"],
        tags=list(lead.tags),
    )

    _apply_weight_map(
        state,
        title,
        FRONTIER_TITLE_SIGNALS,
        component="frontier_fit_score",
        reason_template="frontier title matches '{phrase}'",
        tag="Frontier",
    )
    _apply_weight_map(
        state,
        combined,
        FRONTIER_DESCRIPTION_SIGNALS,
        component="description_score",
        reason_template="frontier context includes '{phrase}'",
        max_points=32,
    )
    _apply_weight_map(
        state,
        combined,
        DESCRIPTION_SIGNALS,
        component="bridge_score",
        reason_template="bridge signal includes '{phrase}'",
        max_points=24,
    )
    _apply_weight_map(
        state,
        combined,
        RN_REQUIREMENT_SIGNALS,
        component="rn_score",
        reason_template="RN leverage signal mentions '{phrase}'",
        max_points=24,
    )

    matched_engineering_titles = _matching_phrases(title, FRONTIER_ENGINEERING_PENALTIES)
    matched_business_titles = _matching_phrases(title, FRONTIER_BUSINESS_PENALTIES)
    if matched_engineering_titles:
        penalty = sum(FRONTIER_ENGINEERING_PENALTIES[phrase] for phrase in matched_engineering_titles)
        state.engineering_heavy = True
        state.add_points("penalty_score", penalty, reason=f"engineering-heavy title penalty for '{matched_engineering_titles[0]}'")
        for phrase in matched_engineering_titles[1:]:
            push_unique(state.reasons, f"engineering-heavy title penalty for '{phrase}'")
    if matched_business_titles:
        penalty = sum(FRONTIER_BUSINESS_PENALTIES[phrase] for phrase in matched_business_titles)
        state.add_points("penalty_score", penalty, reason=f"frontier role penalty for '{matched_business_titles[0]}'")
        for phrase in matched_business_titles[1:]:
            push_unique(state.reasons, f"frontier role penalty for '{phrase}'")

    if employer_slug in FRONTIER_EMPLOYER_BOOSTS:
        state.add_points("employer_score", FRONTIER_EMPLOYER_BOOSTS[employer_slug], reason=f"frontier employer boost for {lead.company}", tag=lead.company)

    state.direct_rn_fit = _contains_any(combined, ("registered nurse", " rn ", "rn-", "nurse practitioner", "nurse"))
    state.bridge_fit = _contains_any(combined, BRIDGE_TERMS)
    state.ai_fit = _contains_any(combined, AI_TERMS)
    state.tech_fit = state.bridge_fit or state.ai_fit or _contains_any(combined, ("clinical", "product", "customer success", "care pathway", "implementation"))
    leverage_type = _infer_frontier_rn_leverage_type(state)
    explicit_leverage_fit = _contains_any(f"{title} {description}", FRONTIER_EXPLICIT_LEVERAGE_TERMS)
    if state.engineering_heavy and not explicit_leverage_fit:
        leverage_type = ""
        state.add_points(
            "penalty_score",
            -18,
            reason="engineering-heavy role lacks explicit RN-leveraged implementation, informatics, or clinical-success duties",
        )
    if leverage_type:
        state.rn_leverage = True
        state.add_points(
            "leverage_score",
            FRONTIER_LEVERAGE_WEIGHTS[leverage_type],
            reason=f"role maps to RN leverage via {leverage_type.replace('_', ' ')}",
            tag=leverage_type.replace("_", " ").title(),
        )

    lead.geo_scope = _infer_geo_scope(lead)
    lead.relocation_risk = _infer_relocation_risk(lead)
    if lead.geo_scope == "remote_us":
        state.add_points("scope_score", 18, reason="remote US scope reduces relocation risk", tag="Remote")
    elif lead.geo_scope == "west_coast":
        state.add_points("scope_score", 12, reason="West Coast scope keeps the role relatively reachable")
    elif lead.geo_scope == "pacific_northwest":
        state.add_points("scope_score", 10, reason="Pacific Northwest scope keeps the role regionally adjacent")
    elif lead.geo_scope == "oregon":
        state.add_points("scope_score", 14, reason="frontier role still connects back to Oregon context", tag="Oregon")

    if not leverage_type and not state.ai_fit and not state.tech_fit:
        state.add_points("penalty_score", -18, reason="frontier role lacks a clear nursing, clinical, workflow, or implementation bridge")

    if state.engineering_heavy and leverage_type not in {"informatics", "implementation"}:
        state.add_points("penalty_score", -12, reason="pure engineering work sits outside the best RN-leveraged frontier lane")

    state.role_class = leverage_type or ("engineering" if state.engineering_heavy else "frontier_general")
    _apply_frontier_lead_profile(lead, state, leverage_type)

    if state.final_score >= FRONTIER_TARGET_FLOOR and leverage_type in FRONTIER_TARGETABLE_TYPES and not (state.engineering_heavy and leverage_type not in {"informatics", "implementation"}):
        bucket = "target"
        push_unique(state.bucket_decision_notes, "RN-leveraged frontier role is strong enough to stand as a real target.")
    elif state.final_score >= FRONTIER_STRATEGIC_WATCH_FLOOR and (leverage_type or state.ai_fit or state.tech_fit):
        bucket = "strategic_watch"
        push_unique(state.bucket_decision_notes, "Interesting frontier role, but better treated as a strategic watch item than an immediate target.")
    elif state.final_score >= FRONTIER_ECOSYSTEM_SIGNAL_FLOOR and (state.ai_fit or state.tech_fit or employer_slug in FRONTIER_EMPLOYER_BOOSTS):
        bucket = "ecosystem_signal"
        push_unique(state.bucket_decision_notes, "Role is ecosystem-relevant, but not strong enough to crowd the target rail.")
    else:
        bucket = "low_fit"
        push_unique(state.bucket_decision_notes, "Role is too generic, too engineering-heavy, or too weakly RN-leveraged for the frontier target rail.")

    return _finalize_lead(lead, state, bucket)


def evaluate_lead(lead: OpportunityLead) -> OpportunityLead:
    employer_slug = _infer_employer_slug(lead)
    if employer_slug:
        lead.metadata["employer_slug"] = employer_slug

    lead.track = (lead.track or str(lead.metadata.get("track", "core_rn_oregon"))).lower()
    lead.subtrack = (lead.subtrack or str(lead.metadata.get("subtrack", "health_system"))).lower()
    lead.horizon = (lead.horizon or str(lead.metadata.get("horizon", "post_reinstatement"))).lower()
    lead.geo_scope = (lead.geo_scope or str(lead.metadata.get("geo_scope", "oregon"))).lower()
    lead.rn_leverage_type = (lead.rn_leverage_type or str(lead.metadata.get("rn_leverage_type", "direct_clinical"))).lower()
    lead.relocation_risk = (lead.relocation_risk or str(lead.metadata.get("relocation_risk", "none"))).lower()

    title = lead.title.lower()
    description = lead.description.lower()
    context = f"{lead.source_context} {lead.discovered_via}".lower()

    if lead.track == "frontier_ecosystem":
        if lead.lead_type == "signal":
            return _score_frontier_signal(lead, employer_slug)
        return _score_frontier_job(lead, employer_slug)

    if lead.lead_type == "signal":
        return _signal_breakdown(lead, employer_slug, title, description, context)

    return _score_job_breakdown(lead, employer_slug)
