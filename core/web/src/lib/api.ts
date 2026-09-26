// Authenticated calls to the core API. Every request carries the signed-in
// user's Firebase ID token; failures become `ApiError` with the HTTP status and
// the server's detail so screens can explain what happened.
import type { User } from 'firebase/auth'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

async function request(user: User, method: string, path: string, body?: unknown): Promise<Response> {
  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${await user.getIdToken()}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'network')
  }
  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as { detail?: unknown }
    const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? response.statusText)
    throw new ApiError(response.status, detail)
  }
  return response
}

export async function api<T>(user: User, method: string, path: string, body?: unknown): Promise<T> {
  return (await (await request(user, method, path, body)).json()) as T
}

/** Download an API response as a file, named by the server when it says so. */
export async function download(user: User, path: string, fallbackName: string): Promise<void> {
  const response = await request(user, 'GET', path)
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName
  saveBlob(await response.blob(), name)
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = Object.assign(document.createElement('a'), { href: url, download: name })
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return 'Sunucuya ulaşılamadı; bağlantını kontrol et.'
    if (error.status === 401) return 'Oturum süresi doldu; yeniden giriş yap.'
    if (error.status === 403) return `İzin yok: ${error.message}`
    if (error.status === 409) return `Çakışma: ${error.message}`
    return `${error.status}: ${error.message}`
  }
  return error instanceof Error ? error.message : String(error)
}
