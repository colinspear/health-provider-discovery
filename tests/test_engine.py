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
        if request.url.params["taxonomy_description"] != "Family":
            return httpx.Response(200, json={"result_count": 0, "results": []})
        results = {
            "60614": [
                nppes_result("1111111111", "Ada", "Lovelace", "60614"),
                nppes_result("3333333333", "Wrong", "Specialty", "60614", code="207N00000X", desc="Dermatology"),
                nppes_result("4444444444", "Retired", "Doc", "60614", status="D"),
                nppes_result("6666666666", "Tele", "Hospitalist", "60614"),
                nppes_result("7777777777", "Ward", "Hospitalist", "60614"),
            ],
            "60657": [nppes_result("2222222222", "Bob", "Builder", "60657"),
                      nppes_result("1111111111", "Ada", "Lovelace", "60614")],  # dup
        }.get(z, [])
        return httpx.Response(200, json={"result_count": len(results), "results": results})

    respx.get("https://npiregistry.cms.hhs.gov/api/").mock(side_effect=nppes)
    def census(request):
        # match only addresses in 60614; echo back the row ids we were sent
        lines = [ln for ln in request.content.decode(errors="ignore").splitlines() if ln[:1].isdigit()]
        out = []
        for ln in lines:
            rid = ln.split(",")[0]
            if ln.rstrip().endswith("60614"):
                out.append(f'"{rid}","x","Match","Exact","x","-87.6400,41.9250","1","L"')
            else:
                out.append(f'"{rid}","x","No_Match"')
        return httpx.Response(200, text="\n".join(out))

    respx.post(url__regex=r"https://geocoding\.geo\.census\.gov/.*addressbatch").mock(side_effect=census)
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(side_effect=cms_router({
        "mj5m-pzi6": [{"npi": "1111111111", "med_sch": "NORTHWESTERN UNIVERSITY", "grd_yr": "2005",
                       "facility_name": "LINCOLN PARK MED GROUP", "org_pac_id": "PAC1", "telehlth": "Y",
                       "pri_spec": "FAMILY PRACTICE"},
                      {"npi": "7777777777", "pri_spec": "HOSPITALIST"}],
        "a174-a962": [{"npi": "1111111111", "source": "group", "final_mips_score_without_cpb": "10", "final_mips_score": "92.5"}],
        "27ea-46a8": [{"npi": "1111111111", "facility_type": "Hospital", "facility_affiliations_certification_number": "140281"},
                      {"npi": "1111111111", "facility_type": "Hospital", "facility_affiliations_certification_number": "050001"},
                      {"npi": "6666666666", "facility_type": "Hospital", "facility_affiliations_certification_number": "050001"}],
        "xubh-q36u": [{"facility_id": "140281", "facility_name": "NORTHWESTERN MEMORIAL HOSPITAL", "zip_code": "60657",
                       "hospital_overall_rating": "5", "hospital_overall_rating_footnote": ""},
                      {"facility_id": "050001", "facility_name": "FARAWAY HOSPITAL", "zip_code": "90210",
                       "hospital_overall_rating": "Not Available", "hospital_overall_rating_footnote": "16"}],
        "0ba7-2cb0": [{"org_pac_id": "PAC1", "measure_title": "Controlling High Blood Pressure", "prf_rate": "70", "star_value": "4"},
                      {"org_pac_id": "PAC1", "measure_title": "Cost measure", "prf_rate": "5000", "star_value": ""}],
        "n0yb-util": [{"npi": "1111111111", "procedure_category": "Knee replacement", "count": "40", "percentile": "90"}],
    }))

    eng = Engine(settings)
    try:
        out = await eng.search("primary_care")
    finally:
        await eng.close()

    ids = [p["id"] for p in out["providers"]]
    # dedup, taxonomy filter, inactive dropped, telehealth-only (6666) and hospitalist (7777) dropped
    assert sorted(ids) == ["1111111111", "2222222222"]
    ada = next(p for p in out["providers"] if p["id"] == "1111111111")
    bob = next(p for p in out["providers"] if p["id"] == "2222222222")
    assert ada["name"] == "Ada Lovelace" and ada["credential"] == "MD" and ada["zip"] == "60614"
    assert ada["located"] == "address" and bob["located"] == "zip"
    assert ada["facts"]["mips_score"] == 92.5  # not the "without CPB" column
    assert ada["facts"]["grad_year"] == 2005 and ada["facts"]["telehealth"] is True
    assert [h["name"] for h in ada["facts"]["hospitals"]] == ["Northwestern Memorial Hospital"]  # far one dropped
    assert ada["facts"]["hospitals"][0]["stars"] == 5
    assert ada["facts"]["mips_source"] == "group"
    assert ada["facts"]["group_measures"] == [{"title": "Controlling High Blood Pressure", "rate": "70", "stars": 4.0}]
    assert ada["facts"]["procedures"][0]["percentile"] == 90  # collected, but not scored for primary care
    assert "volume" not in {c["key"] for c in ada["breakdown"]}
    assert all(0 <= c["value"] <= 1 for c in ada["breakdown"] if c["value"] is not None)
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


