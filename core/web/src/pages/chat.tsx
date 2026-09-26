// A conversation. "/" is a new one: its id is created here and the address
// becomes /c/<id> with the first message. Text shared into the app from other
// apps arrives as a draft in the composer.
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { doc, onSnapshot } from 'firebase/firestore'
import { BrainIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import { PageHeader } from '@/components/app-shell'
import { Thread } from '@/components/chat/thread'
import { useJarvisRuntime } from '@/components/chat/use-jarvis-runtime'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useConversation } from '@/lib/data'
import { useSignedIn } from '@/lib/session'
import type { StoredSettings } from '@/lib/types'

export function useCurrentSettings(): { settings: StoredSettings | null; loading: boolean } {
  const { db, user } = useSignedIn()
  const [state, setState] = useState<{ settings: StoredSettings | null; loading: boolean }>({
    settings: null,
    loading: true,
  })
  useEffect(
    () =>
      onSnapshot(
        doc(db, 'users', user.uid, 'settings', 'current'),
        (snapshot) => setState({ settings: snapshot.exists() ? (snapshot.data() as StoredSettings) : null, loading: false }),
        () => setState({ settings: null, loading: false }),
      ),
    [db, user.uid],
  )
  return state
}

function Welcome() {
  const { settings, loading } = useCurrentSettings()
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-2 py-10 text-center">
      <img src="/icon-192.png" alt="" className="size-14 rounded-2xl shadow-sm" />
      <h2 className="text-2xl font-semibold tracking-tight">Ne yapalım?</h2>
      {!loading && !settings && (
        <div className="text-muted-foreground max-w-sm text-sm">
          <p>Henüz model bağlantısı yok. Mesajların yanıtlanması için önce bir model seç.</p>
          <Button asChild size="sm" className="mt-3">
            <Link to="/brain">
              <BrainIcon /> Beyin'i ayarla
            </Link>
          </Button>
        </div>
      )}
    </div>
  )
}

function ModelBadge() {
  const { settings } = useCurrentSettings()
  if (!settings) return null
  return (
    <Badge variant="secondary" asChild className="hidden max-w-48 truncate sm:inline-flex">
      <Link to="/brain" title="Model ayarları">
        {settings.settings.model.model}
      </Link>
    </Badge>
  )
}

function Conversation({ id, isNew, draft }: { id: string; isNew: boolean; draft?: string }) {
  const navigate = useNavigate()
  const conversation = useConversation(isNew ? undefined : id)
  const { runtime } = useJarvisRuntime(id, () => {
    if (isNew) navigate(`/c/${id}`, { replace: true })
  })

  useEffect(() => {
    if (draft) runtime.thread.composer.setText(draft)
  }, [draft, runtime])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title={conversation?.title || 'Yeni konuşma'}>
        <ModelBadge />
      </PageHeader>
      <div className="min-h-0 flex-1">
        <AssistantRuntimeProvider runtime={runtime}>
          <Thread welcome={<Welcome />} />
        </AssistantRuntimeProvider>
      </div>
    </div>
  )
}

export function ChatPage() {
  const { conversationId } = useParams()
  const location = useLocation()
  const [newId] = useState(() => crypto.randomUUID())
  const draft = (location.state as { draft?: string } | null)?.draft
  const id = conversationId ?? newId
  return <Conversation key={id} id={id} isNew={!conversationId} draft={draft} />
}

/** Web Share Target: text and links shared from other apps become a draft in a new conversation. */
export function ShareTargetPage() {
  const navigate = useNavigate()
  const location = useLocation()
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const draft = ['title', 'text', 'url']
      .map((name) => params.get(name)?.trim())
      .filter((value, index, all): value is string => Boolean(value) && all.indexOf(value) === index)
      .join('\n')
    navigate('/', { replace: true, state: draft ? { draft } : null })
  }, [location.search, navigate])
  return null
}
