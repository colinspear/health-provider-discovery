"""Transparent, explainable ranking.

Each provider gets components scored 0..1 with a weight. Missing data is skipped
(not counted as zero), and the result is shrunk toward a neutral 50 in
proportion to how much quality data was missing. A provider with no quality
data therefore can't out-rank one with good data on proximity alone.

Honest caveat: public quality data for individual clinicians is thin. MIPS
scores are mostly about reporting compliance, not outcomes. Treat the score as a
tie-breaker over a shortlist, not a verdict.
"""

from __future__ import annotations

from datetime import date

from .models import Provider

NEUTRAL = 50.0


def _years_in_practice(grad_year: int | None) -> float | None:
    if not grad_year:
        return None
    yrs = date.today().year - grad_year
    # Full credit from 10 years out; past that, experience isn't a differentiator.
    return max(0.0, min(1.0, (yrs - 3) / 7))


def components(p: Provider, radius: float) -> list[dict]:
    f = p.facts
    out: list[dict] = []

    def add(key, label, value, weight, detail, quality=True):
        out.append(
            {"key": key, "label": label, "value": value, "weight": weight, "detail": detail, "quality": quality}
        )

    if p.kind == "hospital":
        s = f.get("stars")
        add("stars", "CMS overall star rating", s / 5 if s else None, 0.55,
            f"{s:.0f} / 5 stars" if s else "Not rated by CMS")
        ps = f.get("patient_stars")
        add("patients", "Patient survey (HCAHPS) stars", ps / 5 if ps else None, 0.2,
            f"{ps:.0f} / 5 stars" if ps else "No survey rating")
    elif p.kind == "clinician":
        m = f.get("mips_score")
        add("mips", "Medicare MIPS score", m / 100 if m is not None else None, 0.3,
            f"{m:.0f} / 100" if m is not None else "No MIPS score (common for non-Medicare clinicians)")
        hosps = [h for h in f.get("hospitals") or [] if h.get("stars")]
        if hosps:
            best = hosps[0]
            add("hospital", "Best affiliated hospital", best["stars"] / 5, 0.25,
                f"{best['name']} ({best['stars']:.0f}★)")
        else:
            add("hospital", "Best affiliated hospital", None, 0.25, "No rated hospital affiliation")
        y = _years_in_practice(f.get("grad_year"))
        add("experience", "Years in practice", y, 0.15,
            f"Graduated {f['grad_year']}" if f.get("grad_year") else "Unknown")
    if p.distance is not None and radius > 0:
        add("distance", "Proximity", max(0.0, 1 - p.distance / radius), 0.25,
            f"{p.distance:.1f} mi", quality=False)
    return out


def score(p: Provider, radius: float) -> None:
    comps = components(p, radius)
    have = [c for c in comps if c["value"] is not None]
    q_total = sum(c["weight"] for c in comps if c["quality"])
    q_have = sum(c["weight"] for c in have if c["quality"])
    p.breakdown = comps
    if not have:
        p.score, p.confidence = None, 0.0
        return
    raw = 100 * sum(c["value"] * c["weight"] for c in have) / sum(c["weight"] for c in have)
    p.confidence = (q_have / q_total) if q_total else 0.0
    if q_total:
        # shrink toward neutral by the share of quality data that's missing
        p.score = round(p.confidence * raw + (1 - p.confidence) * NEUTRAL, 1)
    else:
        # organizations: no quality data exists at all, so rank on what we have
        p.score = round(raw, 1)
