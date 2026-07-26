import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { PackagePlus } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName } from "@/lib/items"
import { warehouseApi } from "./warehouseApi"
import { dispatchActionsApi } from "./actionsApi"

const SERVERS = ["ASPER1", "ASPER2"]
const SLOT_COUNT = 8

interface Slot { identifier: string; quantity: number }

const emptySlots = (): (Slot | null)[] => Array(SLOT_COUNT).fill(null)

/** Cargo run — fill shuttle delivery slots from the virtual warehouse
 * (routes/warehouse.py), then queue a "cargo_spawn" ServerAction with the
 * chosen items. Stock is deducted server-side atomically with queuing —
 * this dialog's own remaining-count math is just for UX, the real check
 * happens again on submit. */
export function CargoDialog({ open, onOpenChange, defaultServer }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaultServer?: string
}) {
  const queryClient = useQueryClient()
  const { data: catalog } = useItemCatalog()
  const [serverName, setServerName] = useState(
    defaultServer && SERVERS.includes(defaultServer) ? defaultServer : SERVERS[0]
  )
  const [slots, setSlots] = useState<(Slot | null)[]>(emptySlots)

  const { data: warehouse } = useQuery({
    queryKey: ["warehouse"],
    queryFn: () => warehouseApi.list(),
    enabled: open,
  })

  const usedByIdentifier = new Map<string, number>()
  slots.forEach((s) => { if (s) usedByIdentifier.set(s.identifier, (usedByIdentifier.get(s.identifier) ?? 0) + s.quantity) })

  function addToSlot(identifier: string) {
    const stock = warehouse?.items.find((it) => it.identifier === identifier)?.quantity ?? 0
    const used = usedByIdentifier.get(identifier) ?? 0
    if (used >= stock) { toast.error("На складе больше нет этого предмета"); return }

    setSlots((cur) => {
      const existingIdx = cur.findIndex((s) => s?.identifier === identifier)
      if (existingIdx >= 0) {
        const next = [...cur]
        next[existingIdx] = { identifier, quantity: next[existingIdx]!.quantity + 1 }
        return next
      }
      const emptyIdx = cur.findIndex((s) => s === null)
      if (emptyIdx === -1) { toast.error("Все слоты груза заняты"); return cur }
      const next = [...cur]
      next[emptyIdx] = { identifier, quantity: 1 }
      return next
    })
  }

  function removeSlot(idx: number) {
    setSlots((cur) => { const next = [...cur]; next[idx] = null; return next })
  }
  function decrementSlot(idx: number) {
    setSlots((cur) => {
      const s = cur[idx]
      if (!s) return cur
      const next = [...cur]
      next[idx] = s.quantity > 1 ? { ...s, quantity: s.quantity - 1 } : null
      return next
    })
  }

  const filledSlots = slots.filter((s): s is Slot => s !== null)

  const sendMutation = useMutation({
    mutationFn: () => dispatchActionsApi.cargo(serverName, filledSlots.map((s) => ({ identifier: s.identifier, quantity: s.quantity }))),
    onSuccess: () => {
      toast.success("Груз поставлен в очередь на спавн")
      queryClient.invalidateQueries({ queryKey: ["warehouse"] })
      onOpenChange(false)
      setSlots(emptySlots())
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setSlots(emptySlots()) }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-1.5"><PackagePlus className="size-4" /> Груз в очередь на спавн</DialogTitle>
          <DialogDescription>Выберите предметы со склада — они займут слоты поставки челнока.</DialogDescription>
        </DialogHeader>

        <Select value={serverName} onValueChange={setServerName}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            {SERVERS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>

        <div>
          <div className="mb-1.5 text-xs font-semibold text-muted-foreground">
            Слоты поставки ({filledSlots.length}/{SLOT_COUNT})
          </div>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
            {slots.map((s, i) => (
              <button
                key={i}
                onClick={() => decrementSlot(i)}
                onContextMenu={(e) => { e.preventDefault(); removeSlot(i) }}
                title={s ? `${itemDisplayName(catalog, s.identifier)} ×${s.quantity} — клик уменьшает, ПКМ убирает слот целиком` : "Пусто"}
                className="relative flex aspect-square items-center justify-center rounded-md border border-dashed hover:bg-accent"
              >
                {s ? (
                  <>
                    <ItemIcon identifier={s.identifier} size={36} />
                    <span className="absolute bottom-0.5 right-0.5 rounded bg-background/90 px-1 text-[10px] font-semibold">×{s.quantity}</span>
                  </>
                ) : (
                  <span className="text-[10px] text-muted-foreground">Пусто</span>
                )}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1.5 text-xs font-semibold text-muted-foreground">Склад</div>
          <div className="flex max-h-56 flex-wrap gap-2 overflow-y-auto rounded-md border p-2">
            {warehouse?.items.map((it) => {
              const used = usedByIdentifier.get(it.identifier) ?? 0
              const remaining = it.quantity - used
              return (
                <button
                  key={it.identifier}
                  onClick={() => addToSlot(it.identifier)}
                  disabled={remaining <= 0}
                  title={`${itemDisplayName(catalog, it.identifier)} — доступно ${remaining}`}
                  className="relative flex flex-col items-center gap-1 rounded-md border p-1.5 text-[10px] hover:bg-accent disabled:opacity-30"
                >
                  <ItemIcon identifier={it.identifier} size={30} />
                  <span className="max-w-[60px] truncate">{itemDisplayName(catalog, it.identifier)}</span>
                  <span className="font-semibold">{remaining}</span>
                </button>
              )
            })}
            {!warehouse?.items.length && <p className="w-full py-4 text-center text-xs text-muted-foreground">Склад пуст</p>}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!filledSlots.length || sendMutation.isPending} onClick={() => sendMutation.mutate()}>
            Отправить в очередь
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
