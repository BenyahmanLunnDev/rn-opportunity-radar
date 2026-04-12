from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from rn_opportunity_radar.models import EmployerRollup, OpportunityLead
from rn_opportunity_radar.utils import clean_text, dedupe_text_list, repo_path


DEFAULT_SAVED_LEADS = {
    "_schema_version": 1,
    "_editing_notes": [
        "Edit lead_overrides by hand. Keep JSON valid.",
        "starred implies saved and gives a stronger decision-support boost.",
        "dismissed hides the lead from decision views without changing base collection history.",
    ],
    "_example_entry": {
        "lead_key": "ohsu_rn:registered-nurse-icu",
        "title_hint": "RN, ICU",
        "company_hint": "OHSU",
        "starred": True,
        "saved": True,
        "dismissed": False,
        "pinned_reason": "Strong ICU fit and realistic near-term target.",
        "note": "Review after reinstatement paperwork is back in motion.",
        "notes": [
            "Would be a strong Oregon-now application target.",
            "Worth keeping visible even if similar roles rotate out.",
        ],
    },
    "lead_overrides": [],
}

DEFAULT_EMPLOYER_NOTES = {
    "_schema_version": 1,
    "_editing_notes": [
        "Edit employers by company name. Keep JSON valid.",
        "employer_interest_level can be low, medium, high, or frontier.",
        "starred employers get more visibility in the dashboard and employer dossiers.",
    ],
    "_example_entry": {
        "company": "Providence",
        "starred": True,
        "employer_interest_level": "high",
        "pinned_reason": "Strong bridge employer with visible innovation footprint.",
        "note": "Worth tracking across both Oregon and frontier rails.",
        "notes": [
            "Look for workflow, implementation, and clinical systems openings.",
            "Signals here matter even when a specific role is not ready yet.",
        ],
    },
    "employers": [],
}

DEFAULT_PROFILE_PREFERENCES = {
    "_schema_version": 1,
    "_editing_notes": [
        "default_preferences apply to every profile unless a profile override changes them.",
        "preferred_geo_scopes and preferred_rn_leverage_types should use values already present in the data.",
        "profile_weight_overrides lets you tune bonuses and penalties without changing code.",
    ],
    "active_profile": "oregon_now",
    "default_preferences": {
        "preferred_geo_scopes": ["oregon", "pacific_northwest"],
        "preferred_rn_leverage_types": [
            "direct_clinical",
            "implementation",
            "informatics",
            "product_clinical",
        ],
        "preferred_tracks": ["core_rn_oregon", "frontier_ecosystem"],
        "preferred_employers": [],
        "deprioritized_employers": [],
        "horizon_preference": "post_reinstatement",
        "relocation_preference": "avoid_likely",
        "focus_icu_adjacency": True,
        "prefer_remote_frontier": True,
        "profile_weight_overrides": {
            "preferred_geo_scope_bonus": 8,
            "preferred_leverage_bonus": 8,
            "preferred_track_bonus": 6,
            "preferred_employer_bonus": 10,
            "deprioritized_employer_penalty": 12,
            "high_interest_employer_bonus": 14,
            "medium_interest_employer_bonus": 8,
            "low_interest_employer_penalty": 10,
            "horizon_match_bonus": 8,
            "horizon_mismatch_penalty": 6,
            "relocation_penalty": 12,
            "icu_bonus": 10,
            "remote_frontier_bonus": 10,
        },
    },
    "profile_overrides": {
        "oregon_now": {
            "preferred_tracks": ["core_rn_oregon"],
            "preferred_geo_scopes": ["oregon", "pacific_northwest"],
            "preferred_rn_leverage_types": ["direct_clinical", "implementation", "informatics"],
            "horizon_preference": "now",
            "relocation_preference": "prefer_none",
            "focus_icu_adjacency": True,
        },
        "bridge_to_informatics": {
            "preferred_tracks": ["core_rn_oregon", "frontier_ecosystem"],
            "preferred_rn_leverage_types": ["implementation", "informatics", "research", "product_clinical"],
            "horizon_preference": "post_reinstatement",
            "relocation_preference": "avoid_likely",
        },
        "frontier_transition": {
            "preferred_tracks": ["frontier_ecosystem"],
            "preferred_geo_scopes": ["pacific_northwest", "west_coast", "remote_us"],
            "preferred_rn_leverage_types": [
                "implementation",
                "clinical_success",
                "informatics",
                "product_clinical",
            ],
            "horizon_preference": "post_reinstatement",
            "relocation_preference": "avoid_likely",
            "prefer_remote_frontier": True,
        },
    },
    "_example_entry": {
        "active_profile": "bridge_to_informatics",
        "default_preferences": {
            "preferred_geo_scopes": ["oregon", "pacific_northwest"],
            "preferred_rn_leverage_types": ["implementation", "informatics"],
            "preferred_tracks": ["core_rn_oregon", "frontier_ecosystem"],
            "horizon_preference": "post_reinstatement",
            "relocation_preference": "avoid_likely",
            "focus_icu_adjacency": True,
            "prefer_remote_frontier": True,
            "profile_weight_overrides": {"icu_bonus": 12, "remote_frontier_bonus": 14},
        },
        "profile_overrides": {
            "frontier_transition": {
                "preferred_geo_scopes": ["west_coast", "remote_us"],
                "preferred_rn_leverage_types": ["clinical_success", "product_clinical", "implementation"],
                "relocation_preference": "flexible",
            }
        },
    },
}


