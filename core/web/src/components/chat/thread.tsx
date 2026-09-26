// The conversation view, built from assistant-ui primitives with the parts of
// the registry `Thread` this product uses: markdown replies with code copy,
// per-message actions, a composer whose stop button cancels the turn, live turn
// progress and the tool activity of each answer.
import {
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from '@assistant-ui/react'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  RotateCcwIcon,
  ShieldCheckIcon,
  SquareIcon,
  Volume2Icon,
  VolumeXIcon,
} from 'lucide-react'
import { useState, type FC, type ReactNode } from 'react'
import { toast } from 'sonner'

import { MarkdownText } from '@/components/assistant-ui/elements/markdown-text'
import { TooltipIconButton } from '@/components/assistant-ui/elements/tooltip-icon-button'
import { ActivitySummary, TurnProgress } from '@/components/chat/activity'
import type { AssistantCustom } from '@/components/chat/use-jarvis-runtime'
import { Button } from '@/components/ui/button'
import { api, errorMessage } from '@/lib/api'
import { formatDateTime, formatNumber, formatTime } from '@/lib/format'
import { errorHint, errorTechnical, NOTICE_TEXT } from '@/lib/labels'
import { useSignedIn } from '@/lib/session'
import { cn } from '@/lib/utils'

export const Thread: FC<{ welcome: ReactNode }> = ({ welcome }) => (
  <ThreadPrimitive.Root
    className="bg-background flex h-full min-h-0 flex-col"
    style={{ ['--thread-max-width' as string]: '46rem' }}
  >
    <ThreadPrimitive.Viewport className="relative flex min-h-0 flex-1 flex-col overflow-y-auto scroll-smooth">
      <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-3 pt-4 sm:px-4">
        <AuiIf condition={(s) => s.thread.messages.length === 0 && !s.thread.isLoading}>{welcome}</AuiIf>
        <div className="mb-10 flex flex-col gap-y-5 empty:hidden">
          <ThreadPrimitive.Messages>{() => <ThreadMessage />}</ThreadPrimitive.Messages>
        </div>
        <ThreadPrimitive.ViewportFooter className="bg-background sticky bottom-0 mt-auto flex flex-col gap-2 pb-[max(env(safe-area-inset-bottom),0.75rem)]">
          <ThreadPrimitive.ScrollToBottom asChild>
            <TooltipIconButton
              tooltip="En alta git"
              variant="outline"
              className="bg-background absolute -top-11 z-10 size-8 self-center rounded-full disabled:invisible"
            >
              <ArrowDownIcon />
            </TooltipIconButton>
          </ThreadPrimitive.ScrollToBottom>
          <Composer />
        </ThreadPrimitive.ViewportFooter>
      </div>
    </ThreadPrimitive.Viewport>
  </ThreadPrimitive.Root>
)

const ThreadMessage: FC = () => {
  const role = useAuiState((s) => s.message.role)
  return role === 'user' ? <UserMessage /> : <AssistantMessage />
}

const Composer: FC = () => (
  <ComposerPrimitive.Root className="border-foreground/15 focus-within:border-foreground/30 bg-muted/40 flex w-full items-end gap-2 rounded-2xl border p-2 transition-colors">
    <ComposerPrimitive.Input
      placeholder="JARVIS'e yaz…"
      className="placeholder:text-muted-foreground/70 max-h-48 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-base leading-6 outline-none"
      rows={1}
      autoFocus
      aria-label="Mesaj"
    />
    <AuiIf condition={(s) => !s.thread.isRunning}>
      <ComposerPrimitive.Send asChild>
        <Button size="icon" className="size-9 shrink-0 rounded-full" aria-label="Gönder">
          <ArrowUpIcon className="size-4" />
        </Button>
      </ComposerPrimitive.Send>
    </AuiIf>
    <AuiIf condition={(s) => s.thread.isRunning}>
      <ComposerPrimitive.Cancel asChild>
        <Button size="icon" variant="secondary" className="size-9 shrink-0 rounded-full" aria-label="Durdur">
          <SquareIcon className="size-3.5 fill-current" />
        </Button>
      </ComposerPrimitive.Cancel>
    </AuiIf>
  </ComposerPrimitive.Root>
)

const MessageTime: FC<{ className?: string }> = ({ className }) => {
  const createdAt = useAuiState((s) => s.message.createdAt)
  if (!createdAt) return null
  return (
    <time className={cn('text-muted-foreground text-xs tabular-nums', className)} title={formatDateTime(createdAt)}>
      {formatTime(createdAt)}
    </time>
  )
}

const CopyButton: FC = () => (
  <ActionBarPrimitive.Copy asChild>
    <TooltipIconButton tooltip="Kopyala">
      <AuiIf condition={(s) => s.message.isCopied}>
        <CheckIcon />
      </AuiIf>
      <AuiIf condition={(s) => !s.message.isCopied}>
        <CopyIcon />
      </AuiIf>
    </TooltipIconButton>
  </ActionBarPrimitive.Copy>
)

const UserMessage: FC = () => {
  const optimistic = useAuiState((s) => Boolean(s.message.metadata.isOptimistic))
  return (
    <MessagePrimitive.Root className="group flex flex-col items-end gap-1" data-role="user">
      <div
        className={cn(
          'bg-primary text-primary-foreground max-w-[85%] rounded-2xl rounded-br-md px-4 py-2 whitespace-pre-wrap wrap-break-word',
          optimistic && 'opacity-70',
        )}
      >
        <MessagePrimitive.Parts />
      </div>
      <div className="flex items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
        {optimistic ? <span className="text-muted-foreground text-xs">Gönderiliyor…</span> : <MessageTime />}
        <ActionBarPrimitive.Root className="text-muted-foreground flex">
          <CopyButton />
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  )
}

