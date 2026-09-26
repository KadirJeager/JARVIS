import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from '@/App'
import './index.css'
import { initServices } from '@/lib/firebase'
import { SessionProvider } from '@/lib/session'

const root = createRoot(document.getElementById('root')!)

initServices()
  .then((services) =>
    root.render(
      <StrictMode>
        <SessionProvider services={services}>
          <App />
        </SessionProvider>
      </StrictMode>,
    ),
  )
  .catch((error: unknown) => {
    root.render(
      <main style={{ padding: '2rem', fontFamily: 'system-ui' }}>
        <h1>JARVIS açılamadı</h1>
        <p>Kurulum yapılandırması okunamadı: {String(error)}</p>
        <button type="button" onClick={() => location.reload()}>
          Tekrar dene
        </button>
      </main>,
    )
  })
