// FlipDesk Sync - Vinted: reads your listings, sales and purchases with your
// logged-in session (same requests as vinted.fr itself) and sends them to FlipDesk.
(async () => {
  const PLATFORM = "Vinted";
  const CLOTHES = /robe|jupe|legging|chemise|gonna|abito|skirt|combi|bottes|pantalon|ensemble|escarpin|vestido|kleedje|jean|pull|veste|manteau|t-shirt|tee-shirt|sweat|top |blouse|short|chaussure|basket|sneaker|sac |photocard/i;
  const st = await browser.runtime.sendMessage({ type: "shouldSync", platform: PLATFORM });
  if (!st.go) return;
  const done = () => browser.runtime.sendMessage({ type: "done" });

  // content.fetch runs the request as the page (cookies, same origin) in Firefox
  const F = (typeof content !== "undefined" && content.fetch) ? content.fetch.bind(content) : fetch;
  const get = async path => {
    const r = await F(new URL(path, location.origin).href, { headers: { accept: "application/json" }, credentials: "include" });
    if (r.status === 401 || r.status === 403) throw new Error("Non connecté à Vinted");
    if (!r.ok) throw new Error(`Vinted a répondu ${r.status} (${path.split("?")[0]})`);
    return r.json();
  };
  const day = s => (s || "").slice(0, 10);

  try {
    const opts = await browser.storage.local.get({ purchases: true, skipClothes: true, txItem: {} });
    const me = await get("/api/v2/users/current");
    const uid = me.user && me.user.id;
    if (!uid) throw new Error("Non connecté à Vinted");

    // listings (wardrobe), all pages
    const listings = [];
    for (let p = 1; p <= 20; p++) {
      const j = await get(`/api/v2/wardrobe/${uid}/items?page=${p}&per_page=96&order=newest_first`);
      for (const it of j.items || []) {
        if (it.is_draft) continue;
        const ts = it.photos && it.photos[0] && it.photos[0].high_resolution && it.photos[0].high_resolution.timestamp;
        listings.push({
          ext: `vinted:item:${it.id}`, title: it.title, price: +(it.price && it.price.amount || 0),
          active: !it.is_closed && !it.is_hidden, sold: !!it.is_closed && it.item_closing_action === "sold",
          url: it.url || (it.path ? location.origin + it.path : ""), condition: it.status || "",
          date: ts ? new Date(ts * 1000).toISOString().slice(0, 10) : ""
        });
      }
      if (!j.pagination || p >= j.pagination.total_pages) break;
    }

    const orders = async type => {
      const out = [];
      for (let p = 1; p <= 10; p++) {
        const j = await get(`/api/v2/my_orders?type=${type}&status=all&per_page=100&page=${p}`);
        out.push(...(j.my_orders || []));
        if ((j.my_orders || []).length < 100) break;
      }
      return out;
    };

    // sales; the order list has no item id, so look it up once per transaction (cached)
    const txItem = opts.txItem || {};
    let lookups = 0;
    const sales = [];
    for (const o of await orders("sold")) {
      const id = o.transaction_id;
      if (!(id in txItem) && lookups < 40 && o.transaction_user_status !== "failed") {
        lookups++;
        try { txItem[id] = (await get(`/api/v2/transactions/${id}`)).transaction.item_id || null; } catch { txItem[id] = null; }
      }
      sales.push({
        ext: `vinted:tx:${id}`, title: o.title, price: +(o.price && o.price.amount || 0), date: day(o.date),
        cancelled: o.transaction_user_status === "failed", item_ext: txItem[id] ? `vinted:item:${txItem[id]}` : null
      });
    }
    await browser.storage.local.set({ txItem });

    const purchases = [];
    if (opts.purchases) {
      for (const o of await orders("purchased")) {
        if (o.transaction_user_status === "failed") continue;
        if (opts.skipClothes && CLOTHES.test(o.title)) continue;
        purchases.push({ ext: `vinted:buy:${o.transaction_id}`, title: o.title, price: +(o.price && o.price.amount || 0),
          date: day(o.date), note: "total payé, frais de protection et port inclus" });
      }
    }
    await browser.runtime.sendMessage({ type: "sync", payload: { platform: PLATFORM, listings, sales, purchases } });
  } catch (e) {
    await browser.runtime.sendMessage({ type: "error", platform: PLATFORM, error: e.message });
  }
  done();
})();
