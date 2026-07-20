import { Loader2 } from "lucide-react"

/**
 * Full-screen blocking overlay for operations that genuinely take a while
 * (heavy analytics aggregation, log search over large ranges, snapshot
 * generation, etc.) — the API itself stays responsive to everyone else
 * (see routes/analytics.py's run_in_threadpool + the multi-worker mode in
 * api_server.py), but a slow individual request still needs to communicate
 * "this is expected, don't panic" to the person waiting on it.
 */
export function HeavyOperationOverlay({ show, message }: { show: boolean; message?: string }) {
  if (!show) return null
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-background/70 backdrop-blur-sm">
      <div className="flex max-w-xs flex-col items-center gap-3 rounded-lg border bg-card p-6 text-center shadow-lg">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm font-medium">{message ?? "Ого! Выполняю сложные вычисления, подождите!"}</p>
      </div>
    </div>
  )
}
