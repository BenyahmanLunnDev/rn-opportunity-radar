from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from rn_opportunity_radar.models import EmployerRollup, OpportunityLead
from rn_opportunity_radar.persistence import is_kept_lead
from rn_opportunity_radar.utils import clean_text, dedupe_text_list, repo_path


def _employer_key(company: str) -> str:
    return clean_text(company).lower()


def _lead_highlight(lead: OpportunityLead) -> dict[str, object]:
    return {
        "lead_key": lead.lead_key,
        "title": lead.title,
        "bucket": lead.bucket,
        "track": lead.track,
        "detail_url": lead.detail_url,
        "profile_scores": lead.profile_scores,
        "starred": lead.starred,
        "saved": lead.saved,
    }


def _employer_tags(bucket_counts: Counter[str], tracks: set[str], innovation_signal: bool) -> list[str]:
    tags: list[str] = []
    if bucket_counts.get("priority", 0) > 0 or (bucket_counts.get("target", 0) > 0 and len(tracks) > 1):
        tags.append("high_priority_employer")
    if bucket_counts.get("bridge", 0) > 0 or bucket_counts.get("target", 0) > 0:
        tags.append("bridge_employer")
    if "frontier_ecosystem" in tracks and (bucket_counts.get("target", 0) > 0 or bucket_counts.get("strategic_watch", 0) > 0):
        tags.append("frontier_employer")
    if tracks == {"frontier_ecosystem"} and innovation_signal:
        tags.append("ecosystem_only")
    return tags


def _why_employer_matters(
    employer_name: str,
    bucket_counts: Counter[str],
    tracks: set[str],
    innovation_signal: bool,
    leverage_counts: Counter[str],
) -> list[str]:
    reasons: list[str] = []
    if "core_rn_oregon" in tracks and bucket_counts.get("priority", 0) > 0:
        reasons.append(f"{employer_name} has actionable Oregon-now opportunities.")
    if bucket_counts.get("bridge", 0) > 0:
        reasons.append(f"{employer_name} is producing bridge-role inventory.")
    if "frontier_ecosystem" in tracks and bucket_counts.get("target", 0) > 0:
        reasons.append(f"{employer_name} has real frontier targets, not just ecosystem noise.")
    if len(tracks) > 1:
        reasons.append(f"{employer_name} appears across both core and frontier rails.")
    if innovation_signal:
        reasons.append(f"{employer_name} has visible innovation or ecosystem signal presence.")
    if leverage_counts:
        top_leverage = leverage_counts.most_common(1)[0][0].replace("_", " ")
        reasons.append(f"Strongest RN leverage signal: {top_leverage}.")
    return dedupe_text_list(reasons)[:5]


def build_employer_rollups(
    generated_at: str,
    leads: list[OpportunityLead],
) -> list[EmployerRollup]:
    active_kept = [lead for lead in leads if lead.status == "active" and is_kept_lead(lead) and not lead.dismissed]
    grouped: dict[str, list[OpportunityLead]] = defaultdict(list)
    for lead in active_kept:
        grouped[_employer_key(lead.company)].append(lead)

    employers: list[EmployerRollup] = []
    for employer_key, employer_leads in grouped.items():
        if not employer_key:
            continue
        employer_name = employer_leads[0].company
        tracks = {lead.track for lead in employer_leads}
        bucket_counts = Counter(lead.bucket for lead in employer_leads)
        track_counts = Counter(lead.track for lead in employer_leads)
        lead_type_counts = Counter(lead.lead_type for lead in employer_leads)
        location_counts = Counter(clean_text(lead.location) for lead in employer_leads if clean_text(lead.location))
        leverage_counts = Counter(lead.rn_leverage_type for lead in employer_leads if lead.rn_leverage_type)
        innovation_signal = any(lead.lead_type == "signal" for lead in employer_leads)
        strongest_leads = sorted(
            employer_leads,
            key=lambda lead: (
                -lead.profile_scores.get("oregon_now", lead.score),
                -lead.profile_scores.get("frontier_transition", lead.score),
                -lead.score,
                lead.title.lower(),
            ),
        )[:4]
        profile_highlights = {
            "oregon_now": max((lead.profile_scores.get("oregon_now", 0) for lead in employer_leads), default=0),
            "bridge_to_informatics": max((lead.profile_scores.get("bridge_to_informatics", 0) for lead in employer_leads), default=0),
            "frontier_transition": max((lead.profile_scores.get("frontier_transition", 0) for lead in employer_leads), default=0),
        }
        employer_score = (
            bucket_counts.get("priority", 0) * 16
            + bucket_counts.get("bridge", 0) * 10
            + bucket_counts.get("target", 0) * 14
            + bucket_counts.get("strategic_watch", 0) * 8
            + bucket_counts.get("ecosystem_signal", 0) * 4
            + (18 if len(tracks) > 1 else 0)
            + (10 if innovation_signal else 0)
        )

        rollup = EmployerRollup(
            employer_key=employer_key,
            employer_name=employer_name,
            tracks=sorted(tracks),
            bucket_counts=dict(bucket_counts),
            track_counts=dict(track_counts),
            lead_type_counts=dict(lead_type_counts),
            strongest_lead_types=[item[0] for item in lead_type_counts.most_common(3)],
            locations_seen=[item[0] for item in location_counts.most_common(6)],
            appears_in_both_tracks=len(tracks) > 1,
            innovation_signal_presence=innovation_signal,
            rn_leverage_types=[item[0] for item in leverage_counts.most_common(4)],
            employer_tags=_employer_tags(bucket_counts, tracks, innovation_signal),
            why_it_matters=_why_employer_matters(employer_name, bucket_counts, tracks, innovation_signal, leverage_counts),
            highlighted_leads=[_lead_highlight(lead) for lead in strongest_leads],
            profile_highlights=profile_highlights,
            employer_score=employer_score,
        )
        employers.append(rollup)

    return sorted(
        employers,
        key=lambda employer: (-employer.employer_score, employer.employer_name.lower()),
    )


def save_employer_rollups(root: Path, generated_at: str, employers: list[EmployerRollup]) -> None:
    payload = {
        "generated_at": generated_at,
        "employers": [employer.to_dict() for employer in employers],
    }
    repo_path(root, "data", "current").mkdir(parents=True, exist_ok=True)
    repo_path(root, "data", "current", "employers.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_previous_employer_rollups(root: Path) -> dict[str, EmployerRollup]:
    path = repo_path(root, "data", "current", "employers.json")
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    employers = [EmployerRollup.from_dict(item) for item in payload.get("employers", [])]
    return {employer.employer_key: employer for employer in employers}
