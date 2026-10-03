# Quality signals: what exists and what's worth using

This is a survey of public and low-cost data that could rank providers better than
distance alone. Each source was checked for live access and real coverage around
Rochester, NY (14620) in October 2026.

**Bottom line.** No source measures how good an individual primary care doctor is.
The best data available is about hospitals, surgical volume, practice-level
clinical measures, and red flags such as discipline and malpractice. Reviews add
patient experience, which none of the government data covers for individual doctors.

## In use now

| Signal | Source | Who it covers | Strength | Notes |
|---|---|---|---|---|
| Hospital overall stars plus mortality, safety and readmission tallies | CMS Hospital General Information | All acute hospitals | **Strong** | Stars are built from about 40 outcome measures. The detail panel shows "N better / M worse than national". |
| Hospital patient-survey stars (HCAHPS) | CMS | All acute hospitals | Medium | The only patient-experience data with broad coverage. |
| **Procedure volume and national percentile** | CMS Doctors & Clinicians Utilization (`n0yb-util`) | Surgeons and proceduralists. In a Rochester sample, 43 of 99 orthopedists. | **Strong for surgery** | Higher volume reliably predicts better outcomes for joint replacement, spine, cardiac and cancer surgery. Counts Medicare patients only. Scored for surgical and procedural specialties. |
| **Practice clinical-quality stars** | CMS Group MIPS measures (`0ba7-2cb0`) | About 20% of local primary care clinicians | Medium | Real outcome measures such as blood pressure control and diabetes A1c. Reported per practice, not per doctor. |
| MIPS final score | CMS | About 35% | **Weak** | Usually the group's score (`source: group`) and mostly about reporting. Weight reduced to 0.15. |
| Best *local* affiliated hospital | CMS affiliations plus hospital stars | About 50% | Weak to medium | Only hospitals within 60 miles count. Clinicians affiliated only with distant hospitals (telehealth hospitalists) are dropped. |
| Years in practice | CMS | About 60% | Weak | Capped at 10 years, so it only flags very new clinicians. |

| **NY medical board actions** | Health Data NY `ebmi-8ctw` | Every NY physician and PA | **Strong red flag** | Matched on NY license number from NPPES, so no name-collision false positives. Within 10 miles of 14620 there are about 30 matches across 4 specialties. Restorations and non-disciplinary orders are shown but not penalized. |
| **Accepting new patients, languages** | MVP Plan-Net roster | MVP clinicians | Practical | Tracked per network. About 60% of in-network primary care clinicians are accepting. |
| PCI mortality by cardiologist | Health Data NY `ekig-i57g` | 14 local cardiologists | Strong but dated | 2017–2019. Shown, not scored. |

Searches also now drop clinicians whose Medicare specialty is hospitalist,
emergency, anesthesia, radiology or pathology. These were crowding the primary
care results.

## Ready to build (free)

| Signal | Source | Strength | Effort | Notes |
|---|---|---|---|---|
| NY cardiac *surgery* outcomes by surgeon | Health Data NY | Strong, but narrow | Small | Would need a "cardiac surgeon" service. |
| NY hospital infection rates and maternity practices | Health Data NY | Medium | Small | Overlaps heavily with CMS data. Maternity adds C-section and VBAC (birth after a previous C-section) rates by hospital, which would make the OB-GYN search more useful. |
| Ambulatory surgery center quality and patient surveys | CMS (`4jcv-atw7`, `48nr-hqxx`) | Medium | Small | Useful if you'd add a "surgery center" service. |

## Needs a key or money

| Signal | Source | Cost | Strength | Notes |
|---|---|---|---|---|
| **Google reviews (rating, review count, recent text)** | Google Places API | About $0.04 per new provider; Google gives 1,000 free requests a month. Cached for 30 days, so personal use is roughly free. | Medium | The only patient-experience signal for individual doctors and dentists. Biased (angry and happy patients post most) and noisy below about 20 reviews, so the score would use rating × confidence by count. **Recommended.** You'd create a key in Google Cloud and set a budget cap. |
| Yelp reviews | Yelp Fusion API | Paid tiers | Weak to medium | Thinner for doctors than Google. Skip. |
| Leapfrog Hospital Safety Grade | Leapfrog | License fee for data; free to view | Strong | Mostly duplicates CMS. Link out to it instead of integrating. |
| Board certification | ABMS CertiFACTS | Paid | Medium | NY's physician profile has the same information for free (below). |

## Possible, but scraping

| Signal | Source | Strength | Concern |
|---|---|---|---|
| **Malpractice payments, board certification, hospital privileges** | NY Physician Profile (nydoctorprofile.com) | Medium to strong (malpractice is a red flag) | No API, so this means scraping one page per doctor. It's public, government data and fine for personal use if throttled, but it breaks whenever the site changes. A good second step after the disciplinary data. |
| Healthgrades, Vitals, Zocdoc ratings | Commercial sites | Weak to medium | Terms of service forbid scraping. Skip. |
| Insurer "quality tier" or "center of excellence" flags | MVP directory, if published | Unknown | Will check once MVP's API is reachable. |

## Checked and rejected

- **CMS group patient-experience surveys (CAHPS for MIPS):** only about 30 groups nationwide, none in Rochester.
- **CMS per-clinician quality measure stars:** 2 of 300 Rochester clinicians have any.
- **ProPublica Surgeon Scorecard:** discontinued; data from 2015.
- **Open Payments (industry payments to doctors):** free and accurate, but it describes conflicts of interest, not quality. Could be shown as an informational fact if you want it.

## Signals for dental and therapy

Neither CMS nor NY publishes quality data for dentists or therapists. Realistic options:
1. Google reviews.
2. NY Office of the Professions license and discipline lookup, which also covers dentists, psychologists, social workers and optometrists. It's a red-flag check and would need scraping.
3. Your own notes: a "tried them, liked them" flag stored locally.
