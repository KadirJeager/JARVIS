// Live Firestore reads. Security rules limit every read to the signed-in
// owner's documents; writes go through the API.
import {
  collection,
  doc,
  limit,
  onSnapshot,
  orderBy,
  query,
  where,
  type DocumentData,
  type FirestoreError,
} from 'firebase/firestore'
import { useEffect, useMemo, useState } from 'react'

import { useSignedIn } from '@/lib/session'
import type { Conversation, MessageDoc, Turn } from '@/lib/types'

type Live<T> = { data: T; loading: boolean; error: FirestoreError | null }

export function useConversations(max = 300): Live<Conversation[]> {
  const { db, user } = useSignedIn()
  const [state, setState] = useState<Live<Conversation[]>>({ data: [], loading: true, error: null })
  useEffect(() => {
    const ref = query(collection(db, 'users', user.uid, 'conversations'), orderBy('updated_at', 'desc'), limit(max))
    return onSnapshot(
      ref,
      (snapshot) =>
        setState({
          data: snapshot.docs.map((entry) => ({ id: entry.id, ...(entry.data() as DocumentData) }) as Conversation),
          loading: false,
          error: null,
        }),
      (error) => setState((previous) => ({ ...previous, loading: false, error })),
    )
  }, [db, user.uid, max])
  return state
}

export function useConversation(conversationId: string | undefined): Conversation | null {
  const { db, user } = useSignedIn()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  useEffect(() => {
    setConversation(null)
    if (!conversationId) return
    return onSnapshot(doc(db, 'users', user.uid, 'conversations', conversationId), (snapshot) =>
      setConversation(snapshot.exists() ? ({ id: snapshot.id, ...snapshot.data() } as Conversation) : null),
    )
  }, [db, user.uid, conversationId])
  return conversation
}

export function useMessages(conversationId: string | undefined): Live<MessageDoc[]> {
  const { db, user } = useSignedIn()
  const [state, setState] = useState<Live<MessageDoc[]>>({ data: [], loading: Boolean(conversationId), error: null })
  useEffect(() => {
    setState({ data: [], loading: Boolean(conversationId), error: null })
    if (!conversationId) return
    const ref = query(
      collection(db, 'users', user.uid, 'conversations', conversationId, 'messages'),
      orderBy('position'),
    )
    return onSnapshot(
      ref,
      (snapshot) =>
        setState({
          data: snapshot.docs.map((entry) => ({ id: entry.id, ...entry.data() }) as MessageDoc),
          loading: false,
          error: null,
        }),
      (error) => setState((previous) => ({ ...previous, loading: false, error })),
    )
  }, [db, user.uid, conversationId])
  return state
}

/** Live turn documents for the given ids (turns whose reply has not arrived yet). */
export function useTurnDocs(turnIds: string[]): Map<string, Turn> {
  const { db } = useSignedIn()
  const key = [...turnIds].sort().join(',')
  const [turns, setTurns] = useState<Map<string, Turn>>(new Map())
  useEffect(() => {
    const ids = key ? key.split(',') : []
    setTurns((previous) => new Map([...previous].filter(([id]) => ids.includes(id))))
    const unsubscribes = ids.map((id) =>
      onSnapshot(doc(db, 'turns', id), (snapshot) => {
        setTurns((previous) => {
          const next = new Map(previous)
          if (snapshot.exists()) next.set(id, snapshot.data() as Turn)
          else next.delete(id)
          return next
        })
      }),
    )
    return () => unsubscribes.forEach((unsubscribe) => unsubscribe())
  }, [db, key])
  return turns
}

/** The owner's most recent turns across all conversations, newest first. */
export function useRecentTurns(max: number): Live<Turn[]> {
  const { db, user } = useSignedIn()
  const [state, setState] = useState<Live<Turn[]>>({ data: [], loading: true, error: null })
  useEffect(() => {
    const ref = query(collection(db, 'turns'), where('uid', '==', user.uid), orderBy('created_at', 'desc'), limit(max))
    return onSnapshot(
      ref,
      (snapshot) => setState({ data: snapshot.docs.map((entry) => entry.data() as Turn), loading: false, error: null }),
      (error) => setState((previous) => ({ ...previous, loading: false, error })),
    )
  }, [db, user.uid, max])
  return state
}

/** Turn ids of user messages that have no reply, error or notice yet. */
export function usePendingTurnIds(messages: MessageDoc[]): string[] {
  return useMemo(() => {
    const answered = new Set(messages.filter((m) => m.role !== 'user').map((m) => m.turn_id))
    return messages.filter((m) => m.role === 'user' && !answered.has(m.turn_id)).map((m) => m.turn_id)
  }, [messages])
}
