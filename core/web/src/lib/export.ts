// Conversation export as Markdown, read from the owner's own Firestore data.
import { collection, getDocs, orderBy, query, type Firestore } from 'firebase/firestore'

import { saveBlob } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { errorTechnical, NOTICE_TEXT } from '@/lib/labels'
import type { Conversation, MessageDoc } from '@/lib/types'

const ROLE_HEADING: Record<string, string> = { user: 'Sen', assistant: 'JARVIS', error: 'Hata', notice: 'Not' }

export async function exportConversation(db: Firestore, uid: string, conversation: Conversation): Promise<void> {
  const snapshot = await getDocs(
    query(collection(db, 'users', uid, 'conversations', conversation.id, 'messages'), orderBy('position')),
  )
  const title = conversation.title || 'Konuşma'
  const lines = [`# ${title}`, '']
  for (const entry of snapshot.docs) {
    const message = { id: entry.id, ...entry.data() } as MessageDoc
    lines.push(`## ${ROLE_HEADING[message.role] ?? message.role} · ${formatDateTime(message.created_at)}`, '')
    if (message.role === 'error') lines.push(errorTechnical(message))
    else if (message.role === 'notice') lines.push(NOTICE_TEXT[message.type ?? ''] ?? message.type ?? '')
    else lines.push(message.text ?? '')
    lines.push('')
  }
  const safe = title.replace(/[^\p{L}\p{N}\- ]+/gu, '').trim().replace(/\s+/g, '-').slice(0, 60) || 'konusma'
  saveBlob(new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' }), `${safe}.md`)
}
