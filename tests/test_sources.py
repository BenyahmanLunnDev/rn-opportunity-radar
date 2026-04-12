from rn_opportunity_radar.models import OpportunityLead
from rn_opportunity_radar.sources import (
    _parse_ashby_job_postings,
    _parse_board_results_page,
    _parse_icims_impression_leads,
    _parse_peacehealth_search_page,
    build_providence_detail_url,
    dedupe_leads,
    dedupe_leads_with_stats,
    normalize_location,
)


def test_build_providence_detail_url_uses_city_state_slug_and_guid() -> None:
    job = {
        "guid": "ABC123",
        "title_slug": "rn-critical-care-night",
        "city_exact": "Portland",
        "state_short": "OR",
    }
    url = build_providence_detail_url(job, "providence-oregon.jobs")
    assert url == "https://providence-oregon.jobs/portland-or/rn-critical-care-night/ABC123/job/"


def test_dedupe_prefers_higher_priority_source() -> None:
    board = OpportunityLead(
        lead_key="board",
        lead_type="job",
        source_key="board",
        source_name="Specialty Board",
        company="OHSU",
        title="RN Case Manager",
        detail_url="https://example.com/job",
        source_url="https://example.com/board",
        location="Portland, OR",
        description="Short description.",
        metadata={"source_priority": 70},
    )
    official = OpportunityLead(
        lead_key="official",
        lead_type="job",
        source_key="official",
        source_name="Official OHSU Source",
        company="OHSU",
        title="RN Case Manager",
        detail_url="https://example.com/job",
        source_url="https://example.com/official",
        location="Portland, OR",
        description="Longer official description that should win the dedupe pass.",
        metadata={"source_priority": 100},
    )

    deduped = dedupe_leads([board, official])
    assert len(deduped) == 1
    assert deduped[0].source_key == "official"


def test_dedupe_tracks_board_removal_when_official_copy_wins() -> None:
    board = OpportunityLead(
        lead_key="board",
        lead_type="job",
        source_key="ania_jobs",
        source_name="ANIA Jobs Board",
        company="PeaceHealth",
        title="RN - ICU",
        detail_url="https://jobs.ania.org/job/rn-icu/123/",
        source_url="https://jobs.ania.org/jobseeker/search/results/",
        location="Springfield, OR",
        description="Board copy.",
        metadata={"source_priority": 76},
    )
    official = OpportunityLead(
        lead_key="official",
        lead_type="job",
        source_key="peacehealth_oregon_nursing",
        source_name="PeaceHealth Oregon Nursing Jobs",
        company="PeaceHealth",
        title="RN - ICU",
        detail_url="https://careers.peacehealth.org/jobs/123-rn-icu",
        source_url="https://www.peacehealth.org/job-opportunities/nursing-jobs/nursing-jobs-oregon",
        location="Springfield, OR",
        description="Official employer copy with better detail.",
        metadata={"source_priority": 94, "requisition_id": "123"},
    )

    deduped, stats = dedupe_leads_with_stats([board, official])
    assert len(deduped) == 1
    assert deduped[0].source_key == "peacehealth_oregon_nursing"
    assert stats["ania_jobs"] == 1


def test_dedupe_does_not_merge_same_listing_across_tracks() -> None:
    core = OpportunityLead(
        lead_key="core",
        lead_type="job",
        source_key="providence_oregon_nursing",
        source_name="Providence Oregon Nursing Jobs",
        company="Providence",
        title="Clinical Informatics RN",
        detail_url="https://example.com/job/123",
        source_url="https://example.com/core",
        location="Portland, OR",
        track="core_rn_oregon",
        metadata={"source_priority": 100},
    )
    frontier = OpportunityLead(
        lead_key="frontier",
        lead_type="job",
        source_key="viz_ai_frontier_roles",
        source_name="Viz.ai Frontier Roles",
        company="Providence",
        title="Clinical Informatics RN",
        detail_url="https://example.com/job/123",
        source_url="https://example.com/frontier",
        location="Portland, OR",
        track="frontier_ecosystem",
        metadata={"source_priority": 88},
    )

    deduped = dedupe_leads([core, frontier])

    assert len(deduped) == 2
    assert {lead.track for lead in deduped} == {"core_rn_oregon", "frontier_ecosystem"}


def test_normalize_location_simplifies_us_prefixed_location() -> None:
    assert normalize_location("US-OR-Portland") == "portland"