PROFILE_NAMES = ("oregon_now", "bridge_to_informatics", "frontier_transition")
EMPLOYER_INTEREST_LEVELS = {"none", "low", "medium", "high", "frontier"}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_json(path: Path, fallback: dict[str, object]) -> dict[str, object]:
    if not path.exists():
        return deepcopy(fallback)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return deepcopy(fallback)
    return payload if isinstance(payload, dict) else deepcopy(fallback)


def _backfill_template(existing: object, template: object) -> object:
    if isinstance(existing, dict) and isinstance(template, dict):
        merged = dict(existing)
        for key, value in template.items():
            if key not in merged:
                merged[key] = deepcopy(value)
            elif isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = _backfill_template(merged[key], value)
        return merged
    return existing


def _ensure_json_file(path: Path, payload: dict[str, object]) -> None:
    if not path.exists():
        _write_json(path, payload)
        return
    existing = _load_json(path, payload)
    merged = _backfill_template(existing, payload)
    if merged != existing:
        _write_json(path, merged)


def ensure_manual_files(root: Path) -> None:
    manual_dir = repo_path(root, "data", "manual")
    manual_dir.mkdir(parents=True, exist_ok=True)
    _ensure_json_file(manual_dir / "saved_leads.json", DEFAULT_SAVED_LEADS)
    _ensure_json_file(manual_dir / "employer_notes.json", DEFAULT_EMPLOYER_NOTES)
    _ensure_json_file(manual_dir / "profile_preferences.json", DEFAULT_PROFILE_PREFERENCES)


def _normalized_text_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = [clean_text(str(item)) for item in values]
    return [item for item in cleaned if item]


def _normalize_interest_level(value: object) -> str:
    normalized = clean_text(str(value)).lower()
    return normalized if normalized in EMPLOYER_INTEREST_LEVELS else "none"


