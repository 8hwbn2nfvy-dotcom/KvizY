/* Service worker KvizY: оболочка приложения (HTML, CSS, JS, иконки) открывается из кэша мгновенно,
   даже пока сервер просыпается. Свежая версия скачивается в фоне и будет видна при следующем запуске.
   Запросы к /api/ всегда идут на сервер (данные не кэшируем). */
const CACHE = "kvizy-shell-v1";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/manifest.json",
  "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  const key = url.pathname === "/" || url.pathname === "/index.html" ? "/" : url.pathname;
  e.respondWith(caches.open(CACHE).then(async (cache) => {
    const cached = await cache.match(key);
    const fresh = fetch(e.request).then((resp) => {
      if (resp && resp.ok) cache.put(key, resp.clone());
      return resp;
    }).catch(() => null);
    if (cached) { e.waitUntil(fresh); return cached; }
    return (await fresh) || new Response("Нет связи", { status: 503 });
  }));
});
