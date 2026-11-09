// FlipDesk Sync - Leboncoin. Leboncoin's API needs a token that only lives inside the
// page, so instead of calling it we read the answers the page itself receives when you
// open "Mes annonces" (listings) and "Mes transactions" (sales / purchases).
(() => {
  const browser = globalThis.browser || globalThis.chrome; // Firefox | Chrome
  const PLATFORM = "Leboncoin";
  const CLOTHES = /robe|jupe|legging|chemise|pantalon|veste|manteau|pull|sweat|chaussure|basket|sac /i;
  let forced = false;
  browser.runtime.sendMessage({ type: "shouldSync", platform: PLATFORM }).then(r => { forced = r.forced; }).catch(() => {});

  // inject the hook into the page as early as possible
  const s = document.createElement("script");
  s.src = browser.runtime.getURL("lbc-hook.js");
  s.onload = () => s.remove();
  (document.head || document.documentElement).appendChild(s);

  const send = payload => browser.runtime.sendMessage({ type: "sync", payload: { platform: PLATFORM, partial: true, ...payload } });
  const day = v => (v || "").slice(0, 10);
  const big = u => (u || "").replace("rule=ad-thumb", "rule=ad-large").replace("rule=ad-small", "rule=ad-large");
  const euros = (cents, fallback) => cents != null ? cents / 100 : +(Array.isArray(fallback) ? fallback[0] : fallback) || 0;

  async function onData(url, body) {
    let j;
    try { j = JSON.parse(body); } catch { return; }
    try {
      if (/dashboard\/v1\/search/.test(url)) {
        const listings = (j.ads || []).filter(a => a.ad_type !== "demand").map(a => ({
          ext: `lbc:ad:${a.list_id}`, title: a.subject, price: euros(a.price_cents, a.price),
          active: a.status === "active", sold: false, url: a.url || "", date: day(a.first_publication_date),
          condition: ((a.attributes || []).find(x => x.key === "condition") || {}).value_label || "",
          photo: (a.images && ((a.images.urls_large || [])[0] || (a.images.urls || [])[0] || big(a.images.thumb_url))) || ""
        }));
        await send({ listings });
      } else {
        const list = Array.isArray(j) ? j : (j.transactions || j.items || []);
        const seller = /user_kind=seller/.test(url);
        const rows = list.map(t => ({
          ext: `lbc:${seller ? "tx" : "buy"}:${(t.id && (t.id.purchase_id || t.id.id)) || t.purchase_id || t.id}`,
          title: (t.item && t.item.title) || t.title || "Article Leboncoin",
          price: (t.price != null ? t.price : t.item && t.item.price || 0) / 100,
          date: day(t.created_at), cancelled: t.step === "cancelled",
          photo: big(t.item && t.item.thumbnail_url)
        }));
        if (seller) await send({ sales: rows });
        else {
          const { purchases = true, skipClothes = true } = await browser.storage.local.get({ purchases: true, skipClothes: true });
          if (purchases) await send({ purchases: rows.filter(r => !r.cancelled && !(skipClothes && CLOTHES.test(r.title)))
            .map(r => ({ ...r, note: "total payé (paiement sécurisé et port inclus)" })) });
        }
      }
    } catch (e) {
      browser.runtime.sendMessage({ type: "error", platform: PLATFORM, error: e.message });
    }
    if (forced) browser.runtime.sendMessage({ type: "done" });
  }

  window.addEventListener("message", e => {
    if (e.source !== window || !e.data || e.data.__flipdesk !== 1 || e.data.kind !== "data") return;
    onData(String(e.data.url), String(e.data.body));
  });
})();