def _normalize_profile_preferences(
    preferences: dict[str, object],
    employer_overrides: dict[str, dict[str, object]],
) -> dict[str, object]:
    merged = _backfill_template(preferences, DEFAULT_PROFILE_PREFERENCES)
    if not isinstance(merged, dict):
        merged = deepcopy(DEFAULT_PROFILE_PREFERENCES)

    default_preferences = merged.get("default_preferences", {})
    if not isinstance(default_preferences, dict):
        default_preferences = deepcopy(DEFAULT_PROFILE_PREFERENCES["default_preferences"])
    merged["default_preferences"] = _backfill_template(
        default_preferences,
        DEFAULT_PROFILE_PREFERENCES["default_preferences"],
    )

    overrides = merged.get("profile_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    for profile_name in PROFILE_NAMES:
        current = overrides.get(profile_name, {})
        overrides[profile_name] = _backfill_template(
            current if isinstance(current, dict) else {},
            DEFAULT_PROFILE_PREFERENCES["profile_overrides"][profile_name],
        )
    merged["profile_overrides"] = overrides

    merged["employer_interest_overrides"] = {
        company: _normalize_interest_level(payload.get("employer_interest_level"))
        for company, payload in employer_overrides.items()
    }
    return merged


def load_manual_state(root: Path) -> dict[str, object]:
    ensure_manual_files(root)
    manual_dir = repo_path(root, "data", "manual")
    saved = _load_json(manual_dir / "saved_leads.json", DEFAULT_SAVED_LEADS)
    employers = _load_json(manual_dir / "employer_notes.json", DEFAULT_EMPLOYER_NOTES)
    preferences = _load_json(manual_dir / "profile_preferences.json", DEFAULT_PROFILE_PREFERENCES)

    lead_overrides = {
        clean_text(str(item.get("lead_key", ""))): item
        for item in saved.get("lead_overrides", [])
        if isinstance(item, dict) and clean_text(str(item.get("lead_key", "")))
    }
    employer_overrides = {
        clean_text(str(item.get("company") or item.get("employer_name") or "")).lower(): item
        for item in employers.get("employers", [])
        if isinstance(item, dict) and clean_text(str(item.get("company") or item.get("employer_name") or ""))
    }
    normalized_preferences = _normalize_profile_preferences(preferences, employer_overrides)

    return {
        "lead_overrides": lead_overrides,
        "employer_overrides": employer_overrides,
        "profile_preferences": normalized_preferences,
        "raw_saved_leads": saved,
        "raw_employer_notes": employers,
    }


def apply_lead_curation(leads: list[OpportunityLead], manual_state: dict[str, object]) -> dict[str, int]:
    overrides = manual_state.get("lead_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}

    saved_count = 0
    starred_count = 0
    dismissed_count = 0
    pinned_count = 0
    for lead in leads:
        override = overrides.get(lead.lead_key)
        if not isinstance(override, dict):
            continue

        lead.starred = bool(override.get("starred", False))
        lead.saved = bool(override.get("saved", False) or lead.starred)
        lead.dismissed = bool(override.get("dismissed", False) or override.get("hidden", False))
        lead.pinned_reason = clean_text(str(override.get("pinned_reason") or override.get("pin_reason") or ""))

        note_bits = []
        note = clean_text(str(override.get("note", "")))
        if note:
            note_bits.append(note)
        note_bits.extend(_normalized_text_list(override.get("notes", [])))
        if lead.pinned_reason:
            note_bits.append(f"Pinned: {lead.pinned_reason}")
        bootstrap_group = clean_text(str(override.get("bootstrap_group", "")))
        if bootstrap_group:
            note_bits.append(f"Bootstrap group: {bootstrap_group.replace('_', ' ')}")
        lead.manual_notes = dedupe_text_list(note_bits)

        if lead.starred:
            starred_count += 1
            if "Starred" not in lead.tags:
                lead.tags.insert(0, "Starred")
        if lead.saved:
            saved_count += 1
            if "Saved" not in lead.tags:
                lead.tags.append("Saved")
        if lead.dismissed:
            dismissed_count += 1
        if lead.pinned_reason:
            pinned_count += 1

    return {
        "saved_leads": saved_count,
        "starred_leads": starred_count,
        "dismissed_leads": dismissed_count,
        "pinned_leads": pinned_count,
    }


def apply_employer_curation(employers: list[EmployerRollup], manual_state: dict[str, object]) -> dict[str, int]:
    overrides = manual_state.get("employer_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}

    starred = 0
    high_interest = 0
    for employer in employers:
        override = overrides.get(employer.employer_key)
        if not isinstance(override, dict):
            continue

        employer.manual_starred = bool(override.get("starred", False) or override.get("interesting", False))
        employer.manual_interest_level = _normalize_interest_level(override.get("employer_interest_level"))
        employer.pinned_reason = clean_text(str(override.get("pinned_reason") or override.get("pin_reason") or ""))

        note_bits = []
        note = clean_text(str(override.get("note", "")))
        if note:
            note_bits.append(note)
        note_bits.extend(_normalized_text_list(override.get("notes", [])))
        if employer.pinned_reason:
            note_bits.append(f"Pinned: {employer.pinned_reason}")
        employer.manual_notes = dedupe_text_list(note_bits)

        if employer.manual_starred:
            starred += 1
            employer.employer_score += 20
            if "starred_employer" not in employer.employer_tags:
                employer.employer_tags.insert(0, "starred_employer")
            if "You manually marked this employer as interesting." not in employer.why_it_matters:
                employer.why_it_matters.insert(0, "You manually marked this employer as interesting.")

        if employer.manual_interest_level == "high":
            high_interest += 1
            employer.employer_score += 16
            if "manual_interest_high" not in employer.employer_tags:
                employer.employer_tags.insert(0, "manual_interest_high")
            employer.why_it_matters.insert(0, "Manual interest level is high.")
        elif employer.manual_interest_level == "medium":
            employer.employer_score += 8
            if "manual_interest_medium" not in employer.employer_tags:
                employer.employer_tags.insert(0, "manual_interest_medium")
        elif employer.manual_interest_level == "frontier":
            employer.employer_score += 10
            if "manual_interest_frontier" not in employer.employer_tags:
                employer.employer_tags.insert(0, "manual_interest_frontier")
            employer.why_it_matters.insert(0, "Manual interest level is frontier-focused.")
        elif employer.manual_interest_level == "low":
            employer.employer_score -= 8
            if "manual_interest_low" not in employer.employer_tags:
                employer.employer_tags.append("manual_interest_low")

        employer.employer_tags = dedupe_text_list(employer.employer_tags)
        employer.why_it_matters = dedupe_text_list(employer.why_it_matters)[:6]

    employers.sort(key=lambda employer: (-employer.employer_score, employer.employer_name.lower()))
    return {"starred_employers": starred, "high_interest_employers": high_interest}
