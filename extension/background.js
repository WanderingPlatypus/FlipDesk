// FlipDesk Sync - background: receives what the content scripts read on Vinted /
// Leboncoin and pushes it to the FlipDesk server (POST /api/sync).
const DEF = { server: "", password: "", purchases: true, skipClothes: true };
const THROTTLE_MS = 10 * 60 * 1000;     // passive Vinted sync at most every 10 min
const forced = new Map();                // tabId -> resolve(), tabs opened by "Synchroniser tout"
let unseen = 0;                          // badge counter

const cfg = async () => ({ ...DEF, ...(await browser.storage.local.get(Object.keys(DEF))) });

function authHeaders(c) {
  const h = { "Content-Type": "application/json" };
  if (c.password) h.Authorization = "Basic " + btoa(unescape(encodeURIComponent("flipdesk:" + c.password)));
  return h;
}

async function serverFetch(path, opts = {}) {
  const c = await cfg();
  if (!c.server) throw new Error("Adresse du serveur FlipDesk non configurée (Réglages)");
  const r = await fetch(c.server.replace(/\/+$/, "") + path, { ...opts, headers: authHeaders(c) });
  if (r.status === 401) throw new Error("Mot de passe FlipDesk incorrect");
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Erreur serveur ${r.status}`);
  return j;
}

async function record(platform, entry) {
  const { last = {} } = await browser.storage.local.get("last");
  last[platform] = { at: Date.now(), ...entry };
  await browser.storage.local.set({ last });
}

async function push(payload) {
  const empty = !["listings", "sales", "purchases"].some(k => (payload[k] || []).length);
  if (empty) return { skipped: true };
  try {
    const rep = await serverFetch("/api/sync", { method: "POST", body: JSON.stringify(payload) });
    const counts = { created: rep.created.length, sold: rep.sold.length, updated: rep.updated.length };
    const { last = {} } = await browser.storage.local.get("last");
    const prevWarn = last[payload.platform]?.warnings || [];
    await record(payload.platform, { ok: true, counts, created: rep.created, sold: rep.sold,
      warnings: [...new Set([...rep.warnings, ...(payload.partial ? prevWarn : [])])].slice(0, 10) });
    const news = counts.created + counts.sold;
    if (news) {
      unseen += news;
      browser.browserAction.setBadgeText({ text: String(unseen) });
      browser.browserAction.setBadgeBackgroundColor({ color: "#22c55e" });
    }
    if (rep.sold.length || rep.warnings.length) {
      browser.notifications.create({
        type: "basic", title: `FlipDesk · ${payload.platform}`,
        message: [rep.sold.length ? `Vendu : ${rep.sold.join(", ")}` : "", ...rep.warnings].filter(Boolean).join("\n").slice(0, 300)
      });
    }
    return rep;
  } catch (e) {
    await record(payload.platform, { ok: false, error: e.message });
    browser.browserAction.setBadgeText({ text: "!" });
    browser.browserAction.setBadgeBackgroundColor({ color: "#ef4444" });
    return { error: e.message };
  }
}

browser.runtime.onMessage.addListener(async (msg, sender) => {
  const tabId = sender.tab && sender.tab.id;
  switch (msg.type) {
    case "shouldSync": {
      if (forced.has(tabId)) return { go: true, forced: true };
      const { last = {} } = await browser.storage.local.get("last");
      const at = last[msg.platform]?.at || 0;
      return { go: Date.now() - at > THROTTLE_MS, forced: false };
    }
    case "sync":
      return push(msg.payload);
    case "error":
      await record(msg.platform, { ok: false, error: msg.error });
      return {};
    case "done":
      if (forced.has(tabId)) { forced.get(tabId)(); }
      return {};
    case "syncAll":
      return syncAll();
    case "ping":
      try { await serverFetch("/api/health"); return { ok: true }; } catch (e) { return { ok: false, error: e.message }; }
    case "seen":
      unseen = 0; browser.browserAction.setBadgeText({ text: "" }); return {};
  }
});

// Opens the pages that hold the data in background tabs, waits for each to report, closes them.
async function openAndWait(url, ms = 30000) {
  const tab = await browser.tabs.create({ url, active: false });
  await new Promise(res => {
    const t = setTimeout(res, ms);
    forced.set(tab.id, () => { clearTimeout(t); res(); });
  });
  forced.delete(tab.id);
  await browser.tabs.remove(tab.id).catch(() => {});
}

let running = null;
function syncAll() {
  if (running) return running;
  running = (async () => {
    await Promise.all([
      openAndWait("https://www.vinted.fr/my_orders", 60000),
      (async () => {
        await openAndWait("https://www.leboncoin.fr/compte/part/mes-annonces");
        await openAndWait("https://www.leboncoin.fr/compte/part/mes-transactions?page=ventes");
        if ((await cfg()).purchases) await openAndWait("https://www.leboncoin.fr/compte/part/mes-transactions?page=achats");
      })()
    ]);
    const { last = {} } = await browser.storage.local.get("last");
    return last;
  })().finally(() => { running = null; });
  return running;
}
