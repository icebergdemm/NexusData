import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Server, Play, Square, RotateCw, Zap } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"
import { departmentsApi, type DepartmentDetail } from "./api"

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

export function TestServerTab({ department, canEdit }: { department: DepartmentDetail; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const canView = department.my_access.test_server?.view

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["department-test-server", department.id],
    queryFn: () => departmentsApi.testServer.status(department.id),
    enabled: canView,
    refetchInterval: 10000,
    retry: false,
  })

  const powerMutation = useMutation({
    mutationFn: (action: "start" | "stop" | "restart" | "kill") => departmentsApi.testServer.power(department.id, action),
    onSuccess: () => {
      toast.success("Команда отправлена")
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["department-test-server", department.id] }), 2000)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!canView) {
    return <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Недостаточно прав для управления тестовым сервером.</CardContent></Card>
  }

  if (!department.pterodactyl_server_uuid) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground">
          <Server className="size-6 opacity-40" />
          Тестовый сервер не привязан к отделу — укажите UUID сервера Pterodactyl в настройках отдела.
        </CardContent>
      </Card>
    )
  }

  const state = data?.state ?? null
  const running = state === "running"

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold"><Server className="size-4" /> Статус тестового сервера</div>
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

          {canEdit && (
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
          )}
        </CardContent>
      </Card>
    </div>
  )
}
