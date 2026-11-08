// Runs inside the leboncoin.fr page: lets the page work normally and passes a copy of
// the "my listings" and "my transactions" responses to the FlipDesk content script.
(() => {
  if (window.__flipdeskHook) return;
  window.__flipdeskHook = true;
  const WANT = /\/api\/dashboard\/v1\/search|\/api\/consumergoods\/proxy\/v\d+\/pages\/transactions/;
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const p = orig.apply(this, arguments);
    try {
      const url = typeof input === "string" ? input : (input && input.url) || String(input);
      if (WANT.test(url)) {
        p.then(r => r.clone().text())
          .then(body => window.postMessage({ __flipdesk: 1, kind: "data", url, body }, location.origin))
          .catch(() => {});
      }
    } catch (e) { /* never break the page */ }
    return p;
  };
  window.postMessage({ __flipdesk: 1, kind: "ready" }, location.origin);
})();
