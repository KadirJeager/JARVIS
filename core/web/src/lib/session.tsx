// Signed-in session: Firebase services plus the current user, shared through
// context so pages call the API and read Firestore without prop drilling.
import { onAuthStateChanged, type User } from 'firebase/auth'
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import type { Services } from '@/lib/firebase'

type SessionState = { services: Services; user: User | null; ready: boolean }

const SessionContext = createContext<SessionState | null>(null)

export function SessionProvider({ services, children }: { services: Services; children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ services, user: services.auth.currentUser, ready: false })
  useEffect(
    () => onAuthStateChanged(services.auth, (user) => setState({ services, user, ready: true })),
    [services],
  )
  return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>
}

export function useSession(): SessionState {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession outside SessionProvider')
  return value
}

/** Services and the signed-in user; only for screens rendered after sign-in. */
export function useSignedIn(): Services & { user: User } {
  const { services, user } = useSession()
  if (!user) throw new Error('useSignedIn without a signed-in user')
  return { ...services, user }
}
