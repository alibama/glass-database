/* Minimal service worker: satisfies installability and serves a friendly
   offline notice. Glowtbook needs the server for data, so we deliberately do
   NOT cache app responses — just pass through, and show offline.html on failure. */
const OFFLINE = "/glowtbook/pwa/offline.html";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open("glowtbook-v1").then((c) => c.add(OFFLINE)));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => self.clients.claim());
self.addEventListener("fetch", (e) => {
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
  }
});
