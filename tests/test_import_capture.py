import csv
import json

from finder.import_capture import main


def test_import_varied_shapes(tmp_path):
    capture = [
        # flat, Guardian-like
        {"url": "/backend/graphql", "data": {"data": {"findADentistNext": {"providers": [
            {"firstName": "Ada", "lastName": "Lovelace", "officeName": "Smile Co", "postalCode": "14620-1234",
             "addressLine1": "1 Main St", "nationalProviderId": "1111111111", "acceptingNewPatients": True}]}}}},
        # nested address, no NPI, single name field
        {"url": "/api/locator", "data": {"results": [
            {"providerName": "Grace Hopper, OD", "practiceName": "Eye Place",
             "address": {"street1": "2 Elm St", "zipCode": "14607"}, "acceptingNew": "N"},
            {"providerName": "Grace Hopper, OD", "address": {"zipCode": "14607"}},  # duplicate
            {"name": "Not a provider"},  # no zip
        ]}},
    ]
    src, dst = tmp_path / "c.json", tmp_path / "out.csv"
    src.write_text(json.dumps(capture))
    main(str(src), str(dst))
    rows = list(csv.DictReader(dst.open()))
    assert len(rows) == 2
    ada, grace = rows
    assert ada["npi"] == "1111111111" and ada["name"] == "Ada Lovelace" and ada["zip"] == "14620"
    assert ada["accepting"] == "True"
    assert grace["npi"] == "" and grace["name"] == "Grace Hopper, OD" and grace["zip"] == "14607"
    assert grace["accepting"] == "N"
