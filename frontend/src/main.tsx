import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from 'next-themes'
import './index.css'
import App from './App.tsx'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { MaintenanceBanner } from '@/components/layout/MaintenanceBanner'
import { AppErrorBoundary } from '@/components/layout/AppErrorBoundary'
import { V2SkinBackdrop } from '@/components/layout/V2SkinBackdrop'
import { IS_V2, UI_BASENAME, installUiVersionAttribute } from '@/lib/ui-version'
import { installV2SkinAttribute } from '@/lib/v2-skin'
import { installMissionTuning } from '@/lib/v2-mission-tuning'
import { installEdgeTuning } from '@/lib/v2-edge-tuning'
import { installV2Spotlight } from '@/lib/v2-spotlight'
import { installDesignPreviewSession } from '@/pages/design-preview/session'

/*
  Development only. Vite serves the app from "/", but the router only knows
  the two shells, /admin and /v2: any other address — including the
  http://localhost:5173/ that Vite prints — matched neither basename and left
  a blank black page. Send those addresses into V2 with the same path.
*/
const devRedirect = import.meta.env.DEV && !/^\/(?:admin|v2)(?:\/|$)/.test(window.location.pathname)
if (devRedirect) {
  const { pathname, search, hash } = window.location
  window.location.replace(`/v2${pathname === "/" ? "/" : pathname}${search}${hash}`)
}

installUiVersionAttribute()
// Before the first render: without SSR, a design applied from an effect would
// repaint the whole screen once in the wrong one.
installV2SkinAttribute()
installMissionTuning()
installEdgeTuning()
if (IS_V2) installV2Spotlight()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

// Development only: opens /v2/design-preview without a backend. The whole
// branch is dropped from production builds by Vite's constant folding.
if (import.meta.env.DEV) installDesignPreviewSession(queryClient)

// While a redirect is in flight there is nothing worth rendering.
if (!devRedirect) createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      forcedTheme={IS_V2 ? undefined : "dark"}
      enableSystem={false}
      storageKey="nexusdata-v2-theme"
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <V2SkinBackdrop />
        <BrowserRouter basename={UI_BASENAME}>
          <TooltipProvider>
            <AppErrorBoundary>
              <App />
            </AppErrorBoundary>
            <Toaster />
            <MaintenanceBanner />
          </TooltipProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
