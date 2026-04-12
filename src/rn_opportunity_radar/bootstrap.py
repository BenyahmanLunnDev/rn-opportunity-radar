from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from rn_opportunity_radar.employers import build_employer_rollups
from rn_opportunity_radar.manual import (
    DEFAULT_EMPLOYER_NOTES,
    DEFAULT_PROFILE_PREFERENCES,
    DEFAULT_SAVED_LEADS,
    apply_employer_curation,
    apply_lead_curation,
    ensure_manual_files,
    load_manual_state,
)
from rn_opportunity_radar.models import EmployerRollup, OpportunityLead
from rn_opportunity_radar.persistence import is_kept_lead
from rn_opportunity_radar.profiles import apply_profile_overlays, sorted_leads_for_profile
from rn_opportunity_radar.utils import repo_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap manual personalization files from current live data.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing manual entries instead of merging.")
    parser.add_argument("--limit", type=int, default=10, help="Per-group lead and employer limit.")
    return parser.parse_args()


def _load_json(path: Path, fallback: dict[str, object]) -> dict[str, object]:
    if not path.exists():
        return deepcopy(fallback)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return deepcopy(fallback)
    return payload if isinstance(payload, dict) else deepcopy(fallback)


def _load_leads(path: Path, key: str) -> list[OpportunityLead]:
    payload = _load_json(path, {key: []})
    raw_leads = payload.get(key, [])
    if not isinstance(raw_leads, list):
        return []
    return [OpportunityLead.from_dict(item) for item in raw_leads if isinstance(item, dict)]


def _load_current_leads(root: Path) -> list[OpportunityLead]:
    jobs = _load_leads(repo_path(root, "data", "current", "jobs.json"), "jobs")
    signals = _load_leads(repo_path(root, "data", "current", "signals.json"), "signals")
    return [*jobs, *signals]


def _load_current_employers(root: Path) -> list[EmployerRollup]:
    payload = _load_json(repo_path(root, "data", "current", "employers.json"), {"employers": []})
    raw_employers = payload.get("employers", [])
    if not isinstance(raw_employers, list):
        return []
    return [EmployerRollup.from_dict(item) for item in raw_employers if isinstance(item, dict)]


def _lead_entry(lead: OpportunityLead, *, group: str, rank: int) -> dict[str, object]:
    pinned_reason_map = {
        "top_core_leads": "Bootstrap core pick worth reviewing now.",
        "top_bridge_leads": "Bootstrap bridge bet for informatics movement.",
        "top_frontier_targets": "Bootstrap frontier target worth tracking after reinstatement.",
    }
    return {
        "lead_key": lead.lead_key,
        "title_hint": lead.title,
        "company_hint": lead.company,
        "starred": rank <= 1,
        "saved": True,
        "dismissed": False,
        "bootstrap_group": group,
        "bootstrap_rank": rank,
        "pinned_reason": pinned_reason_map.get(group, "Bootstrap pick to review."),
        "note": f"Generated from current live data for the {group.replace('_', ' ')} starter set.",
        "notes": [
            f"{lead.track.replace('_', ' ')} · {lead.bucket.replace('_', ' ')}",
            f"Lens scores: Oregon {lead.profile_scores.get('oregon_now', lead.score)}, Bridge {lead.profile_scores.get('bridge_to_informatics', lead.score)}, Frontier {lead.profile_scores.get('frontier_transition', lead.score)}",
        ],
    }


def _employer_entry(employer: EmployerRollup, *, rank: int) -> dict[str, object]:
    if employer.appears_in_both_tracks or "high_priority_employer" in employer.employer_tags:
        interest_level = "high"
    elif "frontier_employer" in employer.employer_tags:
        interest_level = "frontier"
    else:
        interest_level = "medium"

    return {
        "company": employer.employer_name,
        "starred": rank <= 3,
        "employer_interest_level": interest_level,
        "pinned_reason": "Bootstrap employer dossier worth tracking.",
        "note": "Generated from the current employer leaderboard as a starter dossier.",
        "notes": employer.why_it_matters[:2],
    }


def _merge_unique(
    existing: list[dict[str, object]],
    generated: list[dict[str, object]],
    *,
    key_name: str,
    overwrite: bool,
) -> list[dict[str, object]]:
    if overwrite:
        return generated

    merged = []
    seen: set[str] = set()
    for item in existing:
        key = str(item.get(key_name, "")).strip().lower()
        if not key:
            continue
        seen.add(key)
        merged.append(item)
    for item in generated:
        key = str(item.get(key_name, "")).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _merge_profile_preferences(
    payload: dict[str, object],
    employers: list[EmployerRollup],
    *,
    overwrite: bool,
) -> dict[str, object]:
    if overwrite:
        merged = deepcopy(DEFAULT_PROFILE_PREFERENCES)
    else:
        merged = deepcopy(DEFAULT_PROFILE_PREFERENCES)
        merged.update(payload)

    default_preferences = merged.get("default_preferences", {})
    if not isinstance(default_preferences, dict):
        default_preferences = deepcopy(DEFAULT_PROFILE_PREFERENCES["default_preferences"])

    if overwrite or not default_preferences.get("preferred_employers"):
        default_preferences["preferred_employers"] = [employer.employer_name for employer in employers[:3]]
    if overwrite or not merged.get("active_profile"):
        merged["active_profile"] = "oregon_now"

    merged["default_preferences"] = default_preferences
    return merged