def plannet_role(pid, nets_accepting):
    """PractitionerRole in MVP's layout: network-reference then its newpatients sibling."""
    ext = []
    for net, code in nets_accepting:
        ext.append({"url": "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference",
                    "valueReference": {"reference": f"Organization/{net}"}})
        ext.append({"url": "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/newpatients",
                    "extension": [{"url": "acceptingPatients", "valueCodeableConcept": {"coding": [{"code": code}]}}]})
    return {"resource": {"resourceType": "PractitionerRole", "practitioner": {"reference": f"Practitioner/{pid}"},
                         "extension": ext}}


@respx.mock
async def test_fhir_network_roster(settings):
    settings.networks = [NetworkSettings(name="MVP", type="fhir", fhir_base_url="https://payer.example/fhir",
                                         fhir_network_ids=["net-epo"])]
    respx.get("https://npiregistry.cms.hhs.gov/api/").mock(return_value=httpx.Response(200, json={"results": [
        nppes_result("1111111111", "Ada", "Lovelace", "60614"),
        nppes_result("2222222222", "Bob", "Builder", "60614"),
        nppes_result("5555555555", "Cy", "Nowhere", "60614"),
    ]}))
    respx.post(url__regex=r"https://geocoding.*").mock(return_value=httpx.Response(200, text=""))
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(return_value=httpx.Response(200, json={"results": []}))

    def roles(request):
        assert "location.address-postalcode" in request.url.params
        if request.url.params["location.address-postalcode"] != "60614" or request.url.params["specialty"] != "207Q00000X":
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            # Ada: in the EPO network but not accepting there; accepting in another network
            plannet_role("p1", [("net-other", "newpt"), ("net-epo", "nopt")]),
            plannet_role("p2", [("net-hmo", "newpt")]),
        ]})

    def pracs(request):
        ids = request.url.params["_id"].split(",")
        npi = {"p1": "1111111111", "p2": "2222222222"}
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": "Practitioner", "id": i,
                          "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi[i]}],
                          "communication": [{"coding": [{"code": "es", "display": "Spanish"}]}]}}
            for i in ids if i in npi]})

    respx.get("https://payer.example/fhir/PractitionerRole").mock(side_effect=roles)
    respx.get("https://payer.example/fhir/Practitioner").mock(side_effect=pracs)
    eng = Engine(settings)
    try:
        out = await eng.search("primary_care")
    finally:
        await eng.close()
    by = {p["id"]: p for p in out["providers"]}
    assert {k: v["network"] for k, v in by.items()} == {
        "1111111111": "in_network", "2222222222": "out_of_network", "5555555555": "not_listed"}
    assert by["1111111111"]["facts"]["accepting"] == "Not accepting new patients"  # EPO status, not the other net's
    assert by["1111111111"]["facts"]["languages"] == ["Spanish"]


