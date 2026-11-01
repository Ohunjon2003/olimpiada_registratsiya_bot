"""Mobil ilova (PWA): manifest, service worker va aloqa uzilganda ko'rsatiladigan sahifa.

Telefonda brauzer menyusidan «Bosh ekranga qo'shish» desa — panel alohida ilova kabi
(manzil satrisiz, to'liq ekranda) ochiladi.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ...services import settings as st

router = APIRouter()

# Service worker: faqat /static/ fayllarini keshlaydi (tezlik uchun). Sahifalar va API
# har doim serverdan olinadi — eskirgan ma'lumot yoki CSRF xatosi bo'lmasligi uchun.
SW_JS = """
const V = 'olimp-v1';
const OFFLINE = '/offline';
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(V).then((c) => c.add(OFFLINE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin !== location.origin) return;
  if (u.pathname.startsWith('/static/')) {            // eskirgan nusxani ko'rsatib, fonda yangilaydi
    e.respondWith(caches.open(V).then((c) => c.match(r).then((hit) => {
      const net = fetch(r).then((resp) => { if (resp.ok) c.put(r, resp.clone()); return resp; }).catch(() => hit);
      return hit || net;
    })));
    return;
  }
  if (r.mode === 'navigate') {                         // aloqa yo'q bo'lsa — tushunarli sahifa
    e.respondWith(fetch(r).catch(() => caches.match(OFFLINE)));
  }
});
"""


@router.get("/sw.js", include_in_schema=False)
async def service_worker():
    # Ildizdan beriladi — scope butun saytni qamrab oladi
    return Response(SW_JS, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    name = st.get("olympiad_name") or "Olimpiada"
    data = {
        "name": f"{name} — boshqaruv",
        "short_name": "Olimpiada",
        "description": "Olimpiada boshqaruv paneli: ball kiritish, titul skaneri, davomat",
        "lang": "uz",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f4f6fa",
        "theme_color": "#2453d6",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Titul skaneri", "url": "/scan", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
            {"name": "Ball kiritish", "url": "/judge/enter", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
            {"name": "Davomat", "url": "/attendance", "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192"}]},
        ],
    }
    return JSONResponse(data, media_type="application/manifest+json")


@router.get("/offline", include_in_schema=False)
async def offline():
    return HTMLResponse("""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Aloqa yo'q</title>
<style>body{font:16px system-ui,sans-serif;background:#f4f6fa;color:#1b2333;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}
.b{max-width:340px}h1{font-size:22px}button{font:inherit;padding:12px 22px;border:0;border-radius:10px;background:#2453d6;color:#fff;margin-top:12px}</style></head>
<body><div class="b"><div style="font-size:56px">📡</div><h1>Internet aloqasi yo'q</h1>
<p>Aloqa tiklangach qayta urinib ko'ring. Kiritilgan ma'lumotlar serverda saqlangan.</p>
<button onclick="location.reload()">🔄 Qayta urinish</button></div></body></html>""")
