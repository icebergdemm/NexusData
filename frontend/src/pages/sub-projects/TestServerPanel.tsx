import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Server, Play, Square, RotateCw, Zap } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError } from "@/lib/api"
import type { SubProjectsTestServerStatus } from "./types"

const STATE_LABELS: Record<string, string> = {
  running: "Запущен",
  starting: "Запускается",
  stopping: "Останавливается",
  offline: "Выключен",
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "-"
  return `${(bytes / 1024 / 1024).toFixed(0)} МБ`
}

/** Power control for "Сервер 2" / ASPER2 — the physical server Sub-projects
 * and Тестовый полигон deploy .sub files to. Site-wide (sub_projects:
 * manage_test_server), unrelated to any one department's own test_server
 * module/Pterodactyl link. */
export function SubProjectsTestServerPanel() {
  const queryClient = useQueryClient()

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["sub-projects-test-server"],
    queryFn: () => api.get<SubProjectsTestServerStatus>("/api/v1/admin/sub-projects/test-server/status"),
    refetchInterval: 10000,
    retry: false,
  })

  const powerMutation = useMutation({
    mutationFn: (action: "start" | "stop" | "restart" | "kill") => api.post("/api/v1/admin/sub-projects/test-server/power", { action }),
    onSuccess: () => {
      toast.success("Команда отправлена")
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["sub-projects-test-server"] }), 2000)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const state = data?.state ?? null
  const running = state === "running"

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold"><Server className="size-4" /> Тестовый сервер (Сервер 2 / ASPER2)</div>
          {isLoading ? <Skeleton className="h-6 w-24" /> : (
            <Badge variant={running ? "default" : "secondary"} className={running ? "bg-success text-white" : ""}>
              {state ? (STATE_LABELS[state] ?? state) : "Неизвестно"}
            </Badge>
          )}
        </div>

        {isError && (
          <p className="text-sm text-destructive">
            {error instanceof ApiError ? error.message : "Не удалось получить статус сервера"}
          </p>
        )}

        {data && (
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            <div>
              <div className="text-xs text-muted-foreground">CPU</div>
              <div>{data.cpu_percent != null ? `${data.cpu_percent.toFixed(0)}%` : "-"}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Память</div>
              <div>{formatBytes(data.memory_bytes)} / {formatBytes(data.memory_limit_bytes)}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Диск</div>
              <div>{formatBytes(data.disk_bytes)}</div>
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2 border-t pt-3">
          <Button size="sm" disabled={powerMutation.isPending || running} onClick={() => powerMutation.mutate("start")} className="gap-1.5">
            <Play className="size-4" /> Включить
          </Button>
          <Button size="sm" variant="outline" disabled={powerMutation.isPending || !running} onClick={() => powerMutation.mutate("restart")} className="gap-1.5">
            <RotateCw className="size-4" /> Перезапустить
          </Button>
          <Button size="sm" variant="outline" disabled={powerMutation.isPending || !running} onClick={() => powerMutation.mutate("stop")} className="gap-1.5">
            <Square className="size-4" /> Выключить
          </Button>
          <Button size="sm" variant="destructive" disabled={powerMutation.isPending} onClick={() => powerMutation.mutate("kill")} className="gap-1.5">
            <Zap className="size-4" /> Убить процесс
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
