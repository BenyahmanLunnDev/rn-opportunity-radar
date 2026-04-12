# RN Opportunity Radar

RN Opportunity Radar is a daily lead-and-signal tracker for Oregon RN opportunities, with extra weight for bridge roles and employers doing real AI, informatics, workflow, research, and digital-care work.

The project follows the same operating model as Donovan LiveWire where that model still makes sense:

- Python package under `src/`
- source-specific scrapers
- `requests` + `BeautifulSoup` first
- lightweight browser fallback only if a source truly needs it
- JSON persistence in `data/current/` and `data/history/`
- static `docs/index.html` output for GitHub Pages
- `docs/latest.json` for downstream automation
- optional RSS feed output
- explainable scoring with human-readable reasons
- stale-source handling so the dashboard can distinguish fresh, stale, and vanished leads

## Scope

This version is built around two kinds of leads:

- `job`: real openings, especially Oregon RN roles and RN-to-informatics bridge roles
- `signal`: AI, informatics, transformation, and career-resource pages that raise employer priority or help identify watch areas

## Current source coverage

Live request-first parsers are included for:

- OHSU nursing / RN iCIMS searches
- Legacy nursing iCIMS search
- Providence Oregon nursing API-backed listings
- Kaiser Northwest / Portland nursing search pages
- OHSU and Providence signal pages

Live browser-backed parsers are included for:

- Oregon Nurse Career Center
- ANIA jobs board
- AMIA jobs board

PeaceHealth is wired for browser fallback, but the current unattended Python Playwright path is still being blocked by Cloudflare challenge pages. The source stays in health reporting so the failure is visible instead of silently faked.

## Local usage

Install the package:

```powershell
python -m pip install -e .[dev]
python -m playwright install chromium
```

Run the pipeline from the repository root:

```powershell
python -m rn_opportunity_radar --root .
```

That will update:

- `data/current/jobs.json`
- `data/current/signals.json`
- `data/current/reports.json`
- `data/current/summary.json`
- `data/history/YYYY-MM-DD.json`
- `docs/index.html`
- `docs/latest.json`
- `docs/feed.xml`

Bootstrap starter manual preferences from current live data without clobbering existing edits:

```powershell
python -m rn_opportunity_radar.bootstrap --root .
```

Replace existing starter entries only if you explicitly want a fresh pass:

```powershell
python -m rn_opportunity_radar.bootstrap --root . --overwrite
```

## Manual personalization

The repo uses hand-editable JSON files under `data/manual/`. Because JSON does not support comments, each file includes `_editing_notes` and example blocks that are safe to leave in place.

### `data/manual/saved_leads.json`

Use this file to star, save, dismiss, or pin individual leads.

```json
{
  "lead_overrides": [
    {
      "lead_key": "ohsu_rn:registered-nurse-icu",
      "title_hint": "RN, ICU",
      "company_hint": "OHSU",
      "starred": true,
      "saved": true,
      "dismissed": false,
      "pinned_reason": "Strong ICU fit and realistic near-term target.",
      "note": "Worth reviewing after reinstatement.",
      "notes": [
        "Would be a strong Oregon-now application target.",
        "Keep visible even if a similar listing rotates out."
      ]
    }
  ]
}
```

Fields:

- `starred`: stronger than `saved`; starred leads get extra visibility in profile picks.
- `saved`: keeps a lead visible in decision-support views.
- `dismissed`: hides a lead from decision views without removing it from collection history.
- `pinned_reason`: short explanation shown in cards and movement views.

### `data/manual/employer_notes.json`

Use this file to mark organizations that deserve more attention.

```json
{
  "employers": [
    {
      "company": "Providence",
      "starred": true,
      "employer_interest_level": "high",
      "pinned_reason": "Strong bridge employer with visible innovation footprint.",
      "note": "Worth tracking across both Oregon and frontier rails.",
      "notes": [
        "Look for workflow, implementation, and clinical systems openings.",
        "Signals here still matter when the exact role is not ready yet."
      ]
    }
  ]
}
```