const SpeakButton: FC = () => (
  <AuiIf condition={(s) => s.thread.capabilities.speech}>
    <AuiIf condition={(s) => s.message.speech == null}>
      <ActionBarPrimitive.Speak asChild>
        <TooltipIconButton tooltip="Sesli oku">
          <Volume2Icon />
        </TooltipIconButton>
      </ActionBarPrimitive.Speak>
    </AuiIf>
    <AuiIf condition={(s) => s.message.speech != null}>
      <ActionBarPrimitive.StopSpeaking asChild>
        <TooltipIconButton tooltip="Okumayı durdur">
          <VolumeXIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.StopSpeaking>
    </AuiIf>
  </AuiIf>
)

function usageText(custom: AssistantCustom): string | null {
  const usage = custom.usage
  if (!usage) return null
  const parts = [
    usage.input_tokens && `${formatNumber(usage.input_tokens)} girdi`,
    usage.output_tokens && `${formatNumber(usage.output_tokens)} çıktı`,
    usage.requests && `${usage.requests} model isteği`,
  ].filter(Boolean)
  return parts.length ? `Sağlayıcının bildirdiği: ${parts.join(' · ')}` : null
}

const CloseReconciliation: FC<{ turnId: string }> = ({ turnId }) => {
  const { user } = useSignedIn()
  const [busy, setBusy] = useState(false)
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy}
      onClick={async () => {
        setBusy(true)
        try {
          await api(user, 'POST', `/v1/turns/${turnId}/close`)
        } catch (error) {
          toast.error(`Kapatılamadı: ${errorMessage(error)}`)
        } finally {
          setBusy(false)
        }
      }}
    >
      <ShieldCheckIcon /> Kontrol ettim, kapat
    </Button>
  )
}

const AssistantMessage: FC = () => {
  const custom = useAuiState((s) => s.message.metadata.custom) as AssistantCustom | undefined
  if (!custom) return null

  if (custom.kind === 'pending') {
    return (
      <MessagePrimitive.Root className="px-1" data-role="assistant">
        <TurnProgress turn={custom.turn} activity={custom.activity} />
      </MessagePrimitive.Root>
    )
  }

  if (custom.kind === 'notice') {
    return (
      <MessagePrimitive.Root className="flex items-center gap-2 px-1" data-role="assistant">
        <span className="text-muted-foreground text-sm italic">{NOTICE_TEXT[custom.noticeType ?? ''] ?? custom.noticeType}</span>
        <MessageTime />
        <ActionBarPrimitive.Root className="text-muted-foreground flex">
          <ActionBarPrimitive.Reload asChild>
            <TooltipIconButton tooltip="Tekrar gönder">
              <RotateCcwIcon />
            </TooltipIconButton>
          </ActionBarPrimitive.Reload>
        </ActionBarPrimitive.Root>
      </MessagePrimitive.Root>
    )
  }

  if (custom.kind === 'error') {
    const error = { type: custom.errorType, detail: custom.errorDetail }
    const uncertain = custom.errorType === 'unresolved_tool_effects'
    return (
      <MessagePrimitive.Root className="px-1" data-role="assistant">
        <ActivitySummary items={custom.activity} />
        <div
          className={cn(
            'rounded-xl border p-3 text-sm',
            uncertain ? 'border-amber-500/50 bg-amber-500/5' : 'border-destructive/40 bg-destructive/5',
          )}
        >
          <p className="text-foreground">{errorHint(error) ?? 'Yanıt verilemedi.'}</p>
          <p className="text-muted-foreground mt-1 font-mono text-xs break-all">{errorTechnical(error)}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {uncertain ? (
              <CloseReconciliation turnId={custom.turnId} />
            ) : (
              <ActionBarPrimitive.Reload asChild>
                <Button size="sm" variant="outline">
                  <RotateCcwIcon /> Tekrar gönder
                </Button>
              </ActionBarPrimitive.Reload>
            )}
            <MessageTime className="ms-auto" />
          </div>
        </div>
      </MessagePrimitive.Root>
    )
  }

  const usage = usageText(custom)
  return (
    <MessagePrimitive.Root className="group px-1" data-role="assistant">
      <ActivitySummary items={custom.activity} />
      <div className="text-foreground leading-relaxed wrap-break-word">
        <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
      </div>
      <div className="mt-1 flex items-center gap-1">
        <ActionBarPrimitive.Root className="text-muted-foreground -ms-1 flex gap-0.5">
          <CopyButton />
          <SpeakButton />
          <ActionBarPrimitive.ExportMarkdown asChild>
            <TooltipIconButton tooltip="Markdown olarak indir">
              <DownloadIcon />
            </TooltipIconButton>
          </ActionBarPrimitive.ExportMarkdown>
        </ActionBarPrimitive.Root>
        <MessageTime className="ms-1" />
        {usage && (
          <span className="text-muted-foreground hidden truncate text-xs sm:inline" title={usage}>
            · {usage}
          </span>
        )}
      </div>
    </MessagePrimitive.Root>
  )
}
