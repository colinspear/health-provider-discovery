/*
 * Export Guardian in-network dentists to data/guardian_dental.csv
 *
 * Guardian's directory API sits behind bot protection, so the app can't call it.
 * Your browser can. This makes the same request the "Find a dentist" page makes
 * when you click Search, once per results page, and saves the results as a CSV.
 *
 *   1. Open https://www.guardianlife.com/find-a-dentist and run one search.
 *   2. Open DevTools (Cmd+Opt+J on Mac), paste this whole file into the Console,
 *      edit ZIP/MILES/PLAN_TYPE below if needed, press Enter.
 *   3. Move the downloaded guardian_dental.csv into the app's data/ folder.
 *
 * PLAN_TYPE: "PPO" for DentalGuard Preferred (most employer plans), "DHMO" for
 * Managed DentalGuard. Your Guardian card or benefits summary says which.
 */
(async () => {
  const ZIP = "14620";
  const MILES = 15;
  const PLAN_TYPE = "PPO";

  const QUERY = `mutation findADentistNext($fadInput: DentistsInput) {
    findADentistNext(fadInput: $fadInput) {
      totalProviders totalPages newDistance
      providers { firstName lastName officeName phoneNumber networks specialties
        addressLine1 city state postalCode distance acceptingNewPatients nationalProviderId }
    }
  }`;

  const page = async (n) => {
    const r = await fetch("/backend/graphql", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        operationName: "findADentistNext",
        query: QUERY,
        variables: { fadInput: { postalCode: ZIP, distance: MILES, planType: PLAN_TYPE, pageNumber: n } },
      }),
    });
    const j = await r.json();
    if (j.errors) throw new Error(JSON.stringify(j.errors));
    return j.data.findADentistNext;
  };

  const first = await page(1);
  const rows = [...first.providers];
  console.log(`Guardian: ${first.totalProviders} dentists, ${first.totalPages} pages`);
  for (let n = 2; n <= first.totalPages; n++) {
    await new Promise((res) => setTimeout(res, 400)); // be polite
    rows.push(...(await page(n)).providers);
    console.log(`page ${n}/${first.totalPages}`);
  }

  const cols = ["npi", "name", "office", "specialties", "address", "city", "state", "zip", "phone", "accepting", "networks"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv = [cols.join(",")].concat(rows.map((p) => [
    p.nationalProviderId, `${p.firstName ?? ""} ${p.lastName ?? ""}`.trim(), p.officeName,
    (p.specialties || []).join("; "), p.addressLine1, p.city, p.state, (p.postalCode || "").slice(0, 5),
    p.phoneNumber, p.acceptingNewPatients, (p.networks || []).join("; "),
  ].map(q).join(","))).join("\n");

  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "guardian_dental.csv";
  a.click();
  console.log(`Saved ${rows.length} rows to guardian_dental.csv`);
})();
