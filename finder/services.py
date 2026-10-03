"""Catalog of searchable services -> how to find them.

kind:
  clinician     individual NPIs from NPPES, enriched with CMS clinician quality data
  organization  organization NPIs from NPPES (urgent care, imaging, labs). No public
                quality data exists for these, so they rank on distance only.
  hospital      CMS Hospital General Information (star ratings, patient surveys)

nppes: taxonomy_description values sent to the NPI Registry.
codes: NUCC taxonomy codes a result must carry to be kept (guards against loose
       text matching on the registry side).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    kind: str
    group: str
    nppes: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()
    tip: str = ""
    # surgeons/proceduralists: Medicare procedure-volume percentiles count toward rank
    procedural: bool = False


def _c(key, label, group, nppes, codes, tip="", procedural=False):
    return Service(key, label, "clinician", group, tuple(nppes), tuple(codes), tip, procedural)


def _o(key, label, group, nppes, codes, tip=""):
    return Service(key, label, "organization", group, tuple(nppes), tuple(codes), tip)


SERVICES: list[Service] = [
    _c("primary_care", "Primary care (family / internal medicine)", "Everyday care",
       # registry search is substring: "Family" also catches "Nurse Practitioner, Family"
       ["Family", "Internal Medicine", "Adult Health", "Primary Care"],
       ["207Q*", "207R00000X", "208D00000X", "207RG0300X", "363LF0000X", "363LA2200X", "363LP2300X"]),
    _c("pediatrics", "Pediatrics", "Everyday care", ["Pediatrics"], ["2080*", "363LP0200X"]),
    _c("obgyn", "OB-GYN", "Everyday care", ["Obstetrics & Gynecology"],
       ["207V*", "363LX0001X"], procedural=True),
    _o("urgent_care", "Urgent care", "Everyday care", ["Urgent Care"], ["261QU0200X"]),
    Service("hospital", "Hospitals", "hospital", "Facilities",
            tip="CMS overall star rating + patient-survey stars"),
    _o("imaging", "Imaging / radiology centers", "Facilities", ["Radiology"], ["261QR*", "2085*"]),
    _o("lab", "Labs", "Facilities", ["Clinical Medical Laboratory"], ["291U00000X"]),
    _c("dermatology", "Dermatology", "Specialists", ["Dermatology"], ["207N*"], procedural=True),
    _c("cardiology", "Cardiology", "Specialists", ["Cardiovascular Disease", "Cardiology"],
       ["207RC*", "207RI0011X"], procedural=True),
    _c("gastro", "Gastroenterology", "Specialists", ["Gastroenterology"], ["207RG0100X"], procedural=True),
    _c("orthopedics", "Orthopedic surgery", "Specialists", ["Orthopaedic Surgery"], ["207X*"], procedural=True),
    _c("ent", "Ear, nose & throat", "Specialists", ["Otolaryngology"], ["207Y*"], procedural=True),
    _c("endocrinology", "Endocrinology", "Specialists",
       ["Endocrinology, Diabetes & Metabolism"], ["207RE0101X"]),
    _c("neurology", "Neurology", "Specialists", ["Neurology"], ["2084N04*", "2084V0102X"]),
    _c("urology", "Urology", "Specialists", ["Urology"], ["2088*"], procedural=True),
    _c("allergy", "Allergy & immunology", "Specialists", ["Allergy & Immunology"],
       ["207K*", "207RA0201X"]),
    _c("ophthalmology", "Ophthalmology", "Eyes & teeth", ["Ophthalmology"], ["207W*"], procedural=True),
    _c("optometry", "Optometry", "Eyes & teeth", ["Optometrist"], ["152W*"]),
    _c("dentist", "Dentist (general)", "Eyes & teeth", ["Dentist"], ["1223G0001X", "122300000X"],
       "Dental is usually a separate plan/network."),
    _c("psychiatry", "Psychiatry", "Mental health", ["Psychiatry"], ["2084P08*", "363LP0808X"]),
    _c("therapy", "Therapy / counseling", "Mental health",
       ["Psychologist", "Social Worker", "Counselor", "Marriage & Family Therapist"],
       ["103T*", "1041C0700X", "101YM0800X", "101YP2500X", "101Y00000X", "106H00000X"],
       "Therapists rarely appear in CMS quality data; ranking leans on distance."),
    _c("physical_therapy", "Physical therapy", "Rehab", ["Physical Therapist"], ["2251*"]),
]


def code_matches(code: str, patterns: tuple[str, ...]) -> bool:
    """Exact NUCC code, or a family prefix written as '207X*'."""
    return any(code.startswith(p[:-1]) if p.endswith("*") else code == p for p in patterns)


BY_KEY = {s.key: s for s in SERVICES}
