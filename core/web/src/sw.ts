/// <reference lib="webworker" />
// Service worker: offline app shell, update on the owner's request, and Web
// Push. Push payloads are data from the core (see `jarvis_core.notify`); the
// wording is chosen here. A reply for the conversation already on screen is
// not shown as a notification.
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { NetworkFirst } from 'workbox-strategies'

declare let self: ServiceWorkerGlobalScope

cleanupOutdatedCaches()
precacheAndRoute(self.__WB_MANIFEST)

// Every app route is the single page; API and internal paths never are.
registerRoute(
  new NavigationRoute(createHandlerBoundToURL('index.html'), {
    denylist: [/^\/v1\//, /^\/internal\//, /^\/config\.json$/, /^\/health$/],
  }),
)

// Installation config: fresh when online, the last copy when offline.
registerRoute(({ url }) => url.origin === self.location.origin && url.pathname === '/config.json', new NetworkFirst({ cacheName: 'config' }))

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') void self.skipWaiting()
})

type PushPayload = {
  kind: 'reply' | 'error' | 'test'
  conversation_id?: string
  turn_id?: string
  title?: string
  text?: string
}

async function conversationOnScreen(conversationId: string): Promise<boolean> {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  return windows.some(
    (client) => client.visibilityState === 'visible' && new URL(client.url).pathname === `/c/${conversationId}`,
  )
}

self.addEventListener('push', (event) => {
  const payload = (event.data?.json() ?? { kind: 'test' }) as PushPayload
  event.waitUntil(
    (async () => {
      if (payload.kind === 'test') {
        await self.registration.showNotification('JARVIS', {
          body: 'Test bildirimi: bu cihaz bildirim alıyor.',
          icon: '/icon-192.png',
          tag: 'jarvis-test',
        })
        return
      }
      const id = payload.conversation_id ?? ''
      if (id && (await conversationOnScreen(id))) return
      const conversation = payload.title || 'JARVIS'
      const failed = payload.kind === 'error'
      await self.registration.showNotification(failed ? 'Yanıt verilemedi' : conversation, {
        body: failed ? `${conversation}\n${payload.text ?? ''}` : (payload.text ?? ''),
        icon: '/icon-192.png',
        tag: id || undefined,
        data: { url: id ? `/c/${id}` : '/' },
      })
    })(),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = new URL((event.notification.data as { url?: string } | null)?.url ?? '/', self.location.origin).href
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      const existing = windows.find((client) => new URL(client.url).origin === self.location.origin)
      if (existing) {
        await existing.focus()
        await existing.navigate(target)
        return
      }
      await self.clients.openWindow(target)
    })(),
  )
})
