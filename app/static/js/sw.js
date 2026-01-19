// A very basic service worker to make the app installable.
// This service worker doesn't do any caching.

self.addEventListener('install', (event) => {
    console.log('Service Worker: Installing...');
    // Skip waiting to activate the new service worker immediately.
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('Service Worker: Activating...');
    // Take control of all pages under its scope immediately.
    event.waitUntil(clients.claim());
});

self.addEventListener('fetch', (event) => {
    // This is a "pass-through" fetch handler.
    // It's the simplest way to satisfy the PWA requirement of having a fetch handler.
    event.respondWith(fetch(event.request));
});