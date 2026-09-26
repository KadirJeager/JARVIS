// The assistant's cloud memory (vault): see, search, correct, add, forget and
// export the markdown files it reads every turn. Saving checks the version the
// owner opened, so a change the assistant made meanwhile is never overwritten.
import { ArrowLeftIcon, DownloadIcon, FilePlusIcon, FileTextIcon, SearchIcon, Trash2Icon, XIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { PageHeader } from '@/components/app-shell'
import { Markdown } from '@/components/markdown'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, api, download, errorMessage } from '@/lib/api'
import { formatDateTime, formatNumber, formatRelative } from '@/lib/format'
import { useSignedIn } from '@/lib/session'
import type { MemoryFileInfo, SystemStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

type OpenFile = { path: string; content: string; version: string | null; truncated: boolean }
type SearchMatch = { path: string; snippet: string; score: number }

const PATH_RE = /^[A-Za-z0-9_.-]{1,200}(\/[A-Za-z0-9_.-]{1,200})*$/
const memoryUrl = (path: string) => `/v1/memory/${path.split('/').map(encodeURIComponent).join('/')}`

function BudgetCard({ memory }: { memory: SystemStatus['memory'] }) {
  const budget = memory.injection_max_tokens * 4
  const used = memory.main_chars ?? 0
  const ratio = Math.min(1, used / budget)
  const lines = memory.main_lines ?? 0
  const over = used > budget || lines > memory.injection_max_lines
  return (
    <Card className="gap-2 py-3">
      <CardContent className="space-y-2 px-3 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium">{memory.main_file} her tura eklenir</span>
          {over && <Badge variant="destructive">Sınır aşıldı</Badge>}
        </div>
        <div className="bg-muted h-2 overflow-hidden rounded-full" aria-hidden>
          <div className={cn('h-full rounded-full', over ? 'bg-destructive' : 'bg-primary')} style={{ width: `${ratio * 100}%` }} />
        </div>
        <p className="text-muted-foreground text-xs">
          {formatNumber(used)} / yaklaşık {formatNumber(budget)} karakter · {formatNumber(lines)} /{' '}
          {memory.injection_max_lines} satır. Sınırı aşan kısım tura eklenmez; model gerektiğinde dosyayı araçla okur.
          Toplam {memory.files} dosya, {formatNumber(memory.total_chars)} karakter.
        </p>
      </CardContent>
    </Card>
  )
}

export function MemoryPage() {
  const { user } = useSignedIn()
  const [files, setFiles] = useState<MemoryFileInfo[] | null>(null)
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [open, setOpen] = useState<OpenFile | null>(null)
  const [draft, setDraft] = useState('')
  const [tab, setTab] = useState('view')
  const [saving, setSaving] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<SearchMatch[] | null>(null)
  const [creating, setCreating] = useState(false)
  const [newPath, setNewPath] = useState('')
  const [forgetting, setForgetting] = useState(false)

  const load = useCallback(async () => {
    setListError(null)
    try {
      const [list, current] = await Promise.all([
        api<{ files: MemoryFileInfo[] }>(user, 'GET', '/v1/memory'),
        api<SystemStatus>(user, 'GET', '/v1/status'),
      ])
      setFiles(list.files)
      setStatus(current)
    } catch (error) {
      setListError(errorMessage(error))
      setFiles([])
    }
  }, [user])

  useEffect(() => {
    void load()
  }, [load])

  const dirty = open !== null && draft !== open.content

  const openFile = async (path: string) => {
    if (dirty && !confirm('Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?')) return
    try {
      const file = await api<OpenFile>(user, 'GET', memoryUrl(path))
      setOpen(file)
      setDraft(file.content)
      setConflict(false)
      setTab('view')
    } catch (error) {
      toast.error(`Açılamadı: ${errorMessage(error)}`)
    }
  }

  const save = async () => {
    if (!open) return
    setSaving(true)
    try {
      const result = await api<{ version: string }>(user, 'PUT', memoryUrl(open.path), {
        content: draft,
        expected_version: open.version,
      })
      setOpen({ ...open, content: draft, version: result.version })
      setConflict(false)
      toast.success('Kaydedildi. Sonraki turdan itibaren kullanılır.')
      void load()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true)
      else toast.error(`Kaydedilemedi: ${errorMessage(error)}`)
    } finally {
      setSaving(false)
    }
  }

  const forget = async () => {
    if (!open?.version) return
    setForgetting(false)
    try {
      await api(user, 'DELETE', `${memoryUrl(open.path)}?expected_version=${encodeURIComponent(open.version)}`)
      toast.success(`${open.path} unutuldu.`)
      setOpen(null)
      void load()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true)
      else toast.error(`Silinemedi: ${errorMessage(error)}`)
    }
  }

  const create = () => {
    const path = newPath.trim()
    if (!PATH_RE.test(path) || path.includes('..')) return
    if (files?.some((f) => f.path === path)) {
      toast.error('Bu adla bir dosya zaten var.')
      return
    }
    setCreating(false)
    setOpen({ path, content: '', version: null, truncated: false })
    setDraft('')
    setTab('edit')
  }

  const search = async () => {
    const q = query.trim()
    if (!q) {
      setMatches(null)
      return
    }
    try {
      const result = await api<{ matches: SearchMatch[] }>(user, 'GET', `/v1/memory-search?q=${encodeURIComponent(q)}`)
      setMatches(result.matches)
    } catch (error) {
      toast.error(`Aranamadı: ${errorMessage(error)}`)
    }
  }

  const sorted = useMemo(
    () => [...(files ?? [])].sort((a, b) => (a.path === status?.memory.main_file ? -1 : b.path === status?.memory.main_file ? 1 : a.path.localeCompare(b.path))),
    [files, status],
  )

  const list = (
    <div className="space-y-3">
      {status && <BudgetCard memory={status.memory} />}
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void search()
        }}
      >
        <div className="relative flex-1">
          <SearchIcon className="text-muted-foreground pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Hafızada ara" className="ps-8" />
        </div>
        {matches && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Aramayı temizle"
            onClick={() => {
              setQuery('')
              setMatches(null)
            }}
          >
            <XIcon />
          </Button>
        )}
      </form>
      {listError && <p className="text-destructive text-sm">{listError}</p>}
      {matches ? (
        <Card className="py-0">
          {matches.length === 0 ? (
            <p className="text-muted-foreground p-4 text-sm">Eşleşme yok.</p>
          ) : (
            <ul>
              {matches.map((match, index) => (
                <li key={`${match.path}-${index}`} className="border-b last:border-b-0">
                  <button type="button" className="hover:bg-muted/50 w-full px-3 py-2 text-start" onClick={() => openFile(match.path)}>
                    <span className="text-sm font-medium">{match.path}</span>
                    <span className="text-muted-foreground line-clamp-3 text-xs whitespace-pre-wrap">{match.snippet}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : (
        <Card className="py-0">
          {files === null ? (
            <div className="space-y-2 p-3">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : sorted.length === 0 ? (
            <p className="text-muted-foreground p-4 text-sm">Henüz hafıza kaydı yok.</p>
          ) : (
            <ul>
              {sorted.map((file) => (
                <li key={file.path} className="border-b last:border-b-0">
                  <button
                    type="button"
                    className={cn('hover:bg-muted/50 flex w-full items-center gap-2 px-3 py-2 text-start', open?.path === file.path && 'bg-muted')}
                    onClick={() => openFile(file.path)}
                  >
                    <FileTextIcon className="text-muted-foreground size-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate text-sm">{file.path}</span>
                    <span className="text-muted-foreground shrink-0 text-xs" title={formatDateTime(file.updated_at)}>
                      {formatNumber(file.chars)} kr · {file.updated_at ? formatRelative(file.updated_at) : '—'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  )

  const editor = open && (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon" className="md:hidden" aria-label="Listeye dön" onClick={() => setOpen(null)}>
          <ArrowLeftIcon />
        </Button>
        <h2 className="min-w-0 flex-1 truncate font-medium">{open.path}</h2>
        {dirty && <Badge variant="secondary">Kaydedilmedi</Badge>}
      </div>
      {conflict && (
        <div className="rounded-lg border border-amber-500/50 bg-amber-500/5 p-3 text-sm">
          Bu dosya sen açtıktan sonra değişti (JARVIS ya da başka bir cihaz yazmış olabilir). Üzerine yazılmadı.
          <Button size="sm" variant="outline" className="ms-2" onClick={() => openFile(open.path)}>
            Güncel hâlini aç
          </Button>
        </div>
      )}
      {open.truncated && <p className="text-destructive text-sm">Dosya çok büyük; yalnız başı gösteriliyor, düzenleme kapalı.</p>}
      <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1">
        <TabsList>
          <TabsTrigger value="view">Görünüm</TabsTrigger>
          <TabsTrigger value="edit" disabled={open.truncated}>
            Düzenle
          </TabsTrigger>
        </TabsList>
        <TabsContent value="view" className="min-h-0">
          <Card className="py-4">
            <CardContent className="px-4">
              {draft.trim() ? <Markdown>{draft}</Markdown> : <p className="text-muted-foreground text-sm">Boş dosya.</p>}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="edit">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-[50dvh] font-mono text-sm"
            spellCheck={false}
          />
        </TabsContent>
      </Tabs>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void save()} disabled={!dirty || saving || open.truncated}>
          {open.version ? 'Düzeltmeyi kaydet' : 'Dosyayı oluştur'}
        </Button>
        {dirty && (
          <Button variant="ghost" onClick={() => setDraft(open.content)}>
            Değişiklikleri geri al
          </Button>
        )}
        {open.version && (
          <Button variant="outline" className="text-destructive ms-auto" onClick={() => setForgetting(true)}>
            <Trash2Icon /> Unut
          </Button>
        )}
      </div>
    </div>
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Hafıza">
        <Button variant="ghost" size="sm" onClick={() => setCreating(true)}>
          <FilePlusIcon /> <span className="hidden sm:inline">Yeni dosya</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => download(user, '/v1/memory-export', 'jarvis-hafiza.zip').catch((e: unknown) => toast.error(`İndirilemedi: ${errorMessage(e)}`))}
        >
          <DownloadIcon /> <span className="hidden sm:inline">Dışa aktar</span>
        </Button>
      </PageHeader>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-6xl gap-4 p-3 sm:p-6 md:grid-cols-[minmax(16rem,22rem)_1fr]">
          <div className={cn(open && 'hidden md:block')}>{list}</div>
          <div className={cn(!open && 'hidden md:block')}>
            {editor ?? (
              <div className="text-muted-foreground flex h-full min-h-40 items-center justify-center rounded-xl border border-dashed p-6 text-center text-sm">
                Görmek, düzeltmek veya unutmak için bir dosya seç.
              </div>
            )}
          </div>
        </div>
      </div>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Yeni hafıza dosyası</DialogTitle>
            <DialogDescription>Harf, rakam, nokta, tire ve alt çizgi kullan; klasör için “/”. Örnek biçim: notlar/konu.md</DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              create()
            }}
          >
            <Input value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder="dosya-adi.md" autoFocus />
            {newPath && !PATH_RE.test(newPath.trim()) && <p className="text-destructive mt-2 text-xs">Geçersiz dosya yolu.</p>}
            <DialogFooter className="mt-4">
              <Button type="submit" disabled={!PATH_RE.test(newPath.trim())}>
                Oluştur
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={forgetting} onOpenChange={setForgetting}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{open?.path} unutulsun mu?</AlertDialogTitle>
            <AlertDialogDescription>
              Dosya bulut hafızasından kalıcı olarak silinir ve sonraki turlarda kullanılmaz. Geçmiş konuşmalarda
              geçen bilgiler o konuşmalar silinene kadar durur.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Vazgeç</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void forget()}>
              Unut
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
