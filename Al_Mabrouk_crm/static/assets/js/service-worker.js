self.addEventListener("install", (event) => {
    self.skipWaiting(); // Forces the new version to take over immediately
  });
  
  self.addEventListener("activate", (event) => {
    event.waitUntil(
      caches.keys().then((cacheNames) => {
        return Promise.all(
          cacheNames.map((cache) => {
            if (cache !== "new-cache-name") {
              return caches.delete(cache); // Delete old cache
            }
          })
        );
      })
    );
  });
  