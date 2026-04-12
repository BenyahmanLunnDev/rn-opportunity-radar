from __future__ import annotations

from dataclasses import dataclass

from rn_opportunity_radar.models import OpportunityLead
from rn_opportunity_radar.utils import clean_text, dedupe_text_list


PROFILE_NAMES = ("oregon_now", "bridge_to_informatics", "frontier_transition")


@dataclass(frozen=True)
class ProfileLens:
    name: str
    title: str
    description: str


PROFILE_LENSES = {
    "oregon_now": ProfileLens(
        name="oregon_now",
        title="Oregon Now",
        description="Favors Oregon direct RN roles, bridge roles, realistic near-term fit, and low relocation risk.",
    ),
    "bridge_to_informatics": ProfileLens(
        name="bridge_to_informatics",
        title="Bridge To Informatics",
        description="Favors implementation, Epic/EHR, workflow, quality, informatics, care coordination, and clinical systems work.",
    ),
    "frontier_transition": ProfileLens(
        name="frontier_transition",
        title="Frontier Transition",
        description="Favors RN-leveraged vendor, clinical success, implementation, product-clinical, digital workflow, and remote-friendly frontier roles.",
    ),
}


def _push_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _normalized_set(values: object) -> set[str]:
    if isinstance(values, str):
        cleaned = clean_text(values).lower()
        return {cleaned} if cleaned else set()
    if not isinstance(values, list):
        return set()
    normalized = {clean_text(str(value)).lower() for value in values}
    return {value for value in normalized if value}