Fields:

- `employer_interest_level`: `low`, `medium`, `high`, or `frontier`
- `starred`: surfaces the employer more aggressively in dossiers and watch sections
- `pinned_reason`: short explanation shown on employer cards

### `data/manual/profile_preferences.json`

Use this file to influence profile overlay scores without changing the base lead score or bucket.

```json
{
  "active_profile": "oregon_now",
  "default_preferences": {
    "preferred_geo_scopes": ["oregon", "pacific_northwest"],
    "preferred_rn_leverage_types": [
      "direct_clinical",
      "implementation",
      "informatics",
      "product_clinical"
    ],
    "preferred_tracks": ["core_rn_oregon", "frontier_ecosystem"],
    "preferred_employers": ["Providence", "OHSU"],
    "horizon_preference": "post_reinstatement",
    "relocation_preference": "avoid_likely",
    "focus_icu_adjacency": true,
    "prefer_remote_frontier": true,
    "profile_weight_overrides": {
      "icu_bonus": 12,
      "remote_frontier_bonus": 14,
      "relocation_penalty": 14
    }
  },
  "profile_overrides": {
    "frontier_transition": {
      "preferred_geo_scopes": ["west_coast", "remote_us"],
      "preferred_rn_leverage_types": [
        "implementation",
        "clinical_success",
        "product_clinical"
      ],
      "relocation_preference": "avoid_likely"
    }
  }
}
```

Useful fields:

- `preferred_geo_scopes`
- `preferred_rn_leverage_types`
- `preferred_tracks`
- `preferred_employers`
- `horizon_preference`
- `relocation_preference`
- `focus_icu_adjacency`
- `prefer_remote_frontier`
- `profile_weight_overrides`

Recommended workflow:

1. Run the bootstrap helper once.
2. Open `saved_leads.json` and delete anything you know you do not care about.
3. Promote your real favorites to `starred`.
4. Mark employer dossiers with `employer_interest_level`.
5. Tune `profile_preferences.json` until the `Top Picks`, `Bridge Bets`, and `Frontier Bets` sections feel sane.

## GitHub automation

The repository includes a GitHub Actions workflow that:

- runs on a Pacific-time-safe daily schedule
- installs the package with dev dependencies
- runs the test suite on push and manual runs
- executes the radar
- commits refreshed data and docs artifacts
- deploys `docs/` with GitHub Pages

## Deploy and operate

The intended deployment path is:

1. push `main` to GitHub
2. let `.github/workflows/scrape.yml` run
3. publish the static site from the generated `docs/` artifact through GitHub Pages

Notes:

- `docs/` remains the publish output for the dashboard, JSON payload, RSS feed, and audit page.
- `docs/.nojekyll` is included so Pages serves the static files without Jekyll interference.
- Scheduled scraping is handled by the `Daily Radar Refresh` workflow in `.github/workflows/scrape.yml`.
- The workflow is the Pages deployment path; this repo does not need a second deployment stack.

### GitHub Pages setup

The workflow is configured to auto-enable Pages for the repository when possible. If GitHub Pages is still not configured after the first push, enable it in GitHub:

1. open the repository on GitHub
2. go to `Settings` -> `Pages`
3. under `Build and deployment`, set `Source` to `GitHub Actions`

After that, pushes to `main` and scheduled runs can deploy the refreshed `docs/` output automatically.

### Local rerun checklist

```powershell
python -m pip install -e .[dev]
python -m playwright install chromium
python -m pytest
python -m rn_opportunity_radar --root .
```

### Future updates

For future maintenance:

1. edit code or manual files
2. run tests locally
3. rerun the radar
4. commit the refreshed `data/` and `docs/` artifacts
5. push to `main`

## Design direction

The dashboard is intentionally not styled like Donovan LiveWire. For this project the UI direction is a light, clinical-intelligence board: soft paper backgrounds, deep teal/navy structure, restrained alert color, and an editorial scan-first layout that works on desktop and mobile without a frontend framework.
