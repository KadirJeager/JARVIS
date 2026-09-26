import { createBrowserRouter, Outlet, RouterProvider } from 'react-router'
import { useRegisterSW } from 'virtual:pwa-register/react'
import { useEffect } from 'react'
import { toast } from 'sonner'

import { AppShell } from '@/components/app-shell'
import { PendingSendsProvider } from '@/components/chat/pending-sends'
import { Spinner } from '@/components/ui/spinner'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useSession } from '@/lib/session'
import { ActivityPage } from '@/pages/activity'
import { BrainPage } from '@/pages/brain'
import { ChatPage, ShareTargetPage } from '@/pages/chat'
import { MemoryPage } from '@/pages/memory'
import { AccessGate, SignInPage } from '@/pages/sign-in'
import { SystemPage } from '@/pages/system'

function SignedInRoot() {
  const { user, ready } = useSession()
  if (!ready) {
    return (
      <main className="flex min-h-dvh items-center justify-center">
        <Spinner />
      </main>
    )
  }
  if (!user) return <SignInPage />
  return (
    <AccessGate>
      <PendingSendsProvider>
        <Outlet />
      </PendingSendsProvider>
    </AccessGate>
  )
}

const router = createBrowserRouter([
  {
    element: <SignedInRoot />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <ChatPage /> },
          { path: 'c/:conversationId', element: <ChatPage /> },
          { path: 'activity', element: <ActivityPage /> },
          { path: 'memory', element: <MemoryPage /> },
          { path: 'brain', element: <BrainPage /> },
          { path: 'system', element: <SystemPage /> },
          { path: 'share', element: <ShareTargetPage /> },
          { path: '*', element: <ChatPage /> },
        ],
      },
    ],
  },
])

/** A new app version is waiting: offer to reload instead of switching under the owner. */
function UpdatePrompt() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW({ immediate: true })
  useEffect(() => {
    if (!needRefresh) return
    toast('JARVIS’in yeni sürümü hazır.', {
      duration: Infinity,
      action: { label: 'Yenile', onClick: () => void updateServiceWorker(true) },
    })
  }, [needRefresh, updateServiceWorker])
  return null
}

export default function App() {
  return (
    <TooltipProvider>
      <RouterProvider router={router} />
      <UpdatePrompt />
      <Toaster position="top-center" richColors closeButton />
    </TooltipProvider>
  )
}
