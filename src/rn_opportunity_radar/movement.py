from __future__ import annotations

import json
from pathlib import Path

from rn_opportunity_radar.models import EmployerRollup, OpportunityLead
from rn_opportunity_radar.persistence import is_kept_lead
from rn_opportunity_radar.utils import repo_path


CORE_BUCKET_RANK = {"discard": 0, "long_shot": 1, "watch": 2, "bridge": 3, "priority": 4}
FRONTIER_BUCKET_RANK = {"low_fit": 0, "ecosystem_signal": 1, "strategic_watch": 2, "target": 3}


def _bucket_rank(lead: OpportunityLead) -> int:
    if lead.track == "frontier_ecosystem":
        return FRONTIER_BUCKET_RANK.get(lead.bucket, -1)
    return CORE_BUCKET_RANK.get(lead.bucket, -1)


def _lead_snapshot(lead: OpportunityLead) -> dict[str, object]:
    return {
        "lead_key": lead.lead_key,
        "track": lead.track,
        "bucket": lead.bucket,
        "title": lead.title,
        "company": lead.company,
        "location": lead.location,
        "detail_url": lead.detail_url,
        "profile_scores": lead.profile_scores,
        "starred": lead.starred,
        "saved": lead.saved,
        "dismissed": lead.dismissed,
        "pinned_reason": lead.pinned_reason,
    }


def _movement_candidate(lead: OpportunityLead) -> bool:
    if not is_kept_lead(lead):
        return False
    if lead.dismissed:
        return False
    if lead.track == "core_rn_oregon":
        return lead.bucket in {"priority", "bridge", "watch"}
    return lead.bucket in {"target", "strategic_watch", "ecosystem_signal"}


def build_movement_summary(
    generated_at: str,
    current_leads: list[OpportunityLead],
    previous_leads: dict[str, OpportunityLead],
    employers: list[EmployerRollup],
    previous_employers: dict[str, EmployerRollup],
) -> dict[str, object]:
    current_active = {lead.lead_key: lead for lead in current_leads if lead.status == "active"}
    previous_active = {key: lead for key, lead in previous_leads.items() if lead.status == "active"}

    new_high_signal = [
        lead
        for key, lead in current_active.items()
        if key not in previous_active and _movement_candidate(lead)
    ]
    promoted = []
    frontier_targets_new = []
    saved_changes = []

    for key, lead in current_active.items():
        if lead.dismissed:
            continue
        previous = previous_active.get(key)
        if previous and _bucket_rank(lead) > _bucket_rank(previous):
            promoted.append(
                {
                    **_lead_snapshot(lead),
                    "from_bucket": previous.bucket,
                    "to_bucket": lead.bucket,
                }
            )
        if lead.track == "frontier_ecosystem" and lead.bucket == "target" and (not previous or previous.bucket != "target"):
            frontier_targets_new.append(_lead_snapshot(lead))
        if lead.saved and previous:
            if previous.status != lead.status or previous.bucket != lead.bucket:
                saved_changes.append(
                    {
                        **_lead_snapshot(lead),
                        "previous_status": previous.status,
                        "current_status": lead.status,
                        "previous_bucket": previous.bucket,
                        "current_bucket": lead.bucket,
                    }
                )

    vanished = [
        {
            **_lead_snapshot(previous),
            "previous_status": previous.status,
            "current_status": "expired",
        }
        for key, previous in previous_active.items()
        if key not in current_active and _movement_candidate(previous)
    ]

    current_employers = {employer.employer_key: employer for employer in employers}
    growing_employers = []
    for key, employer in current_employers.items():
        previous = previous_employers.get(key)
        previous_count = sum(previous.track_counts.values()) if previous else 0
        current_count = sum(employer.track_counts.values())
        if current_count > previous_count:
            growing_employers.append(
                {
                    "employer_name": employer.employer_name,
                    "tracks": employer.tracks,
                    "previous_active_count": previous_count,
                    "current_active_count": current_count,
                    "delta": current_count - previous_count,
                    "employer_tags": employer.employer_tags,
                }
            )

    return {
        "generated_at": generated_at,
        "summary": {
            "new_high_signal_count": len(new_high_signal),
            "promoted_count": len(promoted),
            "vanished_count": len(vanished),
            "growing_employer_count": len(growing_employers),
            "new_frontier_target_count": len(frontier_targets_new),
            "saved_lead_change_count": len(saved_changes),
        },
        "new_high_signal_leads": [_lead_snapshot(lead) for lead in sorted(new_high_signal, key=lambda lead: (-lead.score, lead.title.lower()))[:15]],
        "promoted_leads": sorted(promoted, key=lambda item: (-int(item["profile_scores"].get("oregon_now", 0)), str(item["title"]).lower()))[:15],
        "vanished_leads": sorted(vanished, key=lambda item: (-int(item["profile_scores"].get("oregon_now", 0)), str(item["title"]).lower()))[:15],
        "employers_with_increasing_activity": sorted(growing_employers, key=lambda item: (-int(item["delta"]), str(item["employer_name"]).lower()))[:12],
        "new_frontier_targets": sorted(frontier_targets_new, key=lambda item: (-int(item["profile_scores"].get("frontier_transition", 0)), str(item["title"]).lower()))[:12],
        "saved_lead_changes": sorted(saved_changes, key=lambda item: (-int(item["profile_scores"].get("oregon_now", 0)), str(item["title"]).lower()))[:12],
    }


def save_movement_summary(root: Path, movement: dict[str, object]) -> None:
    repo_path(root, "data", "current").mkdir(parents=True, exist_ok=True)
    repo_path(root, "data", "current", "movement.json").write_text(json.dumps(movement, indent=2), encoding="utf-8")


def load_previous_movement(root: Path) -> dict[str, object]:
    path = repo_path(root, "data", "current", "movement.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
