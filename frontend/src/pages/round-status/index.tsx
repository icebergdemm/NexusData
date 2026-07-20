import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { PlayCircle, MapPin, Ship, Target, Users2, HeartPulse, Truck } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface ServerRoundStatus {
  active: boolean
  location: string | null
  submarine_name: string | null
  mission: string | null
  started_at: string | null
  player_count: number
  submarine_health: number | null
  shuttle_arrival_at: string | null
}

type RoundStatusResponse = Record<"ASPER1" | "ASPER2", ServerRoundStatus>

function formatShuttleEta(shuttleArrivalAt: string | null, now: number): string {
  if (!shuttleArrivalAt) return "-"
  const arrival = new Date(shuttleArrivalAt).getTime()
  const remainingSec = Math.round((arrival - now) / 1000)
  if (remainingSec <= 0) return "должен быть на месте"
  const minutes = Math.floor(remainingSec / 60)
  const seconds = remainingSec % 60
  return minutes > 0 ? `через ${minutes} мин ${String(seconds).padStart(2, "0")} сек` : `через ${seconds} сек`
}

function healthColor(pct: number | null): string {
  if (pct == null) return "text-muted-foreground"
  if (pct < 34) return "text-destructive"
  if (pct < 67) return "text-warning"
  return "text-success"
}

function ServerStatusCard({ serverName, status, now }: { serverName: string; status: ServerRoundStatus; now: number }) {
  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 border-b bg-muted/30 py-3">
        <CardTitle className="text-base font-semibold">{serverName}</CardTitle>
        <Badge variant={status.active ? "default" : "secondary"} className={status.active ? "bg-success text-white" : ""}>
          {status.active ? "🟢 Идёт раунд" : "⚪ Раунд не идёт"}
        </Badge>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-4 pt-4 text-sm">
        <InfoTile icon={MapPin} label="Локация" value={status.location ?? "-"} />
        <InfoTile icon={Ship} label="Корабль" value={status.submarine_name ?? "-"} />
        <InfoTile icon={Target} label="Миссия" value={status.mission ?? "-"} span />
        <InfoTile icon={Users2} label="Игроков онлайн" value={String(status.player_count)} />
        <InfoTile
          icon={HeartPulse}
          label="Корпус"
          value={status.submarine_health != null ? `${status.submarine_health}%` : "-"}
          valueClassName={healthColor(status.submarine_health)}
        />
        <InfoTile icon={Truck} label="Челнок снабжения" value={formatShuttleEta(status.shuttle_arrival_at, now)} span />
      </CardContent>
    </Card>
  )
}

function InfoTile({ icon: Icon, label, value, span, valueClassName }: { icon: typeof MapPin; label: string; value: string; span?: boolean; valueClassName?: string }) {
  return (
    <div className={`flex items-start gap-2 ${span ? "col-span-2" : ""}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      <div>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={`font-medium ${valueClassName ?? ""}`}>{value}</div>
      </div>
    </div>
  )
}

export function RoundStatusPage() {
  // Local ticking clock so the shuttle countdown updates every second between
  // the 15s data refetch, instead of only updating when fresh data arrives.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const { data, isLoading } = useQuery({
    queryKey: ["round-status"],
    queryFn: () => api.get<RoundStatusResponse>("/api/v1/admin/round-status"),
    refetchInterval: 15000,
  })

  return (
    <div>
      <PageHeader title="Статус раунда" description="Живой статус обоих игровых серверов" icon={PlayCircle} />
      {isLoading || !data ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <ServerStatusCard serverName="ASPER1" status={data.ASPER1} now={now} />
          <ServerStatusCard serverName="ASPER2" status={data.ASPER2} now={now} />
        </div>
      )}
    </div>
  )
}
