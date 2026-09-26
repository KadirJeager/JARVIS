// Turn ledger: every message the assistant worked on, across conversations,
// with its state, timing, attempts, tool steps, error and reported usage.
import { ChevronRightIcon, ExternalLinkIcon, ShieldCheckIcon, SquareIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { PageHeader } from '@/components/app-shell'
import { ActivityList } from '@/components/chat/activity'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, errorMessage } from '@/lib/api'
import { useRecentTurns } from '@/lib/data'
import { durationBetween, formatDateTime, formatDuration, formatNumber, formatRelative } from '@/lib/format'
import { errorHint, errorTechnical, TURN_STATUS } from '@/lib/labels'
import { useSignedIn } from '@/lib/session'
import type { Turn, TurnStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

const FILTERS: { key: string; label: string; statuses: TurnStatus[] | null }[] = [
  { key: 'all', label: 'Tümü', statuses: null },
  { key: 'open', label: 'Açık', statuses: ['queued', 'running'] },
  { key: 'failed', label: 'Başarısız', statuses: ['failed'] },
  { key: 'reconciling', label: 'Kontrol', statuses: ['reconciling'] },
  { key: 'cancelled', label: 'İptal', statuses: ['cancelled'] },
]

const STATUS_STYLE: Record<TurnStatus, string> = {
  queued: 'bg-secondary text-secondary-foreground',
  running: 'bg-primary/15 text-primary',
  completed: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  failed: 'bg-destructive/15 text-destructive',
  reconciling: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  cancelled: 'bg-muted text-muted-foreground',
}

export function StatusBadge({ status }: { status: TurnStatus }) {
  return <Badge className={cn('border-0', STATUS_STYLE[status])}>{TURN_STATUS[status] ?? status}</Badge>
}

function turnDuration(turn: Turn): number | null {
  return durationBetween(turn.started_at ?? turn.created_at, turn.finished_at)
}

function TurnRow({ turn }: { turn: Turn }) {
  const { user } = useSignedIn()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const duration = turnDuration(turn)
  const activity = turn.activity ?? []
  const usage = turn.result?.usage

  const act = async (path: string, failure: string) => {
    setBusy(true)
    try {
      await api(user, 'POST', path)
    } catch (error) {
      toast.error(`${failure}: ${errorMessage(error)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <li className="border-b last:border-b-0">
        <CollapsibleTrigger className="hover:bg-muted/50 flex w-full items-start gap-3 px-3 py-3 text-start transition-colors">
          <ChevronRightIcon className={cn('text-muted-foreground mt-0.5 size-4 shrink-0 transition-transform', open && 'rotate-90')} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{turn.text.split('\n')[0]}</p>
            <p className="text-muted-foreground mt-0.5 flex flex-wrap gap-x-2 text-xs">
              <span title={formatDateTime(turn.created_at)}>{formatRelative(turn.created_at)}</span>
              {duration !== null && <span>· {formatDuration(duration)}</span>}
              {activity.length > 0 && <span>· {activity.length} araç adımı</span>}
              {turn.attempt > 1 && <span>· {turn.attempt} deneme</span>}
            </p>
          </div>
          <StatusBadge status={turn.status} />
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-3 px-3 pb-4 ps-10 text-sm">
          {turn.error && (
            <div className="rounded-lg border p-2">
              <p>{errorHint(turn.error) ?? 'Hata'}</p>
              <p className="text-muted-foreground font-mono text-xs break-all">{errorTechnical(turn.error)}</p>
            </div>
          )}
          {activity.length > 0 && <ActivityList items={activity} />}
          <dl className="text-muted-foreground grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt>Gönderildi</dt>
            <dd>{formatDateTime(turn.created_at)}</dd>
            {turn.finished_at && (
              <>
                <dt>Bitti</dt>
                <dd>{formatDateTime(turn.finished_at)}</dd>
              </>
            )}
            {usage && (
              <>
                <dt>Kullanım</dt>
                <dd>
                  {[
                    usage.requests && `${usage.requests} model isteği`,
                    usage.input_tokens && `${formatNumber(usage.input_tokens)} girdi`,
                    usage.output_tokens && `${formatNumber(usage.output_tokens)} çıktı`,
                  ]
                    .filter(Boolean)
                    .join(' · ') || 'bildirilmedi'}
                </dd>
              </>
            )}
            <dt>Tur</dt>
            <dd className="font-mono break-all">{turn.turn_id}</dd>
          </dl>
          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm" variant="outline">
              <Link to={`/c/${turn.conversation_id}`}>
                <ExternalLinkIcon /> Konuşmaya git
              </Link>
            </Button>
            {(turn.status === 'queued' || turn.status === 'running') && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy || Boolean(turn.cancel_requested_at)}
                onClick={() => act(`/v1/turns/${turn.turn_id}/cancel`, 'İptal edilemedi')}
              >
                <SquareIcon /> {turn.cancel_requested_at ? 'İptal ediliyor…' : 'İptal et'}
              </Button>
            )}
            {turn.status === 'reconciling' && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act(`/v1/turns/${turn.turn_id}/close`, 'Kapatılamadı')}>
                <ShieldCheckIcon /> Kontrol ettim, kapat
              </Button>
            )}
          </div>
        </CollapsibleContent>
      </li>
    </Collapsible>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="gap-1 py-3">
      <CardContent className="px-3">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="text-xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
      </CardContent>
    </Card>
  )
}

const WINDOW = 200

export function ActivityPage() {
  const { data, loading, error } = useRecentTurns(WINDOW)
  const [filter, setFilter] = useState('all')

  const stats = useMemo(() => {
    const durations = data
      .filter((t) => t.status === 'completed')
      .map(turnDuration)
      .filter((d): d is number => d !== null)
      .sort((a, b) => a - b)
    const median = durations.length ? durations[Math.floor(durations.length / 2)] : null
    const count = (statuses: TurnStatus[]) => data.filter((t) => statuses.includes(t.status)).length
    const tools = data.reduce((sum, t) => sum + (t.activity?.length ?? 0), 0)
    return { median, open: count(['queued', 'running']), failed: count(['failed', 'reconciling']), done: count(['completed']), tools }
  }, [data])

  const statuses = FILTERS.find((f) => f.key === filter)?.statuses
  const visible = statuses ? data.filter((t) => statuses.includes(t.status)) : data

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Etkinlik" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl space-y-4 p-3 sm:p-6">
          <p className="text-muted-foreground text-sm">
            Son {WINDOW} turun kaydı. Her mesaj bir tur olarak sıraya girer, işlenir ve sonucuyla kapanır.
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Tamamlanan" value={formatNumber(stats.done)} />
            <Stat label="Açık" value={formatNumber(stats.open)} />
            <Stat label="Başarısız / kontrol" value={formatNumber(stats.failed)} />
            <Stat
              label="Ortanca süre"
              value={stats.median !== null ? formatDuration(stats.median) : '—'}
              hint={`${formatNumber(stats.tools)} araç adımı`}
            />
          </div>
          <Tabs value={filter} onValueChange={setFilter}>
            <TabsList className="w-full justify-start overflow-x-auto sm:w-auto">
              {FILTERS.map((f) => (
                <TabsTrigger key={f.key} value={f.key}>
                  {f.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {error && <p className="text-destructive text-sm">Kayıtlar okunamadı: {error.message}</p>}
          <Card className="py-0">
            {loading ? (
              <div className="space-y-3 p-3">
                {Array.from({ length: 6 }, (_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : visible.length === 0 ? (
              <p className="text-muted-foreground p-6 text-center text-sm">Bu filtrede tur yok.</p>
            ) : (
              <ul>
                {visible.map((turn) => (
                  <TurnRow key={turn.turn_id} turn={turn} />
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
