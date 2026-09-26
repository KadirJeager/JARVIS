// Sign-in and the installation's account check. Firebase accepts any Google
// account; the core only serves accounts on its allowlist, so the app asks the
// core once after sign-in and explains a refusal instead of failing later.
import { GoogleAuthProvider, signInWithPopup, signOut } from 'firebase/auth'
import { useEffect, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { ApiError, api, errorMessage } from '@/lib/api'
import { useSession } from '@/lib/session'

function Centered({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-5 p-6 text-center">
      <img src="/icon-192.png" alt="" className="size-16 rounded-2xl shadow-sm" />
      {children}
    </main>
  )
}

export function SignInPage() {
  const { services } = useSession()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <Centered>
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">JARVIS</h1>
        <p className="text-muted-foreground text-sm">Devam etmek için bu kuruluma izin verilmiş Google hesabınla giriş yap.</p>
      </div>
      <Button
        size="lg"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            await signInWithPopup(services.auth, new GoogleAuthProvider())
          } catch (e) {
            const code = (e as { code?: string }).code
            if (code !== 'auth/popup-closed-by-user' && code !== 'auth/cancelled-popup-request') {
              setError(`Giriş yapılamadı: ${code ?? errorMessage(e)}`)
            }
          } finally {
            setBusy(false)
          }
        }}
      >
        Google ile giriş yap
      </Button>
      {error && (
        <p className="text-destructive max-w-sm text-sm" role="alert">
          {error}
        </p>
      )}
    </Centered>
  )
}

type Access = { state: 'checking' } | { state: 'allowed' } | { state: 'denied' } | { state: 'error'; message: string }

export function AccessGate({ children }: { children: ReactNode }) {
  const { services, user } = useSession()
  const [access, setAccess] = useState<Access>({ state: 'checking' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!user) return
    setAccess({ state: 'checking' })
    api(user, 'GET', '/v1/me')
      .then(() => setAccess({ state: 'allowed' }))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 403) setAccess({ state: 'denied' })
        // Offline: cached conversations stay readable; actions report the missing connection themselves.
        else if (error instanceof ApiError && error.status === 0) setAccess({ state: 'allowed' })
        else setAccess({ state: 'error', message: errorMessage(error) })
      })
  }, [user, attempt])

  if (access.state === 'allowed') return children
  if (access.state === 'checking') {
    return (
      <Centered>
        <Spinner />
      </Centered>
    )
  }
  return (
    <Centered>
      <p className="max-w-sm text-sm">
        {access.state === 'denied'
          ? `${user?.email} bu JARVIS kurulumunu kullanamaz. İzinli hesapla giriş yap.`
          : `Sunucuya bağlanılamadı: ${access.message}`}
      </p>
      <div className="flex gap-2">
        {access.state === 'error' && <Button onClick={() => setAttempt((n) => n + 1)}>Tekrar dene</Button>}
        <Button variant="outline" onClick={() => signOut(services.auth)}>
          Çıkış yap
        </Button>
      </div>
    </Centered>
  )
}
