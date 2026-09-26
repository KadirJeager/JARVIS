// Shapes of the Firestore documents and API responses the PWA reads. They
// mirror `jarvis_core.turns`, `settings`, `notify` and `api`.
import type { Timestamp } from 'firebase/firestore'

export type TurnStatus = 'queued' | 'running' | 'completed' | 'failed' | 'reconciling' | 'cancelled'

export type ActivityItem = {
  id: string
  tool: string
  args: string
  state: 'running' | 'done' | 'retry'
  started_at?: Timestamp
  ended_at?: Timestamp
}

export type TurnError = { type: string; detail: string }

export type Usage = Partial<
  Record<'requests' | 'tool_calls' | 'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'cache_write_tokens', number>
>

export type Turn = {
  turn_id: string
  uid: string
  conversation_id: string
  text: string
  seq: number
  status: TurnStatus
  attempt: number
  run_ids: string[]
  error: TurnError | null
  result: { reply: string; history_run_id: string; usage?: Usage } | null
  activity?: ActivityItem[]
  cancel_requested_at?: Timestamp | null
  created_at: Timestamp
  updated_at: Timestamp
  started_at?: Timestamp
  finished_at?: Timestamp
}

export type MessageRole = 'user' | 'assistant' | 'error' | 'notice'

export type MessageDoc = {
  id: string
  role: MessageRole
  text?: string
  type?: string
  detail?: string
  turn_id: string
  position: number
  created_at?: Timestamp
  activity?: ActivityItem[]
}

export type Conversation = {
  id: string
  title?: string
  pinned?: boolean
  last_seq?: number
  settled_seq?: number
  created_at?: Timestamp
  updated_at?: Timestamp
}

export type Protocol = 'google' | 'openai'

export type AssistantSettings = {
  instructions: string
  model: { protocol: Protocol; base_url: string | null; api_key_ref: string | null; model: string }
  tool_selection: { strategy: 'builtin' | 'typesafe_jev'; jev_model: string; api_key_ref: string | null }
  limits: { request_limit: number; tool_calls_limit: number }
}

export type StoredSettings = { version: number; settings: AssistantSettings; updated_at: string | Timestamp }

export type MemoryFileInfo = { path: string; version: string; chars: number; updated_at: string | null }

export type Capability = { id: string; loading: 'core' | 'deferred'; tools: string; strategy?: string }

export type PushSubscriptionInfo = {
  id: string
  label: string
  created_at?: string
  updated_at?: string
  last_success_at?: string
  last_failure?: { status: number | null; error?: string; at: string }
}

export type PushStatus =
  | { configured: false }
  | { configured: true; public_key: string; subscriptions: PushSubscriptionInfo[] }

export type SystemStatus = {
  service: { revision: string | null; build: string | null; project_id: string; service_url: string }
  account: { email: string }
  settings: null | {
    version: number
    updated_at: string
    protocol: Protocol
    base_url: string | null
    model: string
    tool_selection: string
  }
  memory: {
    files: number
    total_chars: number
    main_file: string
    main_chars: number | null
    main_lines: number | null
    injection_max_tokens: number
    injection_max_lines: number
  }
  push: PushStatus
  capabilities: Capability[]
}

export type SecretItem = { id: string; ref: string; managed: boolean; created_at: string | null }
