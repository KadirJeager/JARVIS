// Layout after sign-in: a sidebar with the conversation list and the panel
// sections (a sheet on phones), and the page on the right.
import { signOut } from 'firebase/auth'
import {
  ActivityIcon,
  BrainIcon,
  DatabaseIcon,
  LogOutIcon,
  MessageSquarePlusIcon,
  ServerCogIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'

import { ConversationList } from '@/components/conversation-list'
import { Button } from '@/components/ui/button'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar'
import { useSignedIn } from '@/lib/session'

const SECTIONS = [
  { to: '/activity', label: 'Etkinlik', icon: ActivityIcon },
  { to: '/memory', label: 'Hafıza', icon: DatabaseIcon },
  { to: '/brain', label: 'Beyin', icon: BrainIcon },
  { to: '/system', label: 'Sistem', icon: ServerCogIcon },
]

function AppSidebar() {
  const { auth, user } = useSignedIn()
  const navigate = useNavigate()
  const location = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()
  const close = () => isMobile && setOpenMobile(false)

  return (
    <Sidebar>
      <SidebarHeader className="gap-3">
        <div className="flex items-center gap-2 px-1 pt-1">
          <img src="/icon-192.png" alt="" className="size-7 rounded-md" />
          <span className="text-base font-semibold tracking-tight">JARVIS</span>
        </div>
        <Button
          variant="outline"
          className="justify-start"
          onClick={() => {
            navigate('/')
            close()
          }}
        >
          <MessageSquarePlusIcon /> Yeni konuşma
        </Button>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            {SECTIONS.map(({ to, label, icon: Icon }) => (
              <SidebarMenuItem key={to}>
                <SidebarMenuButton asChild isActive={location.pathname.startsWith(to)}>
                  <NavLink to={to} onClick={close}>
                    <Icon /> {label}
                  </NavLink>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
        <SidebarSeparator />
        <ConversationList />
      </SidebarContent>
      <SidebarFooter>
        <div className="flex items-center gap-2 px-1">
          <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs" title={user.email ?? ''}>
            {user.email}
          </span>
          <Button variant="ghost" size="icon" className="size-8" aria-label="Çıkış yap" onClick={() => signOut(auth)}>
            <LogOutIcon />
          </Button>
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}

export function AppShell() {
  return (
    <SidebarProvider className="h-dvh">
      <AppSidebar />
      <SidebarInset className="min-h-0 overflow-hidden">
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  )
}

/** Top bar of a page: the sidebar toggle, the title and optional actions. */
export function PageHeader({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <header className="bg-background/95 supports-[backdrop-filter]:bg-background/80 sticky top-0 z-20 flex h-12 shrink-0 items-center gap-2 border-b px-2 pt-[env(safe-area-inset-top)] backdrop-blur sm:px-3">
      <SidebarTrigger aria-label="Menüyü aç veya kapat" />
      <h1 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h1>
      {children}
    </header>
  )
}
