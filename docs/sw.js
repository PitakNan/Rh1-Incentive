// Service Worker มาตรฐาน Dashboard — cache-first สำหรับ static shell, network-first สำหรับข้อมูล JSON
// แก้ CACHE_NAME ทุกครั้งที่ deploy เวอร์ชันใหม่ (บังคับล้าง cache เก่า) และ STATIC_ASSETS ให้ตรงกับไฟล์จริง

const CACHE_NAME = 'rh1-incentive-v1';
const STATIC_ASSETS = [
  './',
  './index.html',
  './manifest.webmanifest',
];

// path ที่ถือว่าเป็น "ข้อมูล" ต้องได้ของใหม่สุดทุกครั้งที่มีเน็ต — เงินการเงิน/risk score ห้าม cache-first
const DATA_URL_PATTERN = /\/(data|json|api)\//i;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  if (DATA_URL_PATTERN.test(request.url)) {
    event.respondWith(networkFirst(request));
  } else {
    event.respondWith(cacheFirst(request));
  }
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    // อัปเดตใน background โดยไม่บล็อกการแสดงผล
    fetch(request).then((res) => {
      if (res.ok) caches.open(CACHE_NAME).then((c) => c.put(request, res.clone()));
    }).catch(() => {});
    return cached;
  }
  const res = await fetch(request);
  if (res.ok) {
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, res.clone());
  }
  return res;
}

async function networkFirst(request) {
  try {
    const res = await fetch(request);
    if (res.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, res.clone());
      notifyClients({ type: 'data-fresh', url: request.url });
    }
    return res;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) {
      // สำคัญ: แจ้งหน้าเว็บว่านี่คือข้อมูล cache เก่า ไม่ใช่ของสด — หน้าเว็บต้องขึ้น badge เตือน
      notifyClients({ type: 'data-stale', url: request.url });
      return cached;
    }
    throw err;
  }
}

async function notifyClients(msg) {
  const clients = await self.clients.matchAll();
  clients.forEach((c) => c.postMessage(msg));
}
