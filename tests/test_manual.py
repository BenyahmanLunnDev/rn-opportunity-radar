import json

from rn_opportunity_radar.employers import build_employer_rollups
from rn_opportunity_radar.manual import apply_employer_curation, apply_lead_curation, load_manual_state
from rn_opportunity_radar.models import OpportunityLead, SourceReport
from rn_opportunity_radar.profiles import apply_profile_overlays
from rn_opportunity_radar.render import render_index


def test_saved_and_dismissed_manual_curation_affects_rendering(tmp_path) -> None:
    manual_dir = tmp_path / "data" / "manual"
    manual_dir.mkdir(parents=True)
    (manual_dir / "saved_leads.json").write_text(
        json.dumps(
            {
                "lead_overrides": [
                    {"lead_key": "saved-lead", "starred": True, "saved": True, "pin_reason": "strong fit"},
                    {"lead_key": "dismissed-lead", "dismissed": True, "note": "not realistic right now"},
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (manual_dir / "employer_notes.json").write_text(
        json.dumps({"employers": [{"company": "Test Co", "starred": True, "note": "worth tracking"}]}, indent=2),
        encoding="utf-8",
    )
    (manual_dir / "profile_preferences.json").write_text(
        json.dumps({"active_profile": "oregon_now", "profile_overrides": {}}, indent=2),
        encoding="utf-8",
    )

    saved = OpportunityLead(
        lead_key="saved-lead",
        lead_type="job",
        source_key="core",
        source_name="Core Source",
        company="Test Co",
        title="RN ICU",
        detail_url="https://example.com/saved",
        source_url="https://example.com/source",
        score=110,
        bucket="priority",
        track="core_rn_oregon",
        geo_scope="oregon",
        rn_leverage_type="direct_clinical",
    )
    dismissed = OpportunityLead(
        lead_key="dismissed-lead",
        lead_type="job",
        source_key="frontier",
        source_name="Frontier Source",
        company="Test Co",
        title="Clinical Success Manager",
        detail_url="https://example.com/dismissed",
        source_url="https://example.com/frontier",
        score=105,
        bucket="target",
        track="frontier_ecosystem",
        geo_scope="remote_us",
        rn_leverage_type="clinical_success",
    )
    leads = [saved, dismissed]

    manual_state = load_manual_state(tmp_path)
    curation = apply_lead_curation(leads, manual_state)
    apply_profile_overlays(leads, manual_state["profile_preferences"])
    employers = build_employer_rollups("2026-04-11T07:07:00-07:00", leads)
    curation.update(apply_employer_curation(employers, manual_state))

    html = render_index(
        "2026-04-11T07:07:00-07:00",
        leads,
        [],
        [SourceReport(source_key="core", source_name="Core Source", source_url="https://example.com")],
        {
            "healthy_source_count": 1,
            "failed_source_count": 0,
        },
        employers,
        {"summary": {}},
        curation,
        manual_state["profile_preferences"],
    )

    assert "RN ICU" in html
    assert "Clinical Success Manager" not in html
    assert "Saved" in html
    assert "Starred" in html
    assert "Preference boost" in html
    assert "Starred employers" in html


def test_load_manual_state_backfills_editing_notes_into_existing_files(tmp_path) -> None:
    manual_dir = tmp_path / "data" / "manual"
    manual_dir.mkdir(parents=True)
    (manual_dir / "saved_leads.json").write_text(json.dumps({"lead_overrides": []}, indent=2), encoding="utf-8")
    (manual_dir / "employer_notes.json").write_text(json.dumps({"employers": []}, indent=2), encoding="utf-8")
    (manual_dir / "profile_preferences.json").write_text(
        json.dumps({"active_profile": "oregon_now", "profile_overrides": {}}, indent=2),
        encoding="utf-8",
    )

    load_manual_state(tmp_path)

    saved_payload = json.loads((manual_dir / "saved_leads.json").read_text(encoding="utf-8"))
    profile_payload = json.loads((manual_dir / "profile_preferences.json").read_text(encoding="utf-8"))

    assert "_editing_notes" in saved_payload
    assert "default_preferences" in profile_payload
