import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Globe2, Trash2, Users, User } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { documentsApi, ACCESS_LABEL } from "@/pages/documents/api"

export function ShareDialog({ documentId, open, onOpenChange }: {
  documentId: number
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [granteeKind, setGranteeKind] = useState<"admin" | "group" | "everyone">("admin")
  const [granteeId, setGranteeId] = useState<string>("")
  const [level, setLevel] = useState<"view" | "comment" | "edit">("view")

  const { data: candidates } = useQuery({
    queryKey: ["document-share-candidates", documentId],
    queryFn: () => documentsApi.shareCandidates(documentId),
    enabled: open,
  })
  const { data: perms } = useQuery({
    queryKey: ["document-permissions", documentId],
    queryFn: () => documentsApi.permissions.list(documentId),
    enabled: open,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["document-permissions", documentId] })

  // The document belongs to a department → "Группа" means "уровень доступа
  // отдела" (grantee_type=access_level, string keys), else ND-wide
  // privilege groups (grantee_type=privilege_group, numeric ids).
  const groupGranteeType = candidates?.group_grantee_type ?? "privilege_group"
  const groupLabel = groupGranteeType === "access_level" ? "Уровень доступа" : "Группа"

  const grant = useMutation({
    mutationFn: () => documentsApi.permissions.grant(documentId, {
      grantee_type: granteeKind === "everyone" ? "everyone" : granteeKind === "group" ? groupGranteeType : "admin",
      grantee_id: granteeKind === "everyone"
        ? undefined
        : granteeKind === "group" && groupGranteeType === "access_level"
          ? granteeId
          : Number(granteeId),
      level,
    }),
    onSuccess: () => { toast.success("Доступ предоставлен"); setGranteeId(""); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const revoke = useMutation({
    mutationFn: (permissionId: number) => documentsApi.permissions.revoke(documentId, permissionId),
    onSuccess: () => { invalidate() },
  })

  const granteeLabel = (p: NonNullable<typeof perms>["permissions"][number]) => {
    if (p.grantee_type === "everyone") return "Все администраторы"
    if (p.grantee_type === "admin") return p.grantee_admin?.name ?? "-"
    if (p.grantee_type === "access_level") return p.grantee_access_level?.name ?? "-"
    return p.grantee_group?.name ?? "-"
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Доступ к документу</DialogTitle>
          <DialogDescription>Владелец: {perms?.owner?.name ?? "-"}. Управляйте тем, кто может просматривать, комментировать или редактировать документ.</DialogDescription>
        </DialogHeader>

        <div className="flex items-end gap-2">
          <div className="w-32 shrink-0">
            <Select value={granteeKind} onValueChange={(v) => { setGranteeKind(v as typeof granteeKind); setGranteeId("") }}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="admin">Админ</SelectItem>
                <SelectItem value="group">{groupLabel}</SelectItem>
                <SelectItem value="everyone">Все</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {granteeKind !== "everyone" && (
            <div className="flex-1">
              <Select value={granteeId} onValueChange={setGranteeId}>
                <SelectTrigger><SelectValue placeholder="Выберите…" /></SelectTrigger>
                <SelectContent>
                  {granteeKind === "admin"
                    ? candidates?.admins.map((a) => <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>)
                    : candidates?.groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}
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
          <Button disabled={granteeKind !== "everyone" && !granteeId} onClick={() => grant.mutate()}>Добавить</Button>
        </div>

        <Separator />

        <div className="max-h-64 space-y-2 overflow-y-auto">
          {perms?.permissions.length ? perms.permissions.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm">
              <div className="flex items-center gap-2">
                {p.grantee_type === "everyone" ? <Globe2 className="size-4 text-muted-foreground" /> : p.grantee_type === "privilege_group" || p.grantee_type === "access_level" ? <Users className="size-4 text-muted-foreground" /> : <User className="size-4 text-muted-foreground" />}
                <span className="font-medium">{granteeLabel(p)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{ACCESS_LABEL[p.level]}</span>
                <Button size="icon-xs" variant="ghost" className="text-destructive" onClick={() => revoke.mutate(p.id)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          )) : (
            <div className="py-4 text-center text-sm text-muted-foreground">Документ пока никому не расшарен.</div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Готово</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
