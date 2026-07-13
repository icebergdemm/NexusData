import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Bot } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

const KNOWN_KEYS: { key: string; label: string }[] = [
  { key: "forward_channel_id", label: "Пересылка сообщений" },
  { key: "rating_channel_id", label: "Публикация рейтинга" },
  { key: "dispatch_channel_asper1_id", label: "Диспетчерская ASPER1" },
  { key: "dispatch_channel_asper2_id", label: "Диспетчерская ASPER2" },
  { key: "round_status_channel_asper1_id", label: "Статус раунда ASPER1" },
  { key: "round_status_channel_asper2_id", label: "Статус раунда ASPER2" },
  { key: "ticket_status_channel_id", label: "Статус тикетов" },
  { key: "trap_channel_id", label: "Уведомления о ловушках" },
  { key: "punishment_channel_id", label: "Все наказания" },
  { key: "ban_channel_id", label: "Только баны" },
  { key: "sub_projects_channel_id", label: "Публикация Sub-карт" },
]

interface AntiRaidEvent { event_type: string; discord_id: string | null; username: string | null; occurred_at: string | null; resolved: boolean }
interface TwinkSuspect { new_discord_id: string; new_username: string | null; new_display_name: string | null; similar_player_name: string | null }
interface DiscordBotResponse {
  members_count: number; pending_actions: number; last_sync: string | null
  config: Record<string, string>; antiraid_events: AntiRaidEvent[]; twink_suspects: TwinkSuspect[]
  can_edit: boolean
}

export function DiscordBotPage() {
  const [newKey, setNewKey] = useState({ key: "", value: "" })
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ["discord-bot"], queryFn: () => api.get<DiscordBotResponse>("/api/v1/admin/discord-bot") })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["discord-bot"] })

  const setConfig = useMutation({
    mutationFn: (body: { key: string; value: string }) => api.post("/api/v1/admin/discord-bot/config", body),
    onSuccess: () => { toast.success("Сохранено"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader title="Discord бот" description="Синхронизация участников, каналы уведомлений, анти-рейд" icon={Bot} />
      <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{data?.members_count ?? "—"}</div><div className="text-xs text-muted-foreground">Участников Discord</div></CardContent></Card>
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{data?.pending_actions ?? "—"}</div><div className="text-xs text-muted-foreground">Задач в очереди</div></CardContent></Card>
        <Card><CardContent className="pt-6 text-center"><div className="text-sm font-bold">{data?.last_sync ? new Date(data.last_sync).toLocaleString("ru-RU") : "—"}</div><div className="text-xs text-muted-foreground">Последняя синхронизация</div></CardContent></Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">Каналы</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {KNOWN_KEYS.map(({ key, label }) => (
            <ConfigRow key={key} label={label} configKey={key} value={data?.config[key] ?? ""} canEdit={!!data?.can_edit} onSave={(v) => setConfig.mutate({ key, value: v })} />
          ))}
          {data?.can_edit && (
            <div className="flex items-end gap-2 border-t pt-3">
              <div className="flex-1"><Label>Ключ</Label><Input value={newKey.key} onChange={(e) => setNewKey((f) => ({ ...f, key: e.target.value }))} /></div>
              <div className="flex-1"><Label>Значение</Label><Input value={newKey.value} onChange={(e) => setNewKey((f) => ({ ...f, value: e.target.value }))} /></div>
              <Button disabled={!newKey.key.trim()} onClick={() => { setConfig.mutate(newKey); setNewKey({ key: "", value: "" }) }}>Добавить</Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Анти-рейд лог</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Тип</TableHead><TableHead>Пользователь</TableHead><TableHead>Время</TableHead><TableHead>Статус</TableHead></TableRow></TableHeader>
            <TableBody>
              {data?.antiraid_events.map((e, i) => (
                <TableRow key={i}>
                  <TableCell>{e.event_type}</TableCell>
                  <TableCell>{e.username ?? e.discord_id ?? "—"}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{e.occurred_at ? new Date(e.occurred_at).toLocaleString("ru-RU") : "—"}</TableCell>
                  <TableCell><Badge variant={e.resolved ? "secondary" : "destructive"}>{e.resolved ? "Решено" : "Активно"}</Badge></TableCell>
                </TableRow>
              ))}
              {!data?.antiraid_events.length && <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground">Событий нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Подозрения на твинков (по Discord)</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Новый участник</TableHead><TableHead>Похож на</TableHead></TableRow></TableHeader>
            <TableBody>
              {data?.twink_suspects.map((t, i) => (
                <TableRow key={i}>
                  <TableCell>{t.new_display_name ?? t.new_username}</TableCell>
                  <TableCell>{t.similar_player_name}</TableCell>
                </TableRow>
              ))}
              {!data?.twink_suspects.length && <TableRow><TableCell colSpan={2} className="text-center text-muted-foreground">Подозрений нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      </div>
    </div>
  )
}

function ConfigRow({ label, value, canEdit, onSave }: { label: string; configKey: string; value: string; canEdit: boolean; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState(value)
  return (
    <div className="flex items-center gap-2">
      <Label className="w-56 shrink-0 text-xs text-muted-foreground">{label}</Label>
      <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Channel ID" disabled={!canEdit} />
      {canEdit && draft !== value && <Button size="sm" onClick={() => onSave(draft)}>Сохранить</Button>}
    </div>
  )
}
