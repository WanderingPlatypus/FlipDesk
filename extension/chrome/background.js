// FlipDesk Sync - Chrome background (Manifest V3 service worker).
// Same job as the Firefox background.js: receives what the content scripts read on
// Vinted / Leboncoin and pushes it to the FlipDesk server (POST /api/sync).
// Chrome may stop the worker at any time, so state lives in chrome.storage.session.
const DEF = { server: "", password: "", purchases: true, skipClothes: true };
const THROTTLE_MS = 10 * 60 * 1000;     // passive Vinted sync at most every 10 min
const ss = chrome.storage.session;

const cfg = async () => ({ ...DEF, ...(await chrome.storage.local.get(Object.keys(DEF))) });

function authHeaders(c) {
  const h = { "Content-Type": "application/json" };
  if (c.password) h.Authorization = "Basic " + btoa(unescape(encodeURIComponent("flipdesk:" + c.password)));
  return h;
}

async function serverFetch(path, opts = {}) {
  const c = await cfg();
  if (!c.server) throw new Error("Adresse du serveur FlipDesk non configurée (Réglages)");
  let r;
  try {
    r = await fetch(c.server.replace(/\/+$/, "") + path, { ...opts, headers: authHeaders(c) });
  } catch (e) {
    throw new Error("Serveur FlipDesk injoignable (" + c.server + ")");
  }
  if (r.status === 401) throw new Error("Mot de passe FlipDesk incorrect");
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Erreur serveur ${r.status}`);
  return j;
}

async function record(platform, entry) {
  const { last = {} } = await chrome.storage.local.get("last");
  last[platform] = { at: Date.now(), ...entry };
  await chrome.storage.local.set({ last });
}

async function badge(add) {
  const { unseen = 0 } = await ss.get("unseen");
  const n = add === null ? 0 : unseen + add;
  await ss.set({ unseen: n });
  await chrome.action.setBadgeText({ text: n ? String(n) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
}

async function push(payload) {
  const empty = !["listings", "sales", "purchases"].some(k => (payload[k] || []).length);
  if (empty) return { skipped: true };
  try {
    const rep = await serverFetch("/api/sync", { method: "POST", body: JSON.stringify(payload) });
    const counts = { created: rep.created.length, sold: rep.sold.length, updated: rep.updated.length };
    const { last = {} } = await chrome.storage.local.get("last");
    const prevWarn = (last[payload.platform] && last[payload.platform].warnings) || [];
    await record(payload.platform, { ok: true, counts, created: rep.created, sold: rep.sold,
      warnings: [...new Set([...rep.warnings, ...(payload.partial ? prevWarn : [])])].slice(0, 10) });
    if (counts.created + counts.sold) await badge(counts.created + counts.sold);
    if (rep.sold.length || rep.warnings.length) {
      chrome.notifications.create({
        type: "basic", iconUrl: "icons/128.png", title: `FlipDesk · ${payload.platform}`,
        message: [rep.sold.length ? `Vendu : ${rep.sold.join(", ")}` : "", ...rep.warnings].filter(Boolean).join("\n").slice(0, 300)
      });
    }
    return rep;
  } catch (e) {
    await record(payload.platform, { ok: false, error: e.message });
    await chrome.action.setBadgeText({ text: "!" });
    await chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
    return { error: e.message };
  }
}

// tabs opened by "Synchroniser tout": { [tabId]: true }, and the ones that reported back
const getForced = async () => (await ss.get("forced")).forced || {};
async function setForced(tabId, on) {
  const f = await getForced();
  if (on) f[tabId] = true; else delete f[tabId];
  await ss.set({ forced: f });
}

async function handle(msg, sender) {
  const tabId = sender.tab && sender.tab.id;
  switch (msg.type) {
    case "shouldSync": {
      if ((await getForced())[tabId]) return { go: true, forced: true };
      const { last = {} } = await chrome.storage.local.get("last");
      const at = (last[msg.platform] && last[msg.platform].at) || 0;
      return { go: Date.now() - at > THROTTLE_MS, forced: false };
    }
    case "sync":
      return push(msg.payload);
    case "error":
      await record(msg.platform, { ok: false, error: msg.error });
      return {};
    case "done":
      if (tabId != null) await ss.set({ ["done_" + tabId]: true });
      return {};
    case "syncAll":
      return syncAll();
    case "ping":
      try { await serverFetch("/api/health"); return { ok: true }; } catch (e) { return { ok: false, error: e.message }; }
    case "seen":
      await badge(null); return {};
  }
  return {};
}

// Chrome needs sendResponse + "return true" for async answers
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(sendResponse, e => sendResponse({ error: e.message }));
  return true;
});

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Opens a page that holds the data in a background tab, waits for it to report, closes it.
async function openAndWait(url, ms = 30000) {
  const tab = await chrome.tabs.create({ url, active: false });
  await setForced(tab.id, true);
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if ((await ss.get("done_" + tab.id))["done_" + tab.id]) break;
    await sleep(500);
  }
  await ss.remove("done_" + tab.id);
  await setForced(tab.id, false);
  await chrome.tabs.remove(tab.id).catch(() => {});
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
    const { last = {} } = await chrome.storage.local.get("last");
    return last;
  })().finally(() => { running = null; });
  return running;
}
