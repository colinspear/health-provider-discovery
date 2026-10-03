"""End-to-end engine test against recorded-shape API responses (no network)."""

import json

import httpx
import pytest
import respx

from finder.config import NetworkSettings, Settings
from finder.engine import Engine
from finder.geo import parse_batch_response, parse_gazetteer

GAZ = (
    "GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG                                                                                                               \n"
    "60614\t1\t1\t1\t1\t41.922695\t-87.652774\n"
    "60657\t1\t1\t1\t1\t41.940258\t-87.653057\n"
    "90210\t1\t1\t1\t1\t34.100517\t-118.414630\n"
)


def nppes_result(npi, first, last, zip5, code="207Q00000X", desc="Family Medicine", status="A"):
    return {
        "number": npi,
        "enumeration_type": "NPI-1",
        "basic": {"first_name": first.upper(), "last_name": last.upper(), "credential": "M.D.", "status": status},
        "addresses": [
            {"address_purpose": "MAILING", "address_1": "PO BOX 1", "city": "X", "state": "IL", "postal_code": "606140000"},
            {"address_purpose": "LOCATION", "address_1": "2400 N CLARK ST", "city": "CHICAGO",
             "state": "IL", "postal_code": zip5 + "1234", "telephone_number": "312-555-0100"},
        ],
        "taxonomies": [{"code": code, "desc": desc, "primary": True}],
    }


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "zcta.tsv").write_text(GAZ)
    s = Settings(home="60614", radius_miles=5, cache_path=str(tmp_path / "c.sqlite"))
    # route data/zcta.tsv into tmp
    s.path = lambda p: tmp_path / p if not p.startswith("/") else p  # type: ignore[method-assign]
    return s


def cms_router(rows_by_dataset):
    def handler(request: httpx.Request):
        ds = request.url.path.split("/")[-2]
        body = json.loads(request.content)
        rows = rows_by_dataset.get(ds, [])
        cond = body["conditions"][0]
        vals = set(map(str, cond["value"])) if isinstance(cond["value"], list) else {cond["value"]}
        keep = [r for r in rows if str(r.get(cond["property"])) in vals]
        return httpx.Response(200, json={"results": keep[body["offset"]:body["offset"] + body["limit"]], "count": len(keep)})
    return handler


@respx.mock
async def test_clinician_search_end_to_end(settings):
    def nppes(request):
        z = request.url.params["postal_code"]
        if request.url.params["taxonomy_description"] != "Family Medicine":
            return httpx.Response(200, json={"result_count": 0, "results": []})
        results = {
            "60614": [
                nppes_result("1111111111", "Ada", "Lovelace", "60614"),
                nppes_result("3333333333", "Wrong", "Specialty", "60614", code="207N00000X", desc="Dermatology"),
                nppes_result("4444444444", "Retired", "Doc", "60614", status="D"),
            ],
            "60657": [nppes_result("2222222222", "Bob", "Builder", "60657"),
                      nppes_result("1111111111", "Ada", "Lovelace", "60614")],  # dup
        }.get(z, [])
        return httpx.Response(200, json={"result_count": len(results), "results": results})

    respx.get("https://npiregistry.cms.hhs.gov/api/").mock(side_effect=nppes)
    respx.post(url__regex=r"https://geocoding\.geo\.census\.gov/.*addressbatch").mock(
        return_value=httpx.Response(200, text=(
            '"1111111111","2400 N Clark St, Chicago, IL, 60614","Match","Exact","2400 N CLARK ST, CHICAGO, IL, 60614","-87.6400,41.9250","1","L"\n'
            '"2222222222","2400 N Clark St, Chicago, IL, 60657","No_Match"\n'
        )))
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(side_effect=cms_router({
        "mj5m-pzi6": [{"npi": "1111111111", "med_sch": "NORTHWESTERN UNIVERSITY", "grd_yr": "2005",
                       "facility_name": "LINCOLN PARK MED GROUP", "telehlth": "Y", "pri_spec": "FAMILY PRACTICE"}],
        "a174-a962": [{"npi": "1111111111", "final_MIPS_score_without_CPB": "10", "final_MIPS_score": "92.5"}],
        "27ea-46a8": [{"npi": "1111111111", "facility_type": "Hospital", "facility_affiliations_certification_number": "140281"}],
        "xubh-q36u": [{"facility_id": "140281", "facility_name": "NORTHWESTERN MEMORIAL HOSPITAL", "hospital_overall_rating": "5"}],
    }))

    eng = Engine(settings)
    try:
        out = await eng.search("primary_care")
    finally:
        await eng.close()

    ids = [p["id"] for p in out["providers"]]
    assert sorted(ids) == ["1111111111", "2222222222"]  # dedup, taxonomy filter, inactive dropped
    ada = next(p for p in out["providers"] if p["id"] == "1111111111")
    bob = next(p for p in out["providers"] if p["id"] == "2222222222")
    assert ada["name"] == "Ada Lovelace" and ada["credential"] == "MD" and ada["zip"] == "60614"
    assert ada["located"] == "address" and bob["located"] == "zip"
    assert ada["facts"]["mips_score"] == 92.5  # not the "without CPB" column
    assert ada["facts"]["grad_year"] == 2005 and ada["facts"]["telehealth"] is True
    assert ada["facts"]["hospitals"][0]["stars"] == 5
    assert ada["score"] > bob["score"]  # real data beats no data
    assert ada["confidence"] == 1.0 and bob["confidence"] == 0.0
    assert ids[0] == "1111111111"
    assert all(p["network"] == "unknown" for p in out["providers"])


