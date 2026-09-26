// Connects assistant-ui to JARVIS's event-driven turns.
//
// The conversation lives in Firestore: user messages, replies, errors and
// notices ordered by position. A user message without an answer has a turn
// that is queued or running; it is shown as a running assistant message whose
// live tool activity comes from the turn document. Sending posts to the API
// and shows the message at once until the server's copy arrives. Stop cancels
// the open turns; "send again" posts the same text as a new turn.
import { useExternalStoreRuntime, type AppendMessage, type ThreadMessageLike } from '@assistant-ui/react'
import { useEffect, useMemo } from 'react'
import { toast } from 'sonner'

import { usePendingSends, type PendingSend } from '@/components/chat/pending-sends'
import { DeviceSpeechAdapter, speechSupported } from '@/components/chat/speech'
import { api, errorMessage } from '@/lib/api'
import { useMessages, usePendingTurnIds, useTurnDocs } from '@/lib/data'
import { toDate } from '@/lib/format'
import { errorHint, errorTechnical } from '@/lib/labels'
import { useSignedIn } from '@/lib/session'
import type { ActivityItem, MessageDoc, Turn, Usage } from '@/lib/types'

export type MessageKind = 'reply' | 'error' | 'notice' | 'pending'

/** What the thread's assistant message reads from `metadata.custom`. */
export type AssistantCustom = {
  kind: MessageKind
  turnId: string
  activity: ActivityItem[]
  turn?: Turn
  usage?: Usage
  errorType?: string
  errorDetail?: string
  noticeType?: string
  startedAt?: string
  finishedAt?: string
}

const speech = speechSupported() ? new DeviceSpeechAdapter() : undefined

function iso(value: MessageDoc['created_at'] | Turn['started_at']): string | undefined {
  return toDate(value)?.toISOString()
}

function convert(
  docs: MessageDoc[],
  turns: Map<string, Turn>,
  sends: PendingSend[],
): ThreadMessageLike[] {
  const answered = new Set(docs.filter((m) => m.role !== 'user').map((m) => m.turn_id))
  const known = new Set(docs.map((m) => m.turn_id))
  const result: ThreadMessageLike[] = []
  for (const message of docs) {
    const createdAt = toDate(message.created_at) ?? undefined
    const activity = message.activity ?? []
    if (message.role === 'user') {
      result.push({ id: message.id, role: 'user', content: [{ type: 'text', text: message.text ?? '' }], createdAt })
      if (!answered.has(message.turn_id)) {
        const turn = turns.get(message.turn_id)
        const custom: AssistantCustom = {
          kind: 'pending',
          turnId: message.turn_id,
          activity: turn?.activity ?? [],
          turn,
          startedAt: iso(turn?.started_at),
        }
        result.push({
          id: `pending-${message.turn_id}`,
          role: 'assistant',
          content: [],
          status: { type: 'running' },
          metadata: { custom },
        })
      }
      continue
    }
    if (message.role === 'assistant') {
      const custom: AssistantCustom = { kind: 'reply', turnId: message.turn_id, activity }
      result.push({
        id: message.id,
        role: 'assistant',
        content: [{ type: 'text', text: message.text ?? '' }],
        createdAt,
        status: { type: 'complete', reason: 'stop' },
        metadata: { custom },
      })
      continue
    }
    if (message.role === 'error') {
      const hint = errorHint(message) ?? 'Yanıt verilemedi.'
      const custom: AssistantCustom = {
        kind: 'error',
        turnId: message.turn_id,
        activity,
        errorType: message.type,
        errorDetail: message.detail,
      }
      result.push({
        id: message.id,
        role: 'assistant',
        content: [],
        createdAt,
        status: { type: 'incomplete', reason: 'error', error: `${hint}\n${errorTechnical(message)}` },
        metadata: { custom },
      })
      continue
    }
    const custom: AssistantCustom = { kind: 'notice', turnId: message.turn_id, activity, noticeType: message.type }
    result.push({
      id: message.id,
      role: 'assistant',
      content: [],
      createdAt,
      status: { type: 'incomplete', reason: 'cancelled' },
      metadata: { custom },
    })
  }
  for (const send of sends) {
    if (send.turnId && known.has(send.turnId)) continue
    result.push({
      id: `sending-${send.clientId}`,
      role: 'user',
      content: [{ type: 'text', text: send.text }],
      createdAt: send.createdAt,
      metadata: { isOptimistic: true },
    })
    result.push({
      id: `sending-reply-${send.clientId}`,
      role: 'assistant',
      content: [],
      status: { type: 'running' },
      metadata: { custom: { kind: 'pending', turnId: send.turnId ?? '', activity: [] } satisfies AssistantCustom },
    })
  }
  return result
}

function textOf(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === 'text' ? part.text : ''))
    .join('\n')
    .trim()
}

export function useJarvisRuntime(conversationId: string, onFirstSend: () => void) {
  const { user } = useSignedIn()
  const pendingSends = usePendingSends()
  const messages = useMessages(conversationId)
  const pendingTurnIds = usePendingTurnIds(messages.data)
  const turns = useTurnDocs(pendingTurnIds)
  const sends = pendingSends.list(conversationId)

  const converted = useMemo(() => convert(messages.data, turns, sends), [messages.data, turns, sends])

  // The server's copy of a sent message has arrived: the local one is no longer needed.
  useEffect(() => {
    const known = new Set(messages.data.map((m) => m.turn_id))
    for (const sent of sends) if (sent.turnId && known.has(sent.turnId)) pendingSends.remove(sent.clientId)
  }, [messages.data, sends, pendingSends])
  const unconfirmed = sends.some((send) => !send.turnId || !messages.data.some((m) => m.turn_id === send.turnId))

  const send = async (text: string) => {
    const clientId = crypto.randomUUID()
    const isFirst = messages.data.length === 0 && sends.length === 0
    pendingSends.add({ clientId, conversationId, text, createdAt: new Date() })
    if (isFirst) onFirstSend()
    try {
      const result = await api<{ turn_id: string; dispatch: string }>(
        user,
        'POST',
        `/v1/conversations/${conversationId}/messages`,
        { client_message_id: clientId, text },
      )
      pendingSends.update(clientId, { turnId: result.turn_id })
      if (result.dispatch === 'deferred') toast.info('Sıraya alındı; iletim birkaç dakika içinde yeniden denenecek.')
    } catch (error) {
      pendingSends.remove(clientId)
      toast.error(`Gönderilemedi: ${errorMessage(error)}`, {
        action: { label: 'Tekrar dene', onClick: () => void send(text) },
        duration: 15_000,
      })
    }
  }

  const cancelOpenTurns = async () => {
    const open = pendingTurnIds.filter((id) => {
      const status = turns.get(id)?.status
      return status === 'queued' || status === 'running' || status === undefined
    })
    await Promise.all(
      open.map((id) =>
        api(user, 'POST', `/v1/turns/${id}/cancel`).catch((error: unknown) =>
          toast.error(`İptal edilemedi: ${errorMessage(error)}`),
        ),
      ),
    )
  }

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: converted,
    convertMessage: (message) => message,
    isRunning: pendingTurnIds.length > 0 || unconfirmed,
    isLoading: messages.loading,
    onNew: async (message) => {
      const text = textOf(message)
      if (text) await send(text)
    },
    onCancel: cancelOpenTurns,
    onReload: async (parentId) => {
      const parent = messages.data.find((m) => m.id === parentId && m.role === 'user')
      if (parent?.text) await send(parent.text)
    },
    adapters: { speech },
  })

  return { runtime, loading: messages.loading, error: messages.error, resend: send }
}