@respx.mock
async def test_ny_discipline_by_license(settings):
    ada = nppes_result("1111111111", "Ada", "Lovelace", "60614")
    ada["taxonomies"][0].update({"state": "NY", "license": "012345"})
    bob = nppes_result("2222222222", "Bob", "Builder", "60614")
    bob["taxonomies"][0].update({"state": "NY", "license": "999"})
    respx.get("https://npiregistry.cms.hhs.gov/api/").mock(return_value=httpx.Response(200, json={"results": [ada, bob]}))
    respx.post(url__regex=r"https://geocoding.*").mock(return_value=httpx.Response(200, text=""))
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(url__regex=r"https://health\.data\.ny\.gov/resource/ebmi-8ctw.json.*").mock(return_value=httpx.Response(200, json=[
        {"licensenum": "12345", "licensetype": "MD", "effectivedate": "2020-01-02T00:00:00.000",
         "webaction": "License surrender.  More text.", "webnotes": "Did a bad thing."},
        {"licensenum": "999", "licensetype": "MD", "effectivedate": "2019-01-01T00:00:00.000",
         "webaction": "Dismissed."},
    ]))
    eng = Engine(settings)
    try:
        out = await eng.search("primary_care")
    finally:
        await eng.close()
    by = {p["id"]: p for p in out["providers"]}
    d = by["1111111111"]["facts"]["discipline"]
    assert d[0]["severity"] == "severe" and d[0]["date"] == "2020-01-02" and d[0]["action"] == "License surrender."
    assert by["1111111111"]["flagged"] and not by["2222222222"]["flagged"]
    assert "discipline" not in by["2222222222"]["facts"]  # dismissed actions ignored
    assert by["1111111111"]["score"] < by["2222222222"]["score"]


def test_parsers():
    z = parse_gazetteer(GAZ)
    assert z["60614"] == (41.922695, -87.652774)
    got = parse_batch_response('"7","a","Match","Exact","b","-87.1,41.2","1","L"\n"8","a","No_Match"\n')
    assert got == {"7": (41.2, -87.1)}


@respx.mock
async def test_fhir_facility_listed_by_name_and_distance(settings):
    settings.networks = [NetworkSettings(name="MVP", type="fhir", fhir_base_url="https://payer.example/fhir",
                                         fhir_network_ids=["net-epo"])]
    respx.post(url__regex=r"https://geocoding.*").mock(return_value=httpx.Response(200, text=""))
    respx.post(url__regex=r"https://data\.cms\.gov/.*").mock(side_effect=cms_router({
        "xubh-q36u": [{"facility_id": "330164", "facility_name": "HIGHLAND HOSPITAL", "zip_code": "60614",
                       "hospital_overall_rating": "3"}],
    }))

    def orgs(request):
        assert request.url.params["name"] == "Highland Hospital"
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            # same name, other side of the country: must not match
            {"resource": {"resourceType": "Organization", "id": "far", "name": "HIGHLAND HOSPITAL",
                          "address": [{"postalCode": "94602"}]}},
            # different ZIP than CMS, but ~0.3 mi away by coordinates: should match
            {"resource": {"resourceType": "Organization", "id": "near", "name": "HIGHLAND HOSPITAL",
                          "address": [{"postalCode": "60657", "extension": [{
                              "url": "http://hl7.org/fhir/StructureDefinition/geolocation",
                              "extension": [{"url": "latitude", "valueDecimal": 41.926},
                                            {"url": "longitude", "valueDecimal": -87.652}]}]}]}},
        ]})

    affils = respx.get("https://payer.example/fhir/OrganizationAffiliation").mock(
        return_value=httpx.Response(200, json={"resourceType": "Bundle", "entry": []}))
    respx.get("https://payer.example/fhir/Organization").mock(side_effect=orgs)
    eng = Engine(settings)
    try:
        out = await eng.search("hospital")
    finally:
        await eng.close()
    (h,) = out["providers"]
    assert h["network"] == "listed"  # in the directory, but no plan info published for facilities
    assert affils.calls.last.request.url.params["participating-organization"] == "Organization/near"
