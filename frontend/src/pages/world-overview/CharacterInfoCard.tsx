import { X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName } from "@/lib/items"
import type { WorldCharacter, WorldItem } from "./api"

const TEAM_LABELS: Record<string, string> = {
  Team1: "Команда 1",
  Team2: "Команда 2",
  None: "Без команды",
  FriendlyNPC: "Дружественный NPC",
}

/** Character detail card — used both as the floating popup anchored to a
 * clicked character on WorldCanvas and (implicitly, same component) for any
 * future use elsewhere that needs the same "who/what/inventory" summary.
 * Inventory icons come from the site-wide item catalog (see lib/items.ts,
 * routes/world_state.py's /api/v1/admin/items/catalog, and
 * tools/extract_barotrauma_items.py for how it's generated). */
export function CharacterInfoCard({ character, items, onClose, className }: {
  character: WorldCharacter
  items: WorldItem[]
  onClose: () => void
  className?: string
}) {
  const { data: catalog } = useItemCatalog()

  return (
    <div className={`rounded-lg border bg-card/95 p-3 text-xs shadow-lg backdrop-blur-sm ${className ?? ""}`}>
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className={`truncate text-sm font-semibold ${character.dead ? "text-muted-foreground line-through" : ""}`}>
            {character.name}
          </div>
          <div className="truncate text-muted-foreground">
            {character.job ?? character.species} · {TEAM_LABELS[character.team] ?? character.team}
          </div>
        </div>
        <button onClick={onClose} className="shrink-0 text-muted-foreground hover:text-foreground" title="Закрыть">
          <X className="size-3.5" />
        </button>
      </div>

      <div className="mt-1.5">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-muted-foreground">HP</span>
          <span className="font-mono font-semibold">{Math.round(character.hp)}%</span>
        </div>
        <div
          role="progressbar"
          aria-label={`Здоровье ${character.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(Math.max(0, Math.min(100, character.hp)))}
          className="h-2 overflow-hidden rounded-full bg-muted/70 ring-1 ring-border/70"
        >
          <div
            className="h-full rounded-full shadow-[0_0_8px_color-mix(in_srgb,currentColor_45%,transparent)] transition-[width,background-color] duration-300"
            style={{
              width: `${Math.max(0, Math.min(100, character.hp))}%`,
              backgroundColor: `hsl(${Math.max(0, Math.min(100, character.hp)) * 1.2} 76% 47%)`,
            }}
          />
        </div>
      </div>
      {(character.dead || (character.ragdoll && !character.dead) || character.water) && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {character.dead && <Badge variant="destructive" className="text-[9px]">Мёртв</Badge>}
          {character.ragdoll && !character.dead && <Badge variant="outline" className="text-[9px]">Оглушён</Badge>}
          {character.water && <Badge variant="outline" className="text-[9px]">В воде</Badge>}
        </div>
      )}
      {character.steamid && (
        <div className="mt-1.5 truncate font-mono text-[10px] text-muted-foreground">{character.steamid}</div>
      )}

      {!!items.length && (
        <div className="mt-2.5 border-t pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Инвентарь ({items.length})
          </div>
          <div className="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto">
            {items.map((it) => {
              const name = it.item ? itemDisplayName(catalog, it.item) : "?"
              const title = [
                name,
                `ID: ${it.id}`,
                `Идентификатор: ${it.item ?? "?"}`,
                it.cond != null ? `Прочность: ${Math.round(it.cond)}%` : null,
              ].filter(Boolean).join("\n")
              return <ItemIcon key={it.id} identifier={it.item ?? "?"} size={30} className="border" title={title} />
            })}
          </div>
        </div>
      )}
    </div>
  )
}
