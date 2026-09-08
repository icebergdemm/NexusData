import { useEffect, useMemo, useState } from "react"
import { Package } from "lucide-react"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useItemCatalog, itemDisplayName } from "@/lib/items"
import { formatTimecode } from "./replay-timeline-utils"

interface BridgeEntity {
  id: number
  name: string
  species: string
  teamId: number
  job?: string | null
}

interface BridgeItem {
  id: number
  identifier: string
  condition: number
  ownerId: number
  ownerKind: number
  nested?: boolean
}

interface ReplayBridge {
  getTimeMs?: () => number
  getEntityInventory?: (entityId: number, timeMs?: number) => BridgeItem[]
  getInventoryEntities?: () => BridgeEntity[]
  getTimelineData?: () => { entities?: BridgeEntity[] }
}

function getReplayBridge(): ReplayBridge | undefined {
  return (window as unknown as { __BaroReplayUI?: ReplayBridge }).__BaroReplayUI
}

/** Инвентарь сущности в демозаписи — тот же смысл, что попап персонажа в
 * «Обзоре мира» (CharacterInfoCard): прямые предметы + один уровень
 * вложенности (подсумок в руках). Данные отдаёт replay_player.js через мост
 * (getEntityInventory/getInventoryEntities), иконки и имена — общий каталог
 * (lib/items.ts), как в обзоре мира. */
export function ReplayEntityInventory({ replayId }: { replayId: string }) {
  const { data: catalog } = useItemCatalog()
  const [entities, setEntities] = useState<BridgeEntity[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [items, setItems] = useState<BridgeItem[]>([])
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setEntities([])
    setSelectedId(null)
    setTimeMs(0)
    setItems([])
    setReady(false)

    const pullEntities = (fallback: BridgeEntity[] = []) => {
      const bridge = getReplayBridge()
      const list = bridge?.getInventoryEntities?.() ?? fallback
      if (list.length) {
        setEntities(list)
        setReady(true)
        setSelectedId((current) => (current != null && list.some((e) => e.id === current) ? current : list[0].id))
      }
    }

    const onReady = (raw: Event) => {
      const detail = (raw as CustomEvent<{ entities?: BridgeEntity[] }>).detail
      pullEntities(detail?.entities ?? [])
    }
    const onTime = (raw: Event) => {
      const detail = (raw as CustomEvent<{ timeMs: number }>).detail
      if (detail) setTimeMs(detail.timeMs)
    }
    const onPlayerPicked = (raw: Event) => {
      const detail = (raw as CustomEvent<{ name: string | null }>).detail
      const name = detail?.name
      if (!name) return
      setEntities((list) => {
        const found = list.find((e) => e.name === name)
        if (found) setSelectedId(found.id)
        return list
      })
    }
    window.addEventListener("baroreplay:ready", onReady)
    window.addEventListener("baroreplay:time", onTime)
    window.addEventListener("baroreplay:player-picked", onPlayerPicked)
    // Классический UI не шлёт baroreplay:ready (только V2) — забираем список
    // напрямую с моста, когда плеер уже распарсил демо.
    const timer = window.setInterval(() => pullEntities(), 1000)
    pullEntities()
    return () => {
      window.removeEventListener("baroreplay:ready", onReady)
      window.removeEventListener("baroreplay:time", onTime)
      window.removeEventListener("baroreplay:player-picked", onPlayerPicked)
      window.clearInterval(timer)
    }
  }, [replayId])

  useEffect(() => {
    const bridge = getReplayBridge()
    if (selectedId == null || !bridge?.getEntityInventory) {
      setItems([])
      return
    }
    try {
      setItems(bridge.getEntityInventory(selectedId, timeMs) ?? [])
    } catch {
      setItems([])
    }
  }, [selectedId, timeMs])

  const selected = useMemo(
    () => entities.find((e) => e.id === selectedId) ?? null,
    [entities, selectedId],
  )

  return (
    <div className="rpv-panel" aria-label="Инвентарь сущности в демозаписи">
      <h3 className="flex items-center gap-1.5">
        <Package className="size-3.5" aria-hidden="true" /> Инвентарь сущности
        {selected && (
          <Badge variant="outline" className="ml-auto text-[10px]">
            {formatTimecode(timeMs).replace(/^00:/, "").replace(/\.000$/, "")}
          </Badge>
        )}
      </h3>
      {!ready ? (
        <div className="text-xs text-muted-foreground">Ожидаю разбор демозаписи…</div>
      ) : !entities.length ? (
        <div className="text-xs text-muted-foreground">В этой демозаписи инвентарей нет</div>
      ) : (
        <div className="space-y-2">
          <Select value={selectedId != null ? String(selectedId) : undefined} onValueChange={(v) => setSelectedId(Number(v))}>
            <SelectTrigger aria-label="Сущность для просмотра инвентаря">
              <SelectValue placeholder="Выберите сущность…" />
            </SelectTrigger>
            <SelectContent className="max-h-72">
              {entities.map((e) => (
                <SelectItem key={e.id} value={String(e.id)}>
                  {e.name} · #{e.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selected && (
            <div className="text-[11px] text-muted-foreground">
              {selected.species || "сущность"} · ID {selected.id}
              {selected.job ? ` · ${selected.job}` : ""}
            </div>
          )}
          {items.length ? (
            <>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Предметы ({items.length})
              </div>
              <div className="flex max-h-44 flex-wrap gap-1.5 overflow-y-auto">
                {items.map((it) => {
                  const name = it.identifier ? itemDisplayName(catalog, it.identifier) : "?"
                  const title = [
                    name,
                    `ID: ${it.id}`,
                    `Идентификатор: ${it.identifier ?? "?"}`,
                    `Прочность: ${Math.round((it.condition * 100) / 255)}%`,
                    it.nested ? "Вложен в контейнер" : null,
                  ]
                    .filter(Boolean)
                    .join("\n")
                  return (
                    <span key={it.id} className="relative inline-flex">
                      <ItemIcon identifier={it.identifier ?? "?"} size={30} className="border" title={title} />
                      {it.nested && (
                        <span
                          className="absolute -bottom-1 -right-1 rounded bg-secondary px-1 text-[8px] font-bold leading-3"
                          title="Предмет внутри контейнера (рюкзак, подсумок)"
                        >
                          вн
                        </span>
                      )}
                    </span>
                  )
                })}
              </div>
            </>
          ) : (
            <div className="text-xs text-muted-foreground">
              {selected ? "У сущности сейчас ничего нет" : "Выберите сущность"}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
