import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { TriangleAlert } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { documentsApi } from "./api"

interface ExtraGrant {
  grantee_type: "admin" | "access_level"
  grantee_id: string | number
  level: "view" | "comment" | "edit"
  label: string
  is_base: boolean
}

/** Shown once, right after a freshly-created department document is closed
 * for the first time — prompts for sharing instead of leaving it silently
 * visible only to its owner. Prefilled from the destination folder's own
 * access grants (DepartmentFolderPermission), falling back to the
 * department's base access level (view-only) when the folder has none. */
export function SetupPermissionsDialog({ documentId, open, onDone }: {
  documentId: number
  open: boolean
  onDone: () => void
}) {
  const [accepted, setAccepted] = useState<Record<number, boolean>>({})
  const [granteeKind, setGranteeKind] = useState<"admin" | "group">("admin")
  const [granteeId, setGranteeId] = useState("")
  const [level, setLevel] = useState<"view" | "comment" | "edit">("view")
  const [extra, setExtra] = useState<ExtraGrant[]>([])

  const { data: suggested } = useQuery({
    queryKey: ["document-suggested-permissions", documentId],
    queryFn: () => documentsApi.permissions.suggestedDefaults(documentId),
    enabled: open,
  })
  const { data: candidates } = useQuery({
    queryKey: ["document-share-candidates", documentId],
    queryFn: () => documentsApi.shareCandidates(documentId),
    enabled: open,
  })

  useEffect(() => {
    if (suggested) setAccepted(Object.fromEntries(suggested.suggestions.map((_, i) => [i, true])))
  }, [suggested])

  const groupGranteeType = candidates?.group_grantee_type ?? "privilege_group"

  function addExtra() {
    if (!granteeId) return
    if (granteeKind === "admin") {
      const a = candidates?.admins.find((x) => String(x.id) === granteeId)
      setExtra((cur) => [...cur, { grantee_type: "admin", grantee_id: Number(granteeId), level, label: a?.name ?? "?", is_base: false }])
    } else {
      const g = candidates?.groups.find((x) => String(x.id) === granteeId)
      setExtra((cur) => [...cur, { grantee_type: "access_level", grantee_id: granteeId, level, label: g?.name ?? "?", is_base: !!g?.is_base }])
    }
    setGranteeId("")
  }

  const confirmMutation = useMutation({
    mutationFn: async () => {
      const toGrant = [
        ...(suggested?.suggestions ?? []).filter((_, i) => accepted[i]),
        ...extra,
      ]
      for (const g of toGrant) {
        await documentsApi.permissions.grant(documentId, { grantee_type: g.grantee_type, grantee_id: g.grantee_id, level: g.level })
      }
    },
    onSuccess: () => { toast.success("Доступ настроен"); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })

  const hasBaseSelected = [...(suggested?.suggestions ?? []).filter((_, i) => accepted[i]), ...extra].some((g) => g.is_base)

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onDone() }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Настройте доступ к документу</DialogTitle>
          <DialogDescription>Документ создан в отделе — выберите, кто ещё сможет его увидеть, прежде чем продолжить.</DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          {!suggested?.suggestions.length && !extra.length && (
            <p className="text-xs text-muted-foreground">Пока никому, кроме вас, доступ не предоставлен.</p>
          )}
          {suggested?.suggestions.map((s, i) => (
            <label key={i} className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm">
              <span className="flex items-center gap-2">
                <Checkbox checked={!!accepted[i]} onCheckedChange={(c) => setAccepted((cur) => ({ ...cur, [i]: c === true }))} />
                {s.label}
                {s.is_base && <TriangleAlert className="size-3.5 text-warning" />}
              </span>
              <span className="text-xs text-muted-foreground">{s.level === "edit" ? "изменение" : s.level === "comment" ? "комментирование" : "просмотр"}</span>
            </label>
          ))}
          {extra.map((g, i) => (
            <div key={`extra-${i}`} className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm">
              <span className="flex items-center gap-2">{g.label} {g.is_base && <TriangleAlert className="size-3.5 text-warning" />}</span>
              <span className="text-xs text-muted-foreground">{g.level === "edit" ? "изменение" : g.level === "comment" ? "комментирование" : "просмотр"}</span>
            </div>
          ))}
        </div>

        <div className="flex items-end gap-2 border-t pt-3">
          <div className="w-32 shrink-0">
            <Select value={granteeKind} onValueChange={(v) => { setGranteeKind(v as "admin" | "group"); setGranteeId("") }}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="admin">Сотрудник</SelectItem>
                <SelectItem value="group">{groupGranteeType === "access_level" ? "Уровень" : "Группа"}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex-1">
            <Select value={granteeId} onValueChange={setGranteeId}>
              <SelectTrigger><SelectValue placeholder="Выберите…" /></SelectTrigger>
              <SelectContent>
                {granteeKind === "admin"
                  ? candidates?.admins.map((a) => <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>)
                  : candidates?.groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}{g.is_base ? " (базовый)" : ""}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="w-40 shrink-0">
            <Select value={level} onValueChange={(v) => setLevel(v as typeof level)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="view">Просмотр</SelectItem>
                <SelectItem value="comment">Комментирование</SelectItem>
                <SelectItem value="edit">Редактирование</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button variant="outline" disabled={!granteeId} onClick={addExtra}>Добавить</Button>
        </div>

        {hasBaseSelected && (
          <p className="flex items-center gap-1.5 text-xs text-warning">
            <TriangleAlert className="size-3.5 shrink-0" /> Этот документ увидят все сотрудники отдела с базовым уровнем доступа.
          </p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onDone}>Пропустить</Button>
          <Button disabled={confirmMutation.isPending} onClick={() => confirmMutation.mutate()}>Готово</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
