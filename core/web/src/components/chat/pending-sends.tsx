// Messages the owner sent that the server has not echoed back yet. They live
// above the chat page so a new conversation keeps them while the route
// changes from "/" to "/c/<id>".
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export type PendingSend = {
  clientId: string
  conversationId: string
  text: string
  createdAt: Date
  turnId?: string
}

type PendingSends = {
  list: (conversationId: string) => PendingSend[]
  add: (send: PendingSend) => void
  update: (clientId: string, change: Partial<PendingSend>) => void
  remove: (clientId: string) => void
}

const Context = createContext<PendingSends | null>(null)

export function PendingSendsProvider({ children }: { children: ReactNode }) {
  const [sends, setSends] = useState<PendingSend[]>([])
  const list = useCallback((id: string) => sends.filter((send) => send.conversationId === id), [sends])
  const add = useCallback((send: PendingSend) => setSends((all) => [...all, send]), [])
  const update = useCallback(
    (clientId: string, change: Partial<PendingSend>) =>
      setSends((all) => all.map((send) => (send.clientId === clientId ? { ...send, ...change } : send))),
    [],
  )
  const remove = useCallback((clientId: string) => setSends((all) => all.filter((s) => s.clientId !== clientId)), [])
  const value = useMemo(() => ({ list, add, update, remove }), [list, add, update, remove])
  return <Context.Provider value={value}>{children}</Context.Provider>
}

export function usePendingSends(): PendingSends {
  const value = useContext(Context)
  if (!value) throw new Error('usePendingSends outside PendingSendsProvider')
  return value
}
