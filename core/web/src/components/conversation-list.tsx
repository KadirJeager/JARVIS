// Conversations in the sidebar: search by title, pinned first, then grouped
// by last activity; rename, pin, export and delete from each row's menu.
import { DownloadIcon, MoreHorizontalIcon, PencilIcon, PinIcon, PinOffIcon, SearchIcon, Trash2Icon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

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
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  useSidebar,
} from '@/components/ui/sidebar'
import { api, errorMessage } from '@/lib/api'
import { useConversations } from '@/lib/data'
import { exportConversation } from '@/lib/export'
import { DATE_GROUP_LABELS, dateGroup, formatDateTime, normalizeForSearch, type DateGroup } from '@/lib/format'
import { useSignedIn } from '@/lib/session'
import type { Conversation } from '@/lib/types'

const ORDER: DateGroup[] = ['today', 'yesterday', 'week', 'month', 'older']

function ConversationRow({ conversation, onRename, onDelete }: {
  conversation: Conversation
  onRename: (conversation: Conversation) => void
  onDelete: (conversation: Conversation) => void
}) {
  const { db, user } = useSignedIn()
  const { conversationId } = useParams()
  const { isMobile, setOpenMobile } = useSidebar()
  const title = conversation.title || 'Adsız konuşma'

  const togglePin = async () => {
    try {
      await api(user, 'PATCH', `/v1/conversations/${conversation.id}`, { pinned: !conversation.pinned })
    } catch (error) {
      toast.error(`Değiştirilemedi: ${errorMessage(error)}`)
    }
  }

  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild isActive={conversation.id === conversationId} tooltip={title}>
        <NavLink
          to={`/c/${conversation.id}`}
          onClick={() => isMobile && setOpenMobile(false)}
          title={`${title}\n${formatDateTime(conversation.updated_at)}`}
        >
          <span className="truncate">{title}</span>
        </NavLink>
      </SidebarMenuButton>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuAction showOnHover aria-label="Konuşma işlemleri">
            <MoreHorizontalIcon />
          </SidebarMenuAction>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start">
          <DropdownMenuItem onSelect={() => onRename(conversation)}>
            <PencilIcon /> Yeniden adlandır
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={togglePin}>
            {conversation.pinned ? <PinOffIcon /> : <PinIcon />}
            {conversation.pinned ? 'Sabitlemeyi kaldır' : 'Sabitle'}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() =>
              exportConversation(db, user.uid, conversation).catch((error: unknown) =>
                toast.error(`Dışa aktarılamadı: ${errorMessage(error)}`),
              )
            }
          >
            <DownloadIcon /> Markdown olarak indir
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => onDelete(conversation)}>
            <Trash2Icon /> Sil
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  )
}

export function ConversationList() {
  const { user } = useSignedIn()
  const navigate = useNavigate()
  const { conversationId } = useParams()
  const { data, loading, error } = useConversations()
  const [search, setSearch] = useState('')
  const [renaming, setRenaming] = useState<Conversation | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [deleting, setDeleting] = useState<Conversation | null>(null)

  const groups = useMemo(() => {
    const needle = normalizeForSearch(search.trim())
    const matches = needle
      ? data.filter((c) => normalizeForSearch(c.title ?? '').includes(needle))
      : data
    const pinned = matches.filter((c) => c.pinned)
    const byDate = new Map<DateGroup, Conversation[]>()
    for (const conversation of matches.filter((c) => !c.pinned)) {
      const key = dateGroup(conversation.updated_at)
      byDate.set(key, [...(byDate.get(key) ?? []), conversation])
    }
    return { pinned, byDate, count: matches.length }
  }, [data, search])

  const rename = async () => {
    if (!renaming || !newTitle.trim()) return
    try {
      await api(user, 'PATCH', `/v1/conversations/${renaming.id}`, { title: newTitle.trim() })
      setRenaming(null)
    } catch (error) {
      toast.error(`Adlandırılamadı: ${errorMessage(error)}`)
    }
  }

  const remove = async () => {
    if (!deleting) return
    const target = deleting
    setDeleting(null)
    try {
      await api(user, 'DELETE', `/v1/conversations/${target.id}`)
      toast.success('Konuşma silindi.')
      if (target.id === conversationId) navigate('/')
    } catch (error) {
      toast.error(`Silinemedi: ${errorMessage(error)}`)
    }
  }

  const rows = (items: Conversation[]) =>
    items.map((conversation) => (
      <ConversationRow
        key={conversation.id}
        conversation={conversation}
        onRename={(c) => {
          setNewTitle(c.title ?? '')
          setRenaming(c)
        }}
        onDelete={setDeleting}
      />
    ))

  return (
    <>
      <SidebarGroup className="py-0">
        <div className="relative">
          <SearchIcon className="text-muted-foreground pointer-events-none absolute start-2 top-1/2 size-4 -translate-y-1/2" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Konuşmalarda ara"
            className="h-8 ps-8"
            aria-label="Konuşmalarda ara"
          />
        </div>
      </SidebarGroup>
      {loading && (
        <SidebarGroup>
          <SidebarMenu>
            {Array.from({ length: 5 }, (_, i) => (
              <SidebarMenuItem key={i}>
                <SidebarMenuSkeleton />
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      )}
      {error && <p className="text-destructive px-4 text-sm">Konuşmalar okunamadı: {error.code}</p>}
      {!loading && groups.count === 0 && (
        <p className="text-muted-foreground px-4 py-2 text-sm">{search ? 'Eşleşen konuşma yok.' : 'Henüz konuşma yok.'}</p>
      )}
      {groups.pinned.length > 0 && (
        <SidebarGroup>
          <SidebarGroupLabel>Sabitlenenler</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{rows(groups.pinned)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      )}
      {ORDER.filter((key) => groups.byDate.has(key)).map((key) => (
        <SidebarGroup key={key}>
          <SidebarGroupLabel>{DATE_GROUP_LABELS[key]}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{rows(groups.byDate.get(key) ?? [])}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}

      <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Konuşmayı yeniden adlandır</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void rename()
            }}
          >
            <Input value={newTitle} onChange={(event) => setNewTitle(event.target.value)} maxLength={160} autoFocus />
            <DialogFooter className="mt-4">
              <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
                Vazgeç
              </Button>
              <Button type="submit" disabled={!newTitle.trim()}>
                Kaydet
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Konuşma silinsin mi?</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleting?.title || 'Adsız konuşma'}” konuşmasının mesajları, tur kayıtları ve modelin gördüğü geçmişi
              kalıcı olarak silinir. Hafızaya yazılmış bilgiler silinmez; onları Hafıza sayfasından yönetebilirsin.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Vazgeç</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void remove()}>
              Sil
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
