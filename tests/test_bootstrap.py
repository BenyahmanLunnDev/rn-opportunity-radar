import json

from rn_opportunity_radar.bootstrap import bootstrap_manual_files
from rn_opportunity_radar.models import OpportunityLead


def _write_current_payloads(root) -> None:
    current_dir = root / "data" / "current"
    current_dir.mkdir(parents=True)

    core_job = OpportunityLead(
        lead_key="core-1",
        lead_type="job",
        source_key="core",
        source_name="Core Source",
        company="Providence",
        title="RN ICU",
        detail_url="https://example.com/core",
        source_url="https://example.com/core-source",
        score=110,
        bucket="priority",
        track="core_rn_oregon",
        geo_scope="oregon",
        rn_leverage_type="direct_clinical",
    )
    frontier_job = OpportunityLead(
        lead_key="frontier-1",
        lead_type="job",
        source_key="frontier",
        source_name="Frontier Source",
        company="Viz.ai",
        title="Clinical Success Manager",
        detail_url="https://example.com/frontier",
        source_url="https://example.com/frontier-source",
        score=124,
        bucket="target",
        track="frontier_ecosystem",
        geo_scope="remote_us",
        rn_leverage_type="clinical_success",
    )
    bridge_job = OpportunityLead(
        lead_key="bridge-1",
        lead_type="job",
        source_key="bridge",
        source_name="Bridge Source",
        company="OHSU",
        title="Clinical Informatics Specialist",
        detail_url="https://example.com/bridge",
        source_url="https://example.com/bridge-source",
        score=98,
        bucket="bridge",
        track="core_rn_oregon",
        geo_scope="oregon",
        rn_leverage_type="informatics",
    )

    (current_dir / "jobs.json").write_text(
        json.dumps({"jobs": [core_job.to_dict(), frontier_job.to_dict(), bridge_job.to_dict()]}, indent=2),
        encoding="utf-8",
    )
    (current_dir / "signals.json").write_text(json.dumps({"signals": []}, indent=2), encoding="utf-8")


def test_bootstrap_manual_files_merges_without_clobbering_existing_entries(tmp_path) -> None:
    _write_current_payloads(tmp_path)
    manual_dir = tmp_path / "data" / "manual"
    manual_dir.mkdir(parents=True)
    (manual_dir / "saved_leads.json").write_text(
        json.dumps(
            {
                "lead_overrides": [
                    {
                        "lead_key": "core-1",
                        "saved": True,
                        "note": "keep my original note",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (manual_dir / "employer_notes.json").write_text(
        json.dumps(
            {
                "employers": [
                    {
                        "company": "Providence",
                        "starred": True,
                        "note": "existing employer note",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (manual_dir / "profile_preferences.json").write_text(
        json.dumps(
            {
                "active_profile": "bridge_to_informatics",
                "default_preferences": {"preferred_employers": ["Providence"]},
                "profile_overrides": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = bootstrap_manual_files(tmp_path, overwrite=False, limit=2)

    saved_payload = json.loads((manual_dir / "saved_leads.json").read_text(encoding="utf-8"))
    employer_payload = json.loads((manual_dir / "employer_notes.json").read_text(encoding="utf-8"))
    profile_payload = json.loads((manual_dir / "profile_preferences.json").read_text(encoding="utf-8"))

    existing_lead = next(item for item in saved_payload["lead_overrides"] if item["lead_key"] == "core-1")
    existing_employer = next(item for item in employer_payload["employers"] if item["company"] == "Providence")

    assert existing_lead["note"] == "keep my original note"
    assert existing_employer["note"] == "existing employer note"
    assert profile_payload["active_profile"] == "bridge_to_informatics"
    assert "Providence" in profile_payload["default_preferences"]["preferred_employers"]
    assert summary["lead_overrides_written"] >= 2
    assert summary["employer_notes_written"] >= 2
