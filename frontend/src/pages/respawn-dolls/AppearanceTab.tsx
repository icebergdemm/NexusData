/** Вкладка внешности: крупный предпросмотр слева, выбор частей — справа.
 *
 *  Выбор идёт кликом по миниатюре, а не вводом числового индекса: индексы
 *  причёсок считаются внутри отфильтрованного по полу списка, и угадать их
 *  на глаз невозможно. Смена пола сбрасывает индексы — у другого пола другой
 *  набор, и старый номер указывал бы на чужую причёску.
 */
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { CharacterPreview, SpriteStack } from "./sprites"
import type { AppearanceCatalog, Identity, SpriteRef, WearableSprite } from "./types"

function ColorPalette({ value, palette, onChange }: {
  value?: string
  palette: string[]
  onChange: (color?: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {palette.map((c, i) => (
        <button
          key={`${c}-${i}`}
          type="button"
          title={c}
          onClick={() => onChange(c)}
          className={`size-5 rounded border transition ${value === c ? "ring-2 ring-primary ring-offset-1 ring-offset-background" : "hover:scale-110"}`}
          style={{ backgroundColor: c }}
        />
      ))}
      <input
        type="color"
        value={value && /^#[0-9a-fA-F]{6}$/.test(value) ? value : "#ffffff"}
        onChange={(e) => onChange(e.target.value)}
        className="size-5 cursor-pointer rounded border bg-transparent p-0"
        title="Свой цвет"
      />
      {value && (
        <button type="button" className="text-xs text-muted-foreground underline" onClick={() => onChange(undefined)}>
          сбросить
        </button>
      )}
    </div>
  )
}

/** Ряд миниатюр с вариантом «нет» — общий для причёски, бороды и аксессуара.
 *  Каждая миниатюра показана поверх выбранной головы, иначе причёска сама по
 *  себе выглядит как непонятное пятно. */
function SpriteChoices({ label, head, list, value, onChange, catalog }: {
  label: string
  head: SpriteRef | null
  list: WearableSprite[]
  value: number | undefined
  onChange: (index: number | undefined) => void
  catalog: AppearanceCatalog
}) {
  const selected = value === undefined ? -1 : value
  return (
    <div>
      <Label className="mb-1 block text-xs">{label}</Label>
      {list.length === 0 ? (
        <p className="text-xs text-muted-foreground">Для этой головы недоступно</p>
      ) : (
        <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto rounded-md border p-1.5">
          <button
            type="button"
            onClick={() => onChange(-1)}
            title="Нет"
            className={`flex size-12 items-center justify-center rounded border text-[10px] text-muted-foreground ${selected < 0 ? "border-primary bg-primary/10" : "hover:bg-accent"}`}
          >
            нет
          </button>
          {list.map((w) => (
            <button
              key={w.index}
              type="button"
              onClick={() => onChange(w.index)}
              title={w.name}
              className={`overflow-hidden rounded border ${selected === w.index ? "border-primary ring-1 ring-primary" : "hover:bg-accent"}`}
            >
              <SpriteStack layers={head ? [head, w] : [w]} catalog={catalog} size={48} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function AppearanceTab({ identity, onChange, catalog }: {
  identity: Identity
  onChange: (next: Identity) => void
  catalog?: AppearanceCatalog
}) {
  if (!catalog?.heads?.length) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Справочник внешности недоступен — соберите его скриптом tools/build_doll_catalog.py.
      </p>
    )
  }

  const tags = identity.head_tags ?? []
  const gender = tags.includes("female") ? "female" : "male"
  const headTag = tags.find((t) => /^head\d+$/.test(t))
  const heads = catalog.heads.filter((h) => h.gender === gender)
  const head = heads.find((h) => !headTag || h.tags.includes(headTag)) ?? heads[0] ?? null
  const wear = catalog.wearables[gender] ?? { hair: [], beard: [], face_attachment: [] }

  const set = (patch: Partial<Identity>) => onChange({ ...identity, ...patch })

  const setHead = (nextGender: string, nextTag: string) => {
    // Пишем оба тега сразу: движок сверяет НАБОР целиком, одного head3 мало.
    // Индексы обнуляем — у другого пола другой список причёсок и бород.
    const changedGender = nextGender !== gender
    set({
      head_tags: [nextTag, nextGender],
      ...(changedGender
        ? { hair_index: undefined, beard_index: undefined, face_attachment_index: undefined }
        : {}),
    })
  }

  return (
    <div className="grid gap-4 md:grid-cols-[auto_1fr]">
      <div className="flex flex-col items-center gap-3">
        <div className="rounded-lg border bg-gradient-to-b from-muted/60 to-muted/20 p-2">
          <CharacterPreview identity={identity} catalog={catalog} size={176} />
        </div>
        <div className="flex gap-1">
          {(["male", "female"] as const).map((g) => (
            <button
              key={g}
              type="button"
              onClick={() => setHead(g, headTag ?? "head1")}
              className={`rounded-md border px-3 py-1 text-xs ${gender === g ? "border-primary bg-primary/10" : "hover:bg-accent"}`}
            >
              {g === "male" ? "Мужской" : "Женский"}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label className="mb-1 block text-xs">Имя персонажа</Label>
            <Input
              value={identity.name ?? ""}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="Пусто — движок сгенерирует"
            />
          </div>
          <div>
            <Label className="mb-1 block text-xs">Характер</Label>
            <Select
              value={identity.personality ?? "none"}
              onValueChange={(v) => set({ personality: v === "none" ? undefined : v })}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Не задан</SelectItem>
                {catalog.personalities.map((p) => (
                  <SelectItem key={p} value={p}>{catalog.personality_labels?.[p] ?? p}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div>
          <Label className="mb-1 block text-xs">Голова</Label>
          <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto rounded-md border p-1.5">
            {heads.map((h) => {
              const tag = h.tags.find((t) => /^head\d+$/.test(t)) ?? ""
              const active = head?.tags.join() === h.tags.join()
              return (
                <button
                  key={tag}
                  type="button"
                  title={tag}
                  onClick={() => setHead(gender, tag)}
                  className={`overflow-hidden rounded border ${active ? "border-primary ring-1 ring-primary" : "hover:bg-accent"}`}
                >
                  <SpriteStack layers={[h]} catalog={catalog} size={48} />
                </button>
              )
            })}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <SpriteChoices
            label="Причёска" head={head} list={wear.hair} catalog={catalog}
            value={identity.hair_index}
            onChange={(i) => set({ hair_index: i })}
          />
          <SpriteChoices
            label="Борода" head={head} list={wear.beard} catalog={catalog}
            value={identity.beard_index}
            onChange={(i) => set({ beard_index: i })}
          />
          <SpriteChoices
            label="Аксессуар лица" head={head} list={wear.face_attachment} catalog={catalog}
            value={identity.face_attachment_index}
            onChange={(i) => set({ face_attachment_index: i })}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label className="mb-1 block text-xs">Цвет кожи</Label>
            <ColorPalette value={identity.skin_color} palette={catalog.colors.skin ?? []}
              onChange={(c) => set({ skin_color: c })} />
          </div>
          <div>
            <Label className="mb-1 block text-xs">Цвет волос</Label>
            <ColorPalette value={identity.hair_color} palette={catalog.colors.hair ?? []}
              onChange={(c) => set({ hair_color: c })} />
          </div>
          <div>
            <Label className="mb-1 block text-xs">Цвет бороды</Label>
            <ColorPalette value={identity.facial_hair_color} palette={catalog.colors.facial_hair ?? []}
              onChange={(c) => set({ facial_hair_color: c })} />
          </div>
        </div>
      </div>
    </div>
  )
}
