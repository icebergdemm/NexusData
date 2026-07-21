import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Plus, Minus, Trash2, Search, PackageSearch } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName, type ItemCatalog } from "@/lib/items"
import { warehouseApi } from "./warehouseApi"

/** Item picker + quantity — a simple search-filtered list rather than a
 * full combobox component (none exists in this codebase yet), since the
 * catalog is a few hundred entries and client-side filtering is instant. */
function AddItemDialog({ open, onOpenChange, existingIdentifiers, catalog }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingIdentifiers: Set<string>
  catalog: ItemCatalog | undefined
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [quantity, setQuantity] = useState("1")

  const options = useMemo(() => {
    if (!catalog) return []
    const q = search.trim().toLowerCase()
    return Object.entries(catalog)
      .filter(([id, entry]) => !existingIdentifiers.has(id) && (!q || entry.name.toLowerCase().includes(q) || id.toLowerCase().includes(q)))
      .sort((a, b) => a[1].name.localeCompare(b[1].name, "ru"))
      .slice(0, 150)
  }, [catalog, search, existingIdentifiers])

  const reset = () => { setSelected(null); setSearch(""); setQuantity("1") }

  const addMutation = useMutation({
    mutationFn: () => warehouseApi.upsert(selected!, Math.max(0, Number(quantity) || 0)),
    onSuccess: () => {
      toast.success("Добавлено на склад")
      queryClient.invalidateQueries({ queryKey: ["warehouse"] })
      onOpenChange(false)
      reset()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Добавить предмет на склад</DialogTitle>
          <DialogDescription>Выберите предмет из каталога Barotrauma и укажите количество.</DialogDescription>
        </DialogHeader>

        {!selected ? (
          <>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по названию или ID…" className="pl-8" autoFocus />
            </div>
            <div className="max-h-72 overflow-y-auto rounded-md border">
              {options.map(([id, entry]) => (
                <button
                  key={id}
                  onClick={() => setSelected(id)}
                  className="flex w-full items-center gap-2 border-b px-2.5 py-1.5 text-left text-sm last:border-b-0 hover:bg-accent"
                >
                  <ItemIcon identifier={id} size={24} />
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{id}</span>
                </button>
              ))}
              {!options.length && <div className="py-6 text-center text-xs text-muted-foreground">Ничего не найдено</div>}
            </div>
          </>
        ) : (
          <div className="flex items-center gap-3 rounded-md border p-3">
            <ItemIcon identifier={selected} size={36} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{itemDisplayName(catalog, selected)}</div>
              <div className="truncate font-mono text-[10px] text-muted-foreground">{selected}</div>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>Изменить</Button>
          </div>
        )}

        {selected && (
          <div>
            <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Количество</label>
            <Input type="number" min={0} value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!selected || addMutation.isPending} onClick={() => addMutation.mutate()}>Добавить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Virtual dispatch resource warehouse — see routes/warehouse.py. Item
 * quantities the Диспетчерская can manage now; sending some of this stock
 * out with the supply shuttle is future work (see the "Отправить челнок
 * снабжения" stub action in dispatch/index.tsx) that will read from here. */
export function Warehouse() {
  const queryClient = useQueryClient()
  const { data: catalog } = useItemCatalog()
  const [addOpen, setAddOpen] = useState(false)
  const [search, setSearch] = useState("")

  const { data } = useQuery({
    queryKey: ["warehouse"],
    queryFn: () => warehouseApi.list(),
    refetchInterval: 15000,
  })

  const adjustMutation = useMutation({
    mutationFn: ({ identifier, delta }: { identifier: string; delta: number }) => warehouseApi.adjust(identifier, delta),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["warehouse"] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const removeMutation = useMutation({
    mutationFn: (identifier: string) => warehouseApi.remove(identifier),
    onSuccess: () => { toast.success("Убрано со склада"); queryClient.invalidateQueries({ queryKey: ["warehouse"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const items = data?.items ?? []
  const filtered = items.filter((it) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return itemDisplayName(catalog, it.identifier).toLowerCase().includes(q) || it.identifier.toLowerCase().includes(q)
  })
  const existingIdentifiers = useMemo(() => new Set(items.map((it) => it.identifier)), [items])

  return (
    <div className="flex h-[calc(100vh-13rem)] flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по складу…" className="pl-8" />
        </div>
        {data?.can_edit && (
          <Button onClick={() => setAddOpen(true)}><Plus className="mr-1 size-4" /> Добавить предмет</Button>
        )}
      </div>

      {!filtered.length ? (
        <Card className="flex-1"><CardContent className="flex h-full flex-col items-center justify-center gap-2 py-12 text-center text-sm text-muted-foreground">
          <PackageSearch className="size-6 opacity-40" />
          {items.length ? "Ничего не найдено" : "Склад пуст"}
        </CardContent></Card>
      ) : (
        <div className="grid auto-rows-min flex-1 grid-cols-2 content-start gap-2 overflow-y-auto pr-0.5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {filtered.map((it) => {
            const name = itemDisplayName(catalog, it.identifier)
            return (
              <Card key={it.identifier} className="overflow-hidden py-0">
                <CardContent className="flex flex-col items-center gap-2 p-3">
                  <ItemIcon identifier={it.identifier} size={44} title={`${name}\nИдентификатор: ${it.identifier}`} />
                  <div className="w-full text-center">
                    <div className="truncate text-xs font-medium" title={name}>{name}</div>
                    <div className="truncate font-mono text-[9px] text-muted-foreground">{it.identifier}</div>
                  </div>
                  {data?.can_edit ? (
                    <div className="flex items-center gap-1">
                      <Button
                        size="icon-xs" variant="outline"
                        onClick={() => adjustMutation.mutate({ identifier: it.identifier, delta: -1 })}
                        disabled={it.quantity <= 0 || adjustMutation.isPending}
                      >
                        <Minus className="size-3" />
                      </Button>
                      <span className="w-10 text-center text-sm font-semibold">{it.quantity}</span>
                      <Button
                        size="icon-xs" variant="outline"
                        onClick={() => adjustMutation.mutate({ identifier: it.identifier, delta: 1 })}
                        disabled={adjustMutation.isPending}
                      >
                        <Plus className="size-3" />
                      </Button>
                    </div>
                  ) : (
                    <div className="text-sm font-semibold">{it.quantity}</div>
                  )}
                  {data?.can_edit && (
                    <Button
                      size="icon-xs" variant="ghost" className="text-destructive"
                      onClick={() => removeMutation.mutate(it.identifier)}
                      title="Убрать со склада"
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      <AddItemDialog open={addOpen} onOpenChange={setAddOpen} existingIdentifiers={existingIdentifiers} catalog={catalog} />
    </div>
  )
}
