const ext = globalThis.browser || globalThis.chrome; // Firefox | Chrome
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ago = t => {
  const m = Math.round((Date.now() - t) / 60000);
  return m < 1 ? "à l'instant" : m < 60 ? `il y a ${m} min` : m < 1440 ? `il y a ${Math.round(m / 60)} h` : `il y a ${Math.round(m / 1440)} j`;
};

async function render() {
  const { last = {} } = await ext.storage.local.get("last");
  $("#plats").innerHTML = ["Vinted", "Leboncoin"].map(p => {
    const l = last[p];
    let body;
    if (!l) body = `<span class="mut">Pas encore synchronisé</span>`;
    else if (!l.ok) body = `<span class="neg">⚠ ${esc(l.error)}</span>`;
    else {
      const c = l.counts || {};
      body = `<span class="mut">${ago(l.at)}</span> · ${c.created || 0} ajouté(s), ${c.sold || 0} vendu(s), ${c.updated || 0} mis à jour`
        + (l.sold && l.sold.length ? `<div class="pos">Vendu : ${esc(l.sold.slice(0, 4).join(", "))}${l.sold.length > 4 ? "…" : ""}</div>` : "")
        + (l.warnings || []).map(w => `<div class="warn">⚠ ${esc(w)}</div>`).join("");
    }
    return `<div class="plat"><div class="pname ${p === "Vinted" ? "v" : "l"}">${p}</div>${body}</div>`;
  }).join("");
}

async function ping() {
  const r = await ext.runtime.sendMessage({ type: "ping" });
  const el = $("#srv");
  el.textContent = r.ok ? "serveur OK" : "serveur injoignable";
  el.className = "pill " + (r.ok ? "ok" : "bad");
  el.title = r.error || "";
}

$("#all").onclick = async () => {
  const b = $("#all");
  b.disabled = true; b.textContent = "Synchronisation… (onglets en arrière-plan)";
  await ext.runtime.sendMessage({ type: "syncAll" });
  b.disabled = false; b.textContent = "Synchroniser tout";
  render();
};
$("#open").onclick = async () => {
  const { server } = await ext.storage.local.get("server");
  if (server) ext.tabs.create({ url: server }); else ext.runtime.openOptionsPage();
  window.close();
};
$("#opts").onclick = () => { ext.runtime.openOptionsPage(); window.close(); };
ext.storage.onChanged.addListener(render);
ext.runtime.sendMessage({ type: "seen" });
render(); ping();
