import { Component, type ErrorInfo, type ReactNode } from "react"
import { AlertTriangle, RotateCw } from "lucide-react"

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Catches render-time crashes anywhere in the app (a page throwing on
 * malformed/unexpected data during a backend outage, a bug in a single
 * page, etc.) and shows a recoverable screen instead of a white page —
 * React only supports error boundaries as class components
 * (componentDidCatch has no hook equivalent).
 *
 * Deliberately wraps <App/> in main.tsx, not something inside the router —
 * a crash during routing/auth itself must still be caught.
 */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[AppErrorBoundary] Render crash:", error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 p-4 text-center">
        <AlertTriangle className="size-8 text-destructive" />
        <div>
          <p className="font-medium">Что-то пошло не так</p>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            Произошла непредвиденная ошибка на странице. Попробуйте обновить — если проблема повторится, сообщите администратору.
          </p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="mt-1 flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent"
        >
          <RotateCw className="size-3.5" /> Обновить страницу
        </button>
      </div>
    )
  }
}
