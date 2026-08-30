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
import { IS_V2, UI_BASENAME, installUiVersionAttribute } from '@/lib/ui-version'

installUiVersionAttribute()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
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
