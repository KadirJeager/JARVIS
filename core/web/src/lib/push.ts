// Web Push subscription of this device. The browser asks for permission; the
// subscription (endpoint and keys) is registered with the core, which sends an
// encrypted message when a turn settles.
import type { User } from 'firebase/auth'

import { api } from '@/lib/api'

export function pushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (base64url.length % 4)) % 4)
  const raw = atob(padded)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
  return bytes
}

function sameKey(subscription: PushSubscription, publicKey: string): boolean {
  const current = subscription.options.applicationServerKey
  if (!current) return false
  const a = new Uint8Array(current)
  const b = keyBytes(publicKey)
  return a.length === b.length && a.every((value, index) => value === b[index])
}

/** Id the core gives this subscription: the first 40 hex digits of SHA-256(endpoint). */
export async function subscriptionId(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint))
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 40)
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null
  const registration = await navigator.serviceWorker.getRegistration()
  return (await registration?.pushManager.getSubscription()) ?? null
}

export function deviceLabel(): string {
  const data = (navigator as Navigator & { userAgentData?: { platform?: string; brands?: { brand: string }[]; mobile?: boolean } })
    .userAgentData
  const brand = data?.brands?.map((b) => b.brand).find((b) => !/not.?a.?brand|chromium/i.test(b))
  const platform = data?.platform
  return [platform, brand].filter(Boolean).join(' · ')
}

export async function enablePush(user: User, publicKey: string): Promise<string> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('Bildirim izni verilmedi.')
  const registration = await navigator.serviceWorker.ready
  let subscription = await registration.pushManager.getSubscription()
  if (subscription && !sameKey(subscription, publicKey)) {
    await subscription.unsubscribe()
    subscription = null
  }
  subscription ??= await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes(publicKey),
  })
  const json = subscription.toJSON()
  const result = await api<{ id: string }>(user, 'PUT', '/v1/push/subscriptions', {
    endpoint: json.endpoint,
    keys: json.keys,
    label: deviceLabel(),
  })
  return result.id
}

export async function disablePush(user: User): Promise<void> {
  const subscription = await currentSubscription()
  if (!subscription) return
  const id = await subscriptionId(subscription.endpoint)
  await api(user, 'DELETE', `/v1/push/subscriptions/${id}`)
  await subscription.unsubscribe()
}
