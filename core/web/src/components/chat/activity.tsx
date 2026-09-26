// What the assistant did during a turn: tool calls recorded by the worker.
// Live while the turn runs, folded into one line once it has answered.
import { AlertTriangleIcon, CheckIcon, ChevronRightIcon, LoaderIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { durationBetween, formatDuration, toDate } from '@/lib/format'
import { describeActivity, TURN_STATUS } from '@/lib/labels'
import type { ActivityItem, Turn } from '@/lib/types'
import { cn } from '@/lib/utils'

function ActivityRow({ item }: { item: ActivityItem }) {
  const { title, detail } = describeActivity(item)
  const duration = durationBetween(item.started_at, item.ended_at)
  return (
    <li className="flex items-start gap-2 py-1 text-sm">
      <span className="mt-0.5 shrink-0" aria-hidden>
        {item.state === 'running' && <LoaderIcon className="text-primary size-4 animate-spin" />}
        {item.state === 'done' && <CheckIcon className="text-muted-foreground size-4" />}
        {item.state === 'retry' && <AlertTriangleIcon className="size-4 text-amber-500" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="text-foreground">{title}</span>
        {item.state === 'retry' && <span className="text-muted-foreground"> · model düzeltip yeniden denedi</span>}
        {detail && <span className="text-muted-foreground block truncate text-xs" title={detail}>{detail}</span>}
      </span>
      {duration !== null && <span className="text-muted-foreground shrink-0 text-xs tabular-nums">{formatDuration(duration)}</span>}
    </li>
  )
}

export function ActivityList({ items }: { items: ActivityItem[] }) {
  return (
    <ul className="border-border/60 ms-1 border-s ps-3">
      {items.map((item) => (
        <ActivityRow key={item.id} item={item} />
      ))}
    </ul>
  )
}

export function ActivitySummary({ items }: { items: ActivityItem[] }) {
  const [open, setOpen] = useState(false)
  if (items.length === 0) return null
  const first = items.map((i) => toDate(i.started_at)?.getTime()).filter((t): t is number => t !== undefined)
  const last = items.map((i) => toDate(i.ended_at)?.getTime()).filter((t): t is number => t !== undefined)
  const span = first.length && last.length ? Math.max(...last) - Math.min(...first) : null
  const failed = items.filter((i) => i.state === 'retry').length
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mb-2">
      <CollapsibleTrigger className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs transition-colors">
        <ChevronRightIcon className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
        {items.length} araç adımı{span !== null && ` · ${formatDuration(span)}`}
        {failed > 0 && ` · ${failed} yeniden deneme`}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-1">
        <ActivityList items={items} />
      </CollapsibleContent>
    </Collapsible>
  )
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  return now
}

/** Status line and live activity of a turn that has not answered yet. */
export function TurnProgress({ turn, activity }: { turn?: Turn; activity: ActivityItem[] }) {
  const now = useNow(true)
  const since = toDate(turn?.started_at) ?? toDate(turn?.created_at)
  const running = activity.find((item) => item.state === 'running')
  let label: string
  if (!turn) label = 'Gönderiliyor…'
  else if (turn.cancel_requested_at) label = 'İptal ediliyor…'
  else if (turn.status === 'queued') label = turn.attempt > 0 ? `Yeniden denenecek (deneme ${turn.attempt + 1})` : 'Sırada'
  else if (turn.status === 'running') label = running ? describeActivity(running).title + '…' : 'Düşünüyor…'
  else label = TURN_STATUS[turn.status]
  return (
    <div className="py-1" role="status" aria-live="polite">
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        <span className="relative flex size-2.5" aria-hidden>
          <span className="bg-primary absolute inline-flex size-full animate-ping rounded-full opacity-60 motion-reduce:animate-none" />
          <span className="bg-primary relative inline-flex size-2.5 rounded-full" />
        </span>
        <span className="text-foreground">{label}</span>
        {since && <span className="tabular-nums">{formatDuration(now - since.getTime())}</span>}
      </div>
      {turn?.error && turn.status === 'queued' && (
        <p className="text-muted-foreground mt-1 text-xs">Önceki deneme: {turn.error.type} {turn.error.detail}</p>
      )}
      {activity.length > 0 && (
        <div className="mt-2">
          <ActivityList items={activity} />
        </div>
      )}
    </div>
  )
}
