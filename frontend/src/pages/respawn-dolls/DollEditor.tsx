/** Конструктор куклы: вкладки внешности, профессии, инвентаря, эффектов и
 *  сырого JSON.
 *
 *  Источник истины — объект spec в состоянии, а не текст JSON: вкладки
 *  правят его напрямую, а JSON-вкладка показывает и принимает тот же объект.
 *  Так поля и JSON не могут разъехаться, а при невалидном тексте правки
 *  просто не применяются, вместо того чтобы затирать собранную куклу.
 *
 *  SteamID здесь НЕ спрашивается: кукла — заготовка, привязка к игроку
 *  появляется при постановке в очередь (см. QueueDialog).
 */
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api"
import { AppearanceTab } from "./AppearanceTab"
import { InventoryTab } from "./InventoryTab"
import { JobTab, EffectsTab } from "./ParamTabs"
import { SERVERS, type AppearanceCatalog, type Doll, type DollReference, type DollSpec } from "./types"

/** Заготовка новой куклы: ассистент со штатной внешностью. Пустой spec тоже
 *  валиден, но тогда предпросмотр показывал бы «ничего», а это выглядит
 *  как поломка, а не как «движок сгенерирует сам». */
function blankSpec(): DollSpec {
  return {
    identity: { head_tags: ["head1", "male"] },
    job: { identifier: "assistant", variant: 0 },
  }
}

export function DollEditor({ open, onOpenChange, editing, catalog, reference }: {
  open: boolean
  onOpenChange: (o: boolean) => void
  editing: Doll | null
  catalog?: AppearanceCatalog
  reference?: DollReference
}) {
  const queryClient = useQueryClient()
  const [server, setServer] = useState(editing?.server || SERVERS[0])
  const [label, setLabel] = useState(editing?.label ?? "")
  const [priority, setPriority] = useState(String(editing?.priority ?? 0))
  const [oneShot, setOneShot] = useState(editing?.one_shot ?? true)
  const [team, setTeam] = useState(String(editing?.team ?? 0))
  const [spec, setSpec] = useState<DollSpec>(editing?.spec && Object.keys(editing.spec).length ? editing.spec : blankSpec())
  const [jsonText, setJsonText] = useState("")
  const [jsonError, setJsonError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        server, label, spec,
        priority: Number(priority) || 0,
        one_shot: oneShot,
        team: Number(team) || 0,
      }
      return editing
        ? api.patch<{ warnings: string[] }>(`/api/v1/admin/dolls/${editing.public_id}`, payload)
        : api.post<{ warnings: string[] }>("/api/v1/admin/dolls", payload)
    },
    onSuccess: (res) => {
      toast.success(editing ? "Кукла обновлена" : "Кукла сохранена — поставьте её в очередь, чтобы привязать игрока")
      ;(res?.warnings ?? []).forEach((w) => toast.warning(w))
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? "Изменить куклу" : "Новая кукла"}</DialogTitle>
          <DialogDescription>
            Заготовка персонажа: внешность, профессия, навыки и снаряжение. Кому она достанется,
            выбирается позже — при постановке в очередь.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-4">
          <div>
            <Label className="mb-1 block text-xs">Сервер</Label>
            <Select value={server} onValueChange={setServer}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {SERVERS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="sm:col-span-2">
            <Label className="mb-1 block text-xs">Название (для админки)</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Капитан для ивента" />
          </div>
          <div>
            <Label className="mb-1 block text-xs">Приоритет</Label>
            <Input type="number" value={priority} onChange={(e) => setPriority(e.target.value)} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={oneShot} onCheckedChange={(v) => setOneShot(!!v)} />
            Одноразовая — снимается после первого спавна
          </label>
          <div className="flex items-center gap-2">
            <Label className="text-xs">Команда</Label>
            <Select value={team} onValueChange={setTeam}>
              <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="0">Не менять</SelectItem>
                <SelectItem value="1">Команда 1</SelectItem>
                <SelectItem value="2">Команда 2</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <Tabs
          defaultValue="appearance"
          onValueChange={(v) => {
            // JSON собираем при переходе на вкладку, чтобы не пересобирать
            // текст на каждое движение ползунка навыка.
            if (v === "json") { setJsonText(JSON.stringify(spec, null, 2)); setJsonError(null) }
          }}
        >
          <TabsList className="w-full">
            <TabsTrigger value="appearance" className="flex-1">Внешность</TabsTrigger>
            <TabsTrigger value="job" className="flex-1">Профессия</TabsTrigger>
            <TabsTrigger value="inventory" className="flex-1">Инвентарь</TabsTrigger>
            <TabsTrigger value="effects" className="flex-1">Статы и здоровье</TabsTrigger>
            <TabsTrigger value="json" className="flex-1">JSON</TabsTrigger>
          </TabsList>

          <TabsContent value="appearance" className="mt-3">
            <AppearanceTab
              identity={spec.identity ?? {}}
              catalog={catalog}
              onChange={(identity) => setSpec({ ...spec, identity })}
            />
          </TabsContent>

          <TabsContent value="job" className="mt-3">
            <JobTab spec={spec} onChange={setSpec} reference={reference} />
          </TabsContent>

          <TabsContent value="inventory" className="mt-3">
            <InventoryTab
              items={spec.inventory?.items ?? []}
              onChange={(items) => setSpec({ ...spec, inventory: { ...(spec.inventory ?? {}), items } })}
            />
          </TabsContent>

          <TabsContent value="effects" className="mt-3">
            <EffectsTab spec={spec} onChange={setSpec} reference={reference} />
          </TabsContent>

          <TabsContent value="json" className="mt-3 space-y-2">
            <p className="text-xs text-muted-foreground">
              Ровно то, что уйдёт на игровой сервер. Правки применяются к конструктору, если JSON корректен.
            </p>
            <Textarea
              rows={18}
              value={jsonText}
              spellCheck={false}
              className="font-mono text-xs"
              onChange={(e) => {
                setJsonText(e.target.value)
                try {
                  setSpec(JSON.parse(e.target.value || "{}"))
                  setJsonError(null)
                } catch (err) {
                  setJsonError((err as Error).message)
                }
              }}
            />
            {jsonError && <p className="text-xs text-destructive">JSON некорректен: {jsonError}</p>}
          </TabsContent>
        </Tabs>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending || !!jsonError}>
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
