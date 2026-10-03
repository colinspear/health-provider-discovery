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


def _c(key, label, group, nppes, codes, tip=""):
    return Service(key, label, "clinician", group, tuple(nppes), tuple(codes), tip)


def _o(key, label, group, nppes, codes, tip=""):
    return Service(key, label, "organization", group, tuple(nppes), tuple(codes), tip)


SERVICES: list[Service] = [
    _c("primary_care", "Primary care (family / internal medicine)", "Everyday care",
       ["Family Medicine", "Internal Medicine"], ["207Q00000X", "207R00000X", "208D00000X"]),
    _c("pediatrics", "Pediatrics", "Everyday care", ["Pediatrics"], ["208000000X"]),
    _c("obgyn", "OB-GYN", "Everyday care", ["Obstetrics & Gynecology"],
       ["207V00000X", "207VG0400X", "207VX0000X"]),
    _o("urgent_care", "Urgent care", "Everyday care", ["Urgent Care"], ["261QU0200X"]),
    Service("hospital", "Hospitals", "hospital", "Facilities",
            tip="CMS overall star rating + patient-survey stars"),
    _o("imaging", "Imaging / radiology centers", "Facilities", ["Radiology"],
       ["261QR0200X", "261QR0206X", "261QR0208X"]),
    _o("lab", "Labs", "Facilities", ["Clinical Medical Laboratory"], ["291U00000X"]),
    _c("dermatology", "Dermatology", "Specialists", ["Dermatology"], ["207N00000X"]),
    _c("cardiology", "Cardiology", "Specialists", ["Cardiovascular Disease"], ["207RC0000X"]),
    _c("gastro", "Gastroenterology", "Specialists", ["Gastroenterology"], ["207RG0100X"]),
    _c("orthopedics", "Orthopedic surgery", "Specialists", ["Orthopaedic Surgery"],
       ["207X00000X", "207XS0114X", "207XX0004X", "207XS0106X", "207XX0005X"]),
    _c("ent", "Ear, nose & throat", "Specialists", ["Otolaryngology"], ["207Y00000X"]),
    _c("endocrinology", "Endocrinology", "Specialists",
       ["Endocrinology, Diabetes & Metabolism"], ["207RE0101X"]),
    _c("neurology", "Neurology", "Specialists", ["Neurology"], ["2084N0400X"]),
    _c("urology", "Urology", "Specialists", ["Urology"], ["208800000X"]),
    _c("allergy", "Allergy & immunology", "Specialists", ["Allergy & Immunology"],
       ["207K00000X", "207KA0200X"]),
    _c("ophthalmology", "Ophthalmology", "Eyes & teeth", ["Ophthalmology"], ["207W00000X"]),
    _c("optometry", "Optometry", "Eyes & teeth", ["Optometrist"], ["152W00000X"]),
    _c("dentist", "Dentist (general)", "Eyes & teeth", ["General Practice", "Dentist"],
       ["1223G0001X", "122300000X"], "Dental is usually a separate plan/network."),
    _c("psychiatry", "Psychiatry", "Mental health", ["Psychiatry"], ["2084P0800X"]),
    _c("therapy", "Therapy / counseling", "Mental health",
       ["Psychologist", "Clinical", "Mental Health"],
       ["103T00000X", "103TC0700X", "1041C0700X", "101YM0800X", "101YP2500X", "106H00000X"],
       "Therapists rarely appear in CMS quality data; ranking leans on distance."),
    _c("physical_therapy", "Physical therapy", "Rehab", ["Physical Therapist"],
       ["225100000X", "2251X0800X"]),
]

BY_KEY = {s.key: s for s in SERVICES}