def test_parse_peacehealth_search_page_extracts_oregon_jobs_and_next_page() -> None:
    html = """
    <div class="jobs-section__item page-section-small">
      <div class="row">
        <div class="large-5 columns">
          <h5><a href="https://careers.peacehealth.org/jobs/17602912-rn-icu">RN - ICU</a> <sup>NEW</sup></h5>
        </div>
        <div class="large-2 columns"><span>Work Type: </span><span>Part Time</span></div>
        <div class="large-1 columns"><span>Shift: </span><span>Night</span></div>
        <div class="large-2 columns"><span>Benefit Eligibility: </span><span>Part-time benefits</span></div>
        <div class="large-2 columns">Springfield, OR</div>
      </div>
    </div>
    <a href="https://careers.peacehealth.org/search/jobs/in/or-oregon?brand=nursing&cfml3=Nursing&page=2">View more jobs</a>
    """

    jobs, next_pages = _parse_peacehealth_search_page(html)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "RN - ICU"
    assert jobs[0]["location"] == "Springfield, OR"
    assert jobs[0]["work_type"] == "Part Time"
    assert jobs[0]["shift"] == "Night"
    assert next_pages == [
        "https://careers.peacehealth.org/search/jobs/in/or-oregon?brand=nursing&cfml3=Nursing&page=2"
    ]


def test_parse_board_results_page_skips_promos_and_extracts_hidden_fields() -> None:
    html = """
    <div class="job-main-data candidate-products-promotion-data">
      <a href="/pricing/">Promo</a>
    </div>
    <div class="job-main-data">
      <input type="hidden" name="job_id" value="83176687" />
      <input type="hidden" name="job_Position" value="Informatics Specialist I - Surgical Services" />
      <input type="hidden" name="job_company" value="Presbyterian Healthcare Services" />
      <input type="hidden" name="job_Location" value="Santa Fe, New Mexico, United States" />
      <input type="hidden" name="job_source" value="vnet" />
      <input type="hidden" name="job_upgrades" value="Preferred|" />
      <a href="https://jobs.amia.org/job/informatics-specialist-i-surgical-services/83176687/">Informatics Specialist I - Surgical Services</a>
    </div>
    """

    jobs = _parse_board_results_page(html)

    assert jobs == [
        {
            "job_id": "83176687",
            "title": "Informatics Specialist I - Surgical Services",
            "detail_url": "https://jobs.amia.org/job/informatics-specialist-i-surgical-services/83176687/",
            "company": "Presbyterian Healthcare Services",
            "location": "Santa Fe, New Mexico, United States",
            "job_source": "vnet",
            "job_upgrades": "Preferred",
        }
    ]


def test_parse_icims_impression_leads_extracts_legacy_result_metadata() -> None:
    html = """
    <script>
    var jobImpressions = [{"positionType":"Regular Full-Time","location":{"city":"SPRINGFIELD","state":"OR"},"company":"RiverBend campus","idRaw":47224,"title":"RN Staff Nurse - ICU","category":"Critical Care","postedDate":"2026-04-10"}];
    </script>
    <a class="iCIMS_Anchor" href="https://nursing-lhs.icims.com/jobs/47224/rn-staff-nurse---icu/job?in_iframe=1">
      <h3>RN Staff Nurse - ICU</h3>
    </a>
    """

    leads = _parse_icims_impression_leads(
        html,
        "legacy_nursing",
        {
            "name": "Legacy Nursing Careers",
            "company": "Legacy Health",
            "url": "https://www.legacyhealth.org/for-health-professionals/careers/nursing",
            "source_priority": 98,
            "source_context": "official Legacy nursing careers page",
        },
    )

    assert len(leads) == 1
    assert leads[0].title == "RN Staff Nurse - ICU"
    assert leads[0].company == "RiverBend campus"
    assert leads[0].location == "SPRINGFIELD, OR"
    assert leads[0].metadata["requisition_id"] == "47224"


def test_parse_ashby_job_postings_extracts_embedded_jobs() -> None:
    html = """
    <script>
      window.__ASHBY_JOB_BOARD_DATA__ = {
        "jobPostings": [
          {
            "jobId": "abc123",
            "title": "Care Pathway Lead - Cardiology",
            "locationName": "Remote, United States",
            "employmentType": "Full-time",
            "workplaceType": "Remote",
            "isListed": true
          }
        ]
      };
    </script>
    """

    jobs = _parse_ashby_job_postings(html)

    assert len(jobs) == 1
    assert jobs[0]["jobId"] == "abc123"
    assert jobs[0]["title"] == "Care Pathway Lead - Cardiology"