def _normalized_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return clean_text(value).lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _weight(config: dict[str, object], key: str, default: int) -> int:
    overrides = config.get("profile_weight_overrides", {})
    if not isinstance(overrides, dict):
        return default
    raw = overrides.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _profile_config(preferences: dict[str, object], profile_name: str) -> dict[str, object]:
    default_preferences = preferences.get("default_preferences", {})
    if not isinstance(default_preferences, dict):
        default_preferences = {}

    overrides = preferences.get("profile_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    profile_override = overrides.get(profile_name, {})
    if not isinstance(profile_override, dict):
        profile_override = {}

    merged = dict(default_preferences)
    merged.update({key: value for key, value in profile_override.items() if key != "profile_weight_overrides"})

    merged_weights = {}
    if isinstance(default_preferences.get("profile_weight_overrides"), dict):
        merged_weights.update(default_preferences["profile_weight_overrides"])
    if isinstance(profile_override.get("profile_weight_overrides"), dict):
        merged_weights.update(profile_override["profile_weight_overrides"])
    merged["profile_weight_overrides"] = merged_weights
    return merged


def _text_blob(lead: OpportunityLead) -> str:
    return clean_text(
        " ".join(
            [
                lead.title,
                lead.description,
                lead.source_context,
                lead.location,
                " ".join(lead.tags),
                " ".join(lead.reasons),
            ]
        )
    ).lower()


def _has_icu_signal(lead: OpportunityLead) -> bool:
    text = _text_blob(lead)
    icu_terms = ("icu", "critical care", "intensive care", "rapid response", "acuity", "perioperative")
    return any(term in text for term in icu_terms)


def _is_remote_friendly_frontier(lead: OpportunityLead) -> bool:
    if lead.track != "frontier_ecosystem":
        return False
    location = clean_text(lead.location).lower()
    remote_type = clean_text(str(lead.metadata.get("remote_type", ""))).lower()
    return lead.geo_scope == "remote_us" or "remote" in location or remote_type == "remote"


def _apply_manual_preferences(
    lead: OpportunityLead,
    profile_name: str,
    score: int,
    reasons: list[str],
    preferences: dict[str, object],
) -> tuple[int, int]:
    config = _profile_config(preferences, profile_name)
    company = lead.company.lower()
    preferred_employers = _normalized_set(config.get("preferred_employers", []))
    deprioritized_employers = _normalized_set(config.get("deprioritized_employers", []))
    preferred_geo_scopes = _normalized_set(config.get("preferred_geo_scopes", []))
    preferred_leverage_types = _normalized_set(config.get("preferred_rn_leverage_types", []))
    preferred_tracks = _normalized_set(config.get("preferred_tracks", []))
    preferred_horizons = _normalized_set(config.get("horizon_preference", []))
    relocation_preference = clean_text(str(config.get("relocation_preference", ""))).lower()

    delta = 0

    if company in preferred_employers:
        bonus = _weight(config, "preferred_employer_bonus", 10)
        delta += bonus
        _push_reason(reasons, f"profile preferences favor {lead.company}")
    if company in deprioritized_employers:
        penalty = _weight(config, "deprioritized_employer_penalty", 12)
        delta -= penalty
        _push_reason(reasons, f"profile preferences de-prioritize {lead.company}")

    if lead.geo_scope in preferred_geo_scopes:
        bonus = _weight(config, "preferred_geo_scope_bonus", 8)
        delta += bonus
        _push_reason(reasons, "profile preferences favor this geo scope")
    if lead.track in preferred_tracks:
        bonus = _weight(config, "preferred_track_bonus", 6)
        delta += bonus
        _push_reason(reasons, "profile preferences favor this track")
    if lead.rn_leverage_type in preferred_leverage_types:
        bonus = _weight(config, "preferred_leverage_bonus", 8)
        delta += bonus
        _push_reason(reasons, "profile preferences favor this RN leverage type")

    if preferred_horizons:
        if lead.horizon in preferred_horizons:
            bonus = _weight(config, "horizon_match_bonus", 8)
            delta += bonus
            _push_reason(reasons, "profile preferences align with this time horizon")
        else:
            penalty = _weight(config, "horizon_mismatch_penalty", 6)
            delta -= penalty
            _push_reason(reasons, "profile preferences downweight this horizon")

    if relocation_preference in {"avoid_likely", "prefer_none"} and lead.relocation_risk == "likely":
        penalty = _weight(config, "relocation_penalty", 12)
        delta -= penalty
        _push_reason(reasons, "profile preferences penalize high relocation risk")
    elif relocation_preference == "prefer_none" and lead.relocation_risk == "none":
        bonus = max(4, _weight(config, "relocation_penalty", 12) // 2)
        delta += bonus
        _push_reason(reasons, "profile preferences favor low relocation risk")

    if _normalized_bool(config.get("focus_icu_adjacency", False)) and _has_icu_signal(lead):
        bonus = _weight(config, "icu_bonus", 10)
        delta += bonus
        _push_reason(reasons, "profile preferences favor ICU or critical-care adjacency")

    if _normalized_bool(config.get("prefer_remote_frontier", False)) and _is_remote_friendly_frontier(lead):
        bonus = _weight(config, "remote_frontier_bonus", 10)
        delta += bonus
        _push_reason(reasons, "profile preferences favor remote-friendly frontier roles")

    employer_interest = preferences.get("employer_interest_overrides", {})
    if isinstance(employer_interest, dict):
        interest_level = clean_text(str(employer_interest.get(company, ""))).lower()
        if interest_level == "high":
            bonus = _weight(config, "high_interest_employer_bonus", 14)
            delta += bonus
            _push_reason(reasons, "manual employer interest level is high")
        elif interest_level == "medium":
            bonus = _weight(config, "medium_interest_employer_bonus", 8)
            delta += bonus
            _push_reason(reasons, "manual employer interest level is medium")
        elif interest_level == "frontier":
            bonus = _weight(config, "medium_interest_employer_bonus", 8)
            delta += bonus
            _push_reason(reasons, "manual employer interest marks this as frontier-relevant")
        elif interest_level == "low":
            penalty = _weight(config, "low_interest_employer_penalty", 10)
            delta -= penalty
            _push_reason(reasons, "manual employer interest currently rates this lower")

    score += delta
    return score, delta


def _score_oregon_now(lead: OpportunityLead) -> tuple[int, list[str]]:
    score = max(lead.score, 0)
    reasons: list[str] = []

    if lead.track == "core_rn_oregon":
        score += 34
        _push_reason(reasons, "core Oregon rail gets first priority for near-term action")
    else:
        score -= 24
        _push_reason(reasons, "frontier rail is intentionally secondary for Oregon-now decisions")

    if lead.bucket == "priority":
        score += 28
        _push_reason(reasons, "priority bucket strengthens immediate Oregon-now fit")
    elif lead.bucket in {"bridge", "target"}:
        score += 18
        _push_reason(reasons, "bridge-capable work can still be actionable in the near term")
    elif lead.bucket in {"strategic_watch", "ecosystem_signal"}:
        score -= 14

    if lead.geo_scope == "oregon":
        score += 22
        _push_reason(reasons, "Oregon scope aligns with the near-term target geography")
    elif lead.geo_scope == "pacific_northwest":
        score += 8
    elif lead.geo_scope == "remote_us":
        score += 6
    else:
        score -= 8

    if lead.relocation_risk == "none":
        score += 14
        _push_reason(reasons, "low relocation risk improves near-term practicality")
    elif lead.relocation_risk == "likely":
        score -= 18
        _push_reason(reasons, "high relocation risk weakens Oregon-now usefulness")

    if lead.rn_leverage_type in {"direct_clinical", "implementation", "informatics"}:
        score += 14
        _push_reason(reasons, "RN leverage maps well to realistic next-step work")

    if lead.horizon in {"now", "post_reinstatement"}:
        score += 10
    else:
        score -= 10

    if lead.lead_type == "signal":
        score -= 16
        _push_reason(reasons, "signals matter, but roles outrank them in the Oregon-now lens")

    return score, reasons


def _score_bridge_to_informatics(lead: OpportunityLead) -> tuple[int, list[str]]:
    score = max(lead.score, 0)
    reasons: list[str] = []

    if lead.rn_leverage_type in {"implementation", "informatics"}:
        score += 48
        _push_reason(reasons, "implementation or informatics leverage is central to this lens")
    elif lead.rn_leverage_type in {"research", "product_clinical", "clinical_success"}:
        score += 18
    elif lead.rn_leverage_type == "direct_clinical":
        score -= 8

    if lead.bucket in {"bridge", "target"}:
        score += 28
        _push_reason(reasons, "base bucket already indicates a bridge-capable role")
    elif lead.bucket == "priority":
        score += 14
    elif lead.bucket in {"strategic_watch", "ecosystem_signal"}:
        score += 6

    text = _text_blob(lead)
    bridge_terms = (
        "implementation",
        "informatics",
        "workflow",
        "epic",
        "ehr",
        "quality",
        "clinical systems",
        "care coordination",
    )
    if any(term in text for term in bridge_terms):
        score += 30
        _push_reason(reasons, "workflow or informatics language strengthens the bridge case")

    if lead.track == "frontier_ecosystem":
        score += 10
    elif lead.track == "core_rn_oregon" and lead.bucket == "bridge":
        score += 12
        _push_reason(reasons, "core-rail bridge roles are especially useful stepping stones")

    if lead.lead_type == "signal":
        score -= 10

    return score, reasons


def _score_frontier_transition(lead: OpportunityLead) -> tuple[int, list[str]]:
    score = max(lead.score, 0)
    reasons: list[str] = []

    if lead.track == "frontier_ecosystem":
        score += 38
        _push_reason(reasons, "frontier rail is primary for transition planning")
    else:
        score -= 18

    if lead.bucket == "target":
        score += 28
        _push_reason(reasons, "frontier target status is exactly what this lens looks for")
    elif lead.bucket == "strategic_watch":
        score += 18
        _push_reason(reasons, "strategic frontier watch items still matter for transition planning")
    elif lead.bucket == "ecosystem_signal":
        score += 10
    elif lead.bucket in {"priority", "bridge"} and lead.track == "core_rn_oregon":
        score += 6

    if lead.rn_leverage_type in {"implementation", "clinical_success", "product_clinical", "informatics"}:
        score += 26
        _push_reason(reasons, "RN-leveraged vendor or workflow work is the target transition lane")
    elif lead.rn_leverage_type == "research":
        score += 12
    elif lead.rn_leverage_type == "direct_clinical":
        score -= 10

    if lead.geo_scope in {"remote_us", "west_coast"}:
        score += 18
        _push_reason(reasons, "remote or West Coast scope improves frontier transition flexibility")
    elif lead.geo_scope == "pacific_northwest":
        score += 10

    if lead.relocation_risk == "likely":
        score -= 16
        _push_reason(reasons, "high relocation risk weakens transition practicality")

    if lead.lead_type == "signal":
        score -= 8

    return score, reasons


def _score_profile(
    lead: OpportunityLead,
    profile_name: str,
    preferences: dict[str, object],
) -> tuple[int, int, list[str]]:
    if profile_name == "oregon_now":
        score, reasons = _score_oregon_now(lead)
    elif profile_name == "bridge_to_informatics":
        score, reasons = _score_bridge_to_informatics(lead)
    else:
        score, reasons = _score_frontier_transition(lead)

    score, manual_delta = _apply_manual_preferences(lead, profile_name, score, reasons, preferences)
    if lead.starred:
        score += 14
        _push_reason(reasons, "starred leads stay highly visible in profile-driven picks")
    elif lead.saved:
        score += 8
        _push_reason(reasons, "saved leads stay visible in profile-driven picks")
    if lead.dismissed:
        score -= 60
        _push_reason(reasons, "dismissed leads are heavily de-prioritized in decision views")

    return score, manual_delta, dedupe_text_list(reasons)


def apply_profile_overlays(leads: list[OpportunityLead], preferences: dict[str, object]) -> None:
    for lead in leads:
        lead.profile_scores = {}
        lead.profile_reasons = {}
        lead.profile_preference_deltas = {}
        for profile_name in PROFILE_NAMES:
            score, manual_delta, reasons = _score_profile(lead, profile_name, preferences)
            manual_reasons = [
                reason
                for reason in reasons
                if "profile preferences" in reason or "manual employer interest" in reason
            ]
            prioritized_reasons = dedupe_text_list(manual_reasons + reasons)
            lead.profile_scores[profile_name] = score
            lead.profile_preference_deltas[profile_name] = manual_delta
            lead.profile_reasons[profile_name] = prioritized_reasons[:6]


def sorted_leads_for_profile(
    leads: list[OpportunityLead],
    profile_name: str,
    *,
    include_dismissed: bool = False,
) -> list[OpportunityLead]:
    filtered = [lead for lead in leads if include_dismissed or not lead.dismissed]
    return sorted(
        filtered,
        key=lambda lead: (
            -lead.profile_scores.get(profile_name, lead.score),
            -lead.score,
            lead.company.lower(),
            lead.title.lower(),
        ),
    )


def profile_catalog() -> list[dict[str, str]]:
    return [
        {
            "name": lens.name,
            "title": lens.title,
            "description": lens.description,
        }
        for lens in PROFILE_LENSES.values()
    ]
