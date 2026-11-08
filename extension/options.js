const $ = s => document.querySelector(s);
const DEF = { server: "", password: "", purchases: true, skipClothes: true };

browser.storage.local.get(DEF).then(c => {
  $("#server").value = c.server; $("#password").value = c.password;
  $("#purchases").checked = c.purchases; $("#skipClothes").checked = c.skipClothes;
});

function msg(t, ok) { const m = $("#msg"); m.textContent = t; m.className = ok ? "pos" : "neg"; }

async function save() {
  let server = $("#server").value.trim().replace(/\/+$/, "");
  if (server && !/^https?:\/\//.test(server)) server = "http://" + server;
  $("#server").value = server;
  await browser.storage.local.set({
    server, password: $("#password").value,
    purchases: $("#purchases").checked, skipClothes: $("#skipClothes").checked
  });
}
$("#save").onclick = async () => { await save(); msg("Enregistré ✓", true); };
$("#test").onclick = async () => {
  await save();
  const r = await browser.runtime.sendMessage({ type: "ping" });
  msg(r.ok ? "Connexion à FlipDesk OK ✓" : r.error, r.ok);
};
