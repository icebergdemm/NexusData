/** Постановка куклы в очередь на спавн — единственное место, где кукла
 *  привязывается к игроку.
 *
 *  Игроки берутся из живого списка (онлайн сначала, затем недавние), поэтому
 *  SteamID не нужно ни знать, ни вводить: админ отмечает нужных галочкой.
 *  Ручной ввод оставлен на случай, когда игрока ещё нет в базе — например,
 *  куклу готовят к заходу человека, который на сервере впервые.
 */
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, Circle, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import type { BindCandidate, Doll } from "./types"

export function QueueDialog({ doll, open, onOpenChange }: {
  doll: Doll
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const debounced = useDebouncedValue(search, 300)
  const [selected, setSelected] = useState<BindCandidate[]>(
    doll.bind_steamids.map((s) => ({ steamid: s, name: s, online: false, server: null, avatar_url: null, last_login: null })),
  )
  const [manual, setManual] = useState("")
  const [spawnMode, setSpawnMode] = useState(doll.spawn_mode || "auto")

  const { data, isLoading } = useQuery({
    queryKey: ["doll-bind-candidates", debounced, doll.server],
    queryFn: () => {
      const p = new URLSearchParams()
      if (debounced) p.set("search", debounced)
      if (doll.server) p.set("server", doll.server)
      return api.get<{ players: BindCandidate[] }>(`/api/v1/admin/dolls/players?${p}`)
    },
    enabled: open,
    // Онлайн меняется на глазах — держим список свежим, пока диалог открыт.
    refetchInterval: 20000,
  })

  const toggle = (player: BindCandidate) => {
    setSelected((prev) => prev.some((p) => p.steamid === player.steamid)
      ? prev.filter((p) => p.steamid !== player.steamid)
      : [...prev, player])
  }

  const addManual = () => {
    const ids = manual.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
    if (!ids.length) return
    setSelected((prev) => [
      ...prev,
      ...ids
        .filter((id) => !prev.some((p) => p.steamid === id))
        .map((id) => ({ steamid: id, name: id, online: false, server: null, avatar_url: null, last_login: null })),
    ])
    setManual("")
  }

  const queue = useMutation({
    mutationFn: () => api.post<{ warnings: string[] }>(`/api/v1/admin/dolls/${doll.public_id}/queue`, {
      bind_steamids: selected.map((p) => p.steamid),
      spawn_mode: spawnMode,
    }),
    onSuccess: (res) => {
      toast.success("Кукла в очереди — игровой сервер подхватит её в течение ~10 секунд")
      ;(res?.warnings ?? []).forEach((w) => toast.warning(w))
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const players = data?.players ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Поставить в очередь — {doll.label || doll.public_id}</DialogTitle>
          <DialogDescription>
            Выберите игроков, которым достанется эта кукла. Без привязки она заспавнится ботом.
          </DialogDescription>
        </DialogHeader>

        <div>
          <Label className="mb-1 block text-xs">Режим спавна</Label>
          <Select value={spawnMode} onValueChange={setSpawnMode}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Автоматически — игроку, если есть привязка</SelectItem>
              <SelectItem value="player">Только игроку из списка</SelectItem>
              <SelectItem value="bot">Всегда ботом</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {selected.length > 0 && (
          <div>
            <Label className="mb-1 block text-xs">Привязано ({selected.length})</Label>
            <div className="flex flex-wrap gap-1">
              {selected.map((p) => (
                <Badge key={p.steamid} variant="secondary" className="cursor-pointer gap-1"
                  onClick={() => toggle(p)} title="Убрать">
                  {p.name}
                  <span className="text-muted-foreground">×</span>
                </Badge>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="mb-1 flex items-center gap-2 rounded-md border px-2 py-1.5">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по имени или SteamID"
              className="w-full bg-transparent text-sm outline-none"
            />
          </div>
          <div className="max-h-72 space-y-0.5 overflow-y-auto rounded-md border p-1">
            {isLoading && Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9" />)}
            {!isLoading && players.map((p) => {
              const active = selected.some((s) => s.steamid === p.steamid)
              return (
                <button
                  key={p.steamid}
                  type="button"
                  onClick={() => toggle(p)}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${active ? "bg-primary/10" : "hover:bg-accent"}`}
                >
                  <span className="flex size-4 shrink-0 items-center justify-center">
                    {active ? <Check className="size-4 text-primary" /> : <Circle className="size-3 text-muted-foreground/40" />}
                  </span>
                  {p.avatar_url
                    ? <img src={p.avatar_url} alt="" className="size-6 shrink-0 rounded-full" />
                    : <span className="size-6 shrink-0 rounded-full bg-muted" />}
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  {p.online && <Badge variant="default" className="shrink-0 text-[10px]">{p.server || "в игре"}</Badge>}
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{p.steamid}</span>
                </button>
              )
            })}
            {!isLoading && !players.length && (
              <p className="py-6 text-center text-sm text-muted-foreground">Игроки не найдены</p>
            )}
          </div>
        </div>

        <div>
          <Label className="mb-1 block text-xs">SteamID64 вручную (если игрока ещё нет в базе)</Label>
          <div className="flex gap-2">
            <Input
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addManual() } }}
              placeholder="76561198000000000"
              className="font-mono text-xs"
            />
            <Button variant="outline" onClick={addManual}>Добавить</Button>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button onClick={() => queue.mutate()} disabled={queue.isPending}>
            Поставить в очередь
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
