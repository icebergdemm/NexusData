import { useEffect, useState } from "react"
import { ServerCrash, RotateCw } from "lucide-react"

interface HealthzResponse {
  status: "ok" | "maintenance" | "degraded"
  db_healthy: boolean
  api_enabled: boolean
  web_enabled: boolean
  maintenance_message: string | null
}

const POLL_INTERVAL_MS = 15_000

/**
 * Full-page notice shown whenever the backend reports it's in maintenance
 * mode, a subsystem was toggled off (see console.py's "api/web off"), or the
 * database is unreachable — see main.py's maintenance_gate_middleware and
 * the unauthenticated /healthz probe it backs.
 *
 * Two independent triggers, so this shows up even for a user who's idle on
 * a page when maintenance starts, not just on their next failed request:
 *   1. api.ts dispatches a "nd-maintenance" window event on any 503 whose
 *      body has maintenance:true.
 *   2. This component polls /healthz itself every 15s regardless.
 *
 * Mounted once in main.tsx, outside <App/> — survives routing/auth crashes.
 */
export function MaintenanceBanner() {
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    function onMaintenanceEvent(e: Event) {
      const detail = (e as CustomEvent<{ error?: string }>).detail
      setMessage(detail?.error || "Ведутся технические работы.")
    }
    window.addEventListener("nd-maintenance", onMaintenanceEvent)
    return () => window.removeEventListener("nd-maintenance", onMaintenanceEvent)
  }, [])

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const res = await fetch("/healthz", { credentials: "include" })
        if (!res.ok) return
        const data = (await res.json()) as HealthzResponse
        if (cancelled) return
        if (data.status === "ok") {
          setMessage(null)
        } else {
          setMessage(
            data.maintenance_message
            || (!data.db_healthy ? "База данных недоступна. Мы уже работаем над восстановлением." : "Сайт временно недоступен.")
          )
        }
      } catch {
        // A network-level failure (backend process itself unreachable) is
        // exactly the "even dead processes should show something" case this
        // component can't do anything about from inside the page — the
        // in-process resilience layer (this banner) only covers the backend
        // process being alive but degraded, not it being gone entirely.
      }
    }

    poll()
    const id = window.setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  if (!message) return null

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-background/95 p-4 backdrop-blur-sm">
      <div className="flex max-w-sm flex-col items-center gap-3 rounded-lg border bg-card p-6 text-center shadow-lg">
        <ServerCrash className="size-8 text-warning" />
        <div>
          <p className="font-medium">Технические работы</p>
          <p className="mt-1 text-sm text-muted-foreground">{message}</p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="mt-1 flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent"
        >
          <RotateCw className="size-3.5" /> Обновить страницу
        </button>
        <p className="text-[11px] text-muted-foreground">Страница проверяет доступность автоматически.</p>
      </div>
    </div>
  )
}
