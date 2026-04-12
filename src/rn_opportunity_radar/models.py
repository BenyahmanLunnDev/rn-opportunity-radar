from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OpportunityLead:
    lead_key: str
    lead_type: str
    source_key: str
    source_name: str
    company: str
    title: str
    detail_url: str
    source_url: str
    location: str = ""
    posted_date: str = ""
    description: str = ""
    source_context: str = ""
    discovered_via: str = ""
    track: str = "core_rn_oregon"
    subtrack: str = "health_system"
    horizon: str = "post_reinstatement"
    geo_scope: str = "oregon"
    rn_leverage_type: str = "direct_clinical"
    relocation_risk: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)
    score: int = 0
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    profile_scores: dict[str, int] = field(default_factory=dict)
    profile_reasons: dict[str, list[str]] = field(default_factory=dict)
    profile_preference_deltas: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    bucket: str = "discard"
    starred: bool = False
    saved: bool = False
    dismissed: bool = False
    pinned_reason: str = ""
    manual_notes: list[str] = field(default_factory=list)
    status: str = "active"
    stale_source: bool = False
    stale_since: str = ""
    first_seen: str = ""
    last_seen: str = ""
    expired_on: str = ""
    seen_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OpportunityLead":
        return cls(**payload)


@dataclass
class SourceReport:
    source_key: str
    source_name: str
    source_url: str
    track: str = "core_rn_oregon"
    source_class: str = "official"
    status: str = "ok"
    total_fetched: int = 0
    total_relevant: int = 0
    deduped_away: int = 0
    used_browser: bool = False
    last_attempt_at: str = ""
    last_success_at: str = ""
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceReport":
        return cls(**payload)


@dataclass
class EmployerRollup:
    employer_key: str
    employer_name: str
    tracks: list[str] = field(default_factory=list)
    bucket_counts: dict[str, int] = field(default_factory=dict)
    track_counts: dict[str, int] = field(default_factory=dict)
    lead_type_counts: dict[str, int] = field(default_factory=dict)
    strongest_lead_types: list[str] = field(default_factory=list)
    locations_seen: list[str] = field(default_factory=list)
    appears_in_both_tracks: bool = False
    innovation_signal_presence: bool = False
    rn_leverage_types: list[str] = field(default_factory=list)
    employer_tags: list[str] = field(default_factory=list)
    why_it_matters: list[str] = field(default_factory=list)
    highlighted_leads: list[dict[str, Any]] = field(default_factory=list)
    profile_highlights: dict[str, int] = field(default_factory=dict)
    manual_starred: bool = False
    manual_interest_level: str = "none"
    pinned_reason: str = ""
    manual_notes: list[str] = field(default_factory=list)
    employer_score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EmployerRollup":
        return cls(**payload)


@dataclass
class RunArtifacts:
    generated_at: str
    timezone: str
    jobs: list[OpportunityLead]
    signals: list[OpportunityLead]
    reports: list[SourceReport]
    summary: dict[str, Any]
    employers: list[EmployerRollup] = field(default_factory=list)