@respx.mock
async def test_hospital_search(settings):
    respx.post(url__regex=r"https://geocoding\.geo\.census\.gov/.*").mock(return_value=httpx.Response(200, text=""))
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(side_effect=cms_router({
        "xubh-q36u": [
            {"facility_id": "140281", "facility_name": "GOOD HOSPITAL", "address": "1 MAIN ST", "citytown": "CHICAGO",
             "state": "IL", "zip_code": "60614", "hospital_type": "Acute Care Hospitals",
             "emergency_services": "Yes", "hospital_overall_rating": "5"},
            {"facility_id": "140999", "facility_name": "UNRATED HOSPITAL", "zip_code": "60657",
             "hospital_overall_rating": "Not Available"},
        ],
        "dgck-syfz": [{"facility_id": "140281", "hcahps_measure_id": "H_STAR_RATING", "patient_survey_star_rating": "4"}],
    }))
    eng = Engine(settings)
    try:
        out = await eng.search("hospital")
    finally:
        await eng.close()
    good, unrated = out["providers"]
    assert good["name"] == "Good Hospital" and good["facts"]["stars"] == 5 and good["facts"]["patient_stars"] == 4
    assert good["facts"]["emergency"] is True
    assert unrated["facts"]["stars"] is None
    assert good["score"] > unrated["score"]


@respx.mock
async def test_fhir_network(settings):
    settings.network = NetworkSettings(type="fhir", fhir_base_url="https://payer.example/fhir", fhir_network_ids=["net-ppo"])
    respx.get("https://npiregistry.cms.hhs.gov/api/").mock(return_value=httpx.Response(200, json={"results": [
        nppes_result("1111111111", "Ada", "Lovelace", "60614"),
        nppes_result("2222222222", "Bob", "Builder", "60614"),
        nppes_result("5555555555", "Cy", "Nowhere", "60614"),
    ]}))
    respx.post(url__regex=r"https://geocoding.*").mock(return_value=httpx.Response(200, text=""))
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(return_value=httpx.Response(200, json={"results": []}))

    def prac(request):
        ident = request.url.params["identifier"]
        rid = {"1111111111": "p1", "2222222222": "p2"}.get(ident.split("|")[-1])
        entries = [{"resource": {"resourceType": "Practitioner", "id": rid}}] if rid else []
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": entries})

    def role(request):
        net = {"Practitioner/p1": "net-ppo", "Practitioner/p2": "net-hmo"}[request.url.params["practitioner"]]
        ext = {"url": "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference",
               "valueReference": {"reference": f"Organization/{net}"}}
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": "PractitionerRole", "extension": [ext]}}]})

    respx.get("https://payer.example/fhir/Practitioner").mock(side_effect=prac)
    respx.get("https://payer.example/fhir/PractitionerRole").mock(side_effect=role)
    eng = Engine(settings)
    try:
        out = await eng.search("primary_care")
    finally:
        await eng.close()
    net = {p["id"]: p["network"] for p in out["providers"]}
    assert net == {"1111111111": "in_network", "2222222222": "out_of_network", "5555555555": "not_listed"}


def test_parsers():
    z = parse_gazetteer(GAZ)
    assert z["60614"] == (41.922695, -87.652774)
    got = parse_batch_response('"7","a","Match","Exact","b","-87.1,41.2","1","L"\n"8","a","No_Match"\n')
    assert got == {"7": (41.2, -87.1)}
