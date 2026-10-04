/*
 * Capture an insurer's "find a provider" results from your own browser.
 *
 * Works on most modern directory sites (Davis Vision, VSP, ...), whose pages
 * fetch results as JSON. This records those JSON responses while you search.
 *
 *   1. Open the insurer's provider locator. Log in or enter your client code if it asks.
 *   2. Open DevTools (Cmd+Opt+J), paste this file into the Console, press Enter.
 *   3. Search (ZIP, radius, provider type), then click through every results page.
 *   4. In the Console type:   hpdSave()
 *      It downloads directory_capture.json.
 *   5. Convert it:
 *        python -m finder.import_capture ~/Downloads/directory_capture.json data/davis_vision.csv
 *
 * Nothing leaves your browser except the file you download.
 */
(() => {
  if (window.__hpd) return console.log("capture already running; hpdSave() when done");
  const store = (window.__hpd = []);
  const keep = (url, text) => {
    if (!text || text.length < 50 || !/zip|postal/i.test(text) || !/name/i.test(text)) return;
    try {
      store.push({ url: String(url), data: JSON.parse(text) });
      console.log(`captured ${store.length}: ${String(url).slice(0, 80)}`);
    } catch { /* not JSON */ }
  };

  const origFetch = window.fetch;
  window.fetch = async (...args) => {
    const res = await origFetch(...args);
    res.clone().text().then((t) => keep(args[0]?.url || args[0], t)).catch(() => {});
    return res;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.addEventListener("load", () => {
      try { keep(url, this.responseType === "" || this.responseType === "text" ? this.responseText : JSON.stringify(this.response)); } catch {}
    });
    return origOpen.call(this, method, url, ...rest);
  };

  window.hpdSave = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(store)], { type: "application/json" }));
    a.download = "directory_capture.json";
    a.click();
    console.log(`saved ${store.length} responses`);
  };
  console.log("Capturing. Run your search, page through results, then type hpdSave()");
})();
