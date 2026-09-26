// Dates and numbers in the device's own locale and clock format (the browser
// follows the operating system); the app adds no format setting of its own.
import { Timestamp } from 'firebase/firestore'

export type DateLike = Timestamp | Date | string | null | undefined

export function toDate(value: DateLike): Date | null {
  if (!value) return null
  if (value instanceof Timestamp) return value.toDate()
  if (value instanceof Date) return value
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const timeOnly = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' })
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const integer = new Intl.NumberFormat()

export function formatDateTime(value: DateLike): string {
  const date = toDate(value)
  return date ? dateTime.format(date) : '—'
}

export function formatTime(value: DateLike): string {
  const date = toDate(value)
  return date ? timeOnly.format(date) : ''
}

export function formatRelative(value: DateLike, now = Date.now()): string {
  const date = toDate(value)
  if (!date) return '—'
  const seconds = Math.round((date.getTime() - now) / 1000)
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ['second', 60], ['minute', 60], ['hour', 24], ['day', 7], ['week', 4.35], ['month', 12], ['year', Infinity],
  ]
  let amount = seconds
  for (const [unit, size] of steps) {
    if (Math.abs(amount) < size) return relative.format(Math.round(amount), unit)
    amount /= size
  }
  return dateTime.format(date)
}

export function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.round(milliseconds / 1000))
  if (seconds < 60) return `${seconds} sn`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} dk ${seconds % 60} sn`
}

export function durationBetween(start: DateLike, end: DateLike): number | null {
  const from = toDate(start)
  const to = toDate(end)
  return from && to ? to.getTime() - from.getTime() : null
}

export function formatNumber(value: number): string {
  return integer.format(value)
}

export type DateGroup = 'today' | 'yesterday' | 'week' | 'month' | 'older'

export const DATE_GROUP_LABELS: Record<DateGroup, string> = {
  today: 'Bugün',
  yesterday: 'Dün',
  week: 'Son 7 gün',
  month: 'Son 30 gün',
  older: 'Daha eski',
}

export function dateGroup(value: DateLike, now = new Date()): DateGroup {
  const date = toDate(value) ?? new Date(0)
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 24 * 60 * 60 * 1000
  const time = date.getTime()
  if (time >= startOfToday) return 'today'
  if (time >= startOfToday - day) return 'yesterday'
  if (time >= startOfToday - 7 * day) return 'week'
  if (time >= startOfToday - 30 * day) return 'month'
  return 'older'
}

/** Case- and accent-insensitive match for Turkish text (İ/ı, ş, ğ …). */
export function normalizeForSearch(text: string): string {
  return text
    .toLocaleLowerCase('tr')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/ı/g, 'i')
}
