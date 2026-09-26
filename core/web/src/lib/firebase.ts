// Firebase services for this installation. The web config is not a secret and
// is served by the core at /config.json, so one build works for every
// installation. Firestore keeps a persistent local cache: conversations open
// instantly and stay readable offline; every change still goes through the API.
import { initializeApp, type FirebaseApp } from 'firebase/app'
import { getAuth, type Auth } from 'firebase/auth'
import {
  initializeFirestore,
  persistentLocalCache,
  persistentMultipleTabManager,
  type Firestore,
} from 'firebase/firestore'

export type Services = { app: FirebaseApp; auth: Auth; db: Firestore }

export async function initServices(): Promise<Services> {
  const response = await fetch('/config.json')
  if (!response.ok) throw new Error(`/config.json ${response.status}`)
  const config = (await response.json()) as { firebase: Record<string, string> }
  const app = initializeApp(config.firebase)
  const auth = getAuth(app)
  const db = initializeFirestore(app, {
    localCache: persistentLocalCache({ tabManager: persistentMultipleTabManager() }),
  })
  return { app, auth, db }
}