def bootstrap_manual_files(root: Path, *, overwrite: bool = False, limit: int = 10) -> dict[str, int]:
    ensure_manual_files(root)

    all_leads = _load_current_leads(root)
    if not all_leads:
        raise FileNotFoundError("No current jobs/signals data found. Run the radar once before bootstrapping.")

    manual_state = load_manual_state(root)
    apply_lead_curation(all_leads, manual_state)
    apply_profile_overlays(all_leads, manual_state["profile_preferences"])
    employers = _load_current_employers(root)
    if not employers:
        employers = build_employer_rollups("", all_leads)
    apply_employer_curation(employers, manual_state)

    active_jobs = [lead for lead in all_leads if lead.lead_type == "job" and lead.status == "active" and is_kept_lead(lead) and not lead.dismissed]
    top_core = [
        lead
        for lead in sorted_leads_for_profile(active_jobs, "oregon_now")
        if lead.track == "core_rn_oregon" and lead.bucket in {"priority", "bridge"}
    ][:limit]
    top_bridge = [
        lead
        for lead in sorted_leads_for_profile(active_jobs, "bridge_to_informatics")
        if lead.rn_leverage_type in {"implementation", "informatics", "research", "product_clinical", "clinical_success"}
        or lead.bucket == "bridge"
    ][:limit]
    top_frontier = [
        lead
        for lead in sorted_leads_for_profile(active_jobs, "frontier_transition")
        if lead.track == "frontier_ecosystem" and lead.bucket == "target"
    ][:limit]

    generated_leads = []
    for group_name, group_leads in (
        ("top_core_leads", top_core),
        ("top_bridge_leads", top_bridge),
        ("top_frontier_targets", top_frontier),
    ):
        for rank, lead in enumerate(group_leads, start=1):
            generated_leads.append(_lead_entry(lead, group=group_name, rank=rank))

    generated_employers = [
        _employer_entry(employer, rank=index)
        for index, employer in enumerate(employers[:limit], start=1)
    ]

    manual_dir = repo_path(root, "data", "manual")
    saved_payload = _load_json(manual_dir / "saved_leads.json", DEFAULT_SAVED_LEADS)
    employers_payload = _load_json(manual_dir / "employer_notes.json", DEFAULT_EMPLOYER_NOTES)
    preferences_payload = _load_json(manual_dir / "profile_preferences.json", DEFAULT_PROFILE_PREFERENCES)

    existing_leads = saved_payload.get("lead_overrides", [])
    if not isinstance(existing_leads, list):
        existing_leads = []
    existing_employers = employers_payload.get("employers", [])
    if not isinstance(existing_employers, list):
        existing_employers = []

    saved_payload["lead_overrides"] = _merge_unique(
        [item for item in existing_leads if isinstance(item, dict)],
        generated_leads,
        key_name="lead_key",
        overwrite=overwrite,
    )
    employers_payload["employers"] = _merge_unique(
        [item for item in existing_employers if isinstance(item, dict)],
        generated_employers,
        key_name="company",
        overwrite=overwrite,
    )
    merged_preferences = _merge_profile_preferences(preferences_payload, employers, overwrite=overwrite)

    repo_path(root, "data", "manual").mkdir(parents=True, exist_ok=True)
    (manual_dir / "saved_leads.json").write_text(json.dumps(saved_payload, indent=2), encoding="utf-8")
    (manual_dir / "employer_notes.json").write_text(json.dumps(employers_payload, indent=2), encoding="utf-8")
    (manual_dir / "profile_preferences.json").write_text(json.dumps(merged_preferences, indent=2), encoding="utf-8")

    return {
        "lead_overrides_written": len(saved_payload["lead_overrides"]),
        "employer_notes_written": len(employers_payload["employers"]),
        "preferred_employers_seeded": len(merged_preferences.get("default_preferences", {}).get("preferred_employers", [])),
    }


def main() -> int:
    args = parse_args()
    summary = bootstrap_manual_files(args.root.resolve(), overwrite=args.overwrite, limit=args.limit)
    print(
        "Bootstrapped manual files:"
        f" leads={summary['lead_overrides_written']},"
        f" employers={summary['employer_notes_written']},"
        f" preferred_employers={summary['preferred_employers_seeded']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
