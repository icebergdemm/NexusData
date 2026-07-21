import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trash2, TriangleAlert } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { departmentsApi, type DeptDocumentFolder, type DepartmentAccessLevel } from "./api"

/** Granted access list + grant form for one department document folder —
 * shown wherever a folder's settings can be opened (department's own
 * Документация tab, and the generic Документы page's "Отделы" directory).
 * Extracted as its own component so both places show/manage the same
 * additive ACL instead of one of them silently lacking it. */
export function FolderAccessFields({ open, departmentId, folder, accessLevels }: {
  open: boolean
  departmentId: number
  folder: DeptDocumentFolder
  accessLevels: DepartmentAccessLevel[]
}) {
  const queryClient = useQueryClient()
  const [granteeType, setGranteeType] = useState<"access_level" | "member">("access_level")
  const [accessLevelKey, setAccessLevelKey] = useState("")
  const [memberAdminId, setMemberAdminId] = useState("")
  const [level, setLevel] = useState<"view" | "edit">("view")

  const { data: permsData } = useQuery({
    queryKey: ["dept-folder-permissions", departmentId, folder.id],
    queryFn: () => departmentsApi.documentFolders.permissions.list(departmentId, folder.id),
    enabled: open,
  })
  const { data: membersData } = useQuery({
    queryKey: ["department-members", departmentId],
    queryFn: () => departmentsApi.members.list(departmentId),
    enabled: open,
  })

  const grantMutation = useMutation({
    mutationFn: () => departmentsApi.documentFolders.permissions.grant(departmentId, folder.id, {
      grantee_type: granteeType,
      access_level_key: granteeType === "access_level" ? accessLevelKey : undefined,
      member_admin_id: granteeType === "member" ? Number(memberAdminId) : undefined,
      level,
    }),
    onSuccess: () => {
      toast.success("Доступ выдан")
      queryClient.invalidateQueries({ queryKey: ["dept-folder-permissions", departmentId, folder.id] })
      setAccessLevelKey(""); setMemberAdminId("")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const revokeMutation = useMutation({
    mutationFn: (permissionId: number) => departmentsApi.documentFolders.permissions.revoke(departmentId, folder.id, permissionId),
    onSuccess: () => {
      toast.success("Доступ отозван")
      queryClient.invalidateQueries({ queryKey: ["dept-folder-permissions", departmentId, folder.id] })
    },
  })

  return (
    <div className="space-y-3 border-t pt-3">
      <div className="text-sm font-medium">Доступ к папке</div>

      <div className="space-y-2">
        {!permsData?.permissions.length && <p className="text-xs text-muted-foreground">Дополнительных прав пока нет.</p>}
        {permsData?.permissions.map((p) => {
          const isBase = p.grantee_type === "access_level" && accessLevels.find((l) => l.key === p.access_level_key)?.kind === "base"
          return (
            <div key={p.id} className="flex items-center justify-between rounded-md border px-2.5 py-1.5 text-sm">
              <span className="flex items-center gap-1.5">
                {p.grantee_type === "access_level" ? `Уровень: ${accessLevels.find((l) => l.key === p.access_level_key)?.name ?? p.access_level_key}` : `Сотрудник: ${p.member_name ?? p.member_admin_id}`}
                {" — "}{p.level === "edit" ? "изменение" : "просмотр"}
                {isBase && <TriangleAlert className="size-3.5 text-warning" />}
              </span>
              <Button size="icon-xs" variant="ghost" className="text-destructive" onClick={() => revokeMutation.mutate(p.id)}>
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          )
        })}
      </div>

      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <Select value={granteeType} onValueChange={(v) => setGranteeType(v as "access_level" | "member")}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="access_level">Уровень доступа</SelectItem>
              <SelectItem value="member">Конкретный сотрудник</SelectItem>
            </SelectContent>
          </Select>
          <Select value={level} onValueChange={(v) => setLevel(v as "view" | "edit")}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="view">Просмотр</SelectItem>
              <SelectItem value="edit">Изменение</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {granteeType === "access_level" ? (
          <Select value={accessLevelKey} onValueChange={setAccessLevelKey}>
            <SelectTrigger><SelectValue placeholder="Выберите уровень" /></SelectTrigger>
            <SelectContent>
              {accessLevels.map((l) => <SelectItem key={l.key} value={l.key}>{l.name}{l.kind === "base" ? " (базовый)" : ""}</SelectItem>)}
            </SelectContent>
          </Select>
        ) : (
          <Select value={memberAdminId} onValueChange={setMemberAdminId}>
            <SelectTrigger><SelectValue placeholder="Выберите сотрудника" /></SelectTrigger>
            <SelectContent>
              {membersData?.members.map((m) => <SelectItem key={m.admin_id} value={String(m.admin_id)}>{m.admin_name ?? `#${m.admin_id}`}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        {granteeType === "access_level" && accessLevels.find((l) => l.key === accessLevelKey)?.kind === "base" && (
          <p className="flex items-center gap-1.5 text-xs text-warning">
            <TriangleAlert className="size-3.5 shrink-0" /> Эту папку увидят все сотрудники отдела с базовым уровнем доступа.
          </p>
        )}
        <Button
          size="sm"
          disabled={(granteeType === "access_level" ? !accessLevelKey : !memberAdminId) || grantMutation.isPending}
          onClick={() => grantMutation.mutate()}
        >
          Выдать доступ
        </Button>
      </div>
    </div>
  )
}
