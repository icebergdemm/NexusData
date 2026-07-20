import { useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { Download, X, Undo2, UserCog } from "lucide-react"
import { api } from "@/lib/api"
import type { SubProjectDetailResponse } from "./types"

interface AdminOption { id: number; name: string }

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}
function fmtSize(bytes: number | null): string {
  if (!bytes) return "-"
  return `${(bytes / 1024 / 1024).toFixed(2)} МБ`
}

export function SubProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [changelog, setChangelog] = useState("")
  const [subFile, setSubFile] = useState<File | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [changeOwnerOpen, setChangeOwnerOpen] = useState(false)
  const [newOwnerId, setNewOwnerId] = useState("")
  const queryKey = ["sub-project-detail", projectId]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<SubProjectDetailResponse>(`/api/v1/admin/sub-projects/${projectId}`),
  })

  const { data: ownerCandidates } = useQuery({
    queryKey: ["sub-project-owner-candidates"],
    queryFn: () => api.get<{ admins: AdminOption[] }>("/api/v1/admin/sub-projects/change-owner-candidates"),
    enabled: changeOwnerOpen,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey })

  const uploadVersion = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("sub_file", subFile!)
      form.set("changelog", changelog)
      return api.postForm(`/api/v1/admin/sub-projects/${projectId}/versions`, form)
    },
    onSuccess: () => { toast.success("Версия загружена"); setChangelog(""); setSubFile(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteVersion = useMutation({
    mutationFn: (vid: number) => api.delete(`/api/v1/admin/sub-projects/${projectId}/versions/${vid}`),
    onSuccess: () => { toast.success("Версия удалена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const restoreVersion = useMutation({
    mutationFn: (vid: number) => api.post(`/api/v1/admin/sub-projects/${projectId}/versions/${vid}/restore`),
    onSuccess: () => { toast.success("Версия восстановлена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const changeOwner = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/sub-projects/${projectId}/change-owner`, { new_owner_admin_id: Number(newOwnerId) }),
    onSuccess: () => { toast.success("Владелец изменён"); setChangeOwnerOpen(false); setNewOwnerId(""); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deploy = useMutation({
    mutationFn: ({ vid, server }: { vid: number; server: number }) =>
      api.post(`/api/v1/admin/sub-projects/${projectId}/versions/${vid}/deploy`, { server }),
    onSuccess: () => { toast.success("Развёрнуто"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const undeploy = useMutation({
    mutationFn: ({ vid, server }: { vid: number; server: number }) =>
      api.post(`/api/v1/admin/sub-projects/${projectId}/versions/${vid}/undeploy`, { server }),
    onSuccess: () => { toast.success("Снято с сервера"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const publishDiscord = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/sub-projects/${projectId}/discord/publish`),
    onSuccess: () => { toast.success("Опубликовано в Discord"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const unpublishDiscord = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/sub-projects/${projectId}/discord/unpublish`),
    onSuccess: () => { toast.success("Удалено из Discord"); invalidate() },
  })

  const deleteProject = useMutation({
    mutationFn: () => api.delete(`/api/v1/admin/sub-projects/${projectId}`),
    onSuccess: () => { toast.success("Проект удалён"); navigate("/sub-projects") },
  })

  const assignCategory = useMutation({
    mutationFn: (catId: number) => api.post(`/api/v1/admin/sub-projects/${projectId}/categories/${catId}`),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error(e.message),
  })

  const removeCategory = useMutation({
    mutationFn: (catId: number) => api.delete(`/api/v1/admin/sub-projects/${projectId}/categories/${catId}`),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error(e.message),
  })

  if (isLoading || !data) return <Skeleton className="h-96" />
  const { project } = data
  const s1 = data.sftp_server_1_name, s2 = data.sftp_server_2_name

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>{project.name}</CardTitle>
            <div className="text-sm text-muted-foreground">Автор: {project.author}</div>
          </div>
          <div className="flex gap-2">
            {data.can_edit_this && <Button variant="outline" onClick={() => navigate(`/sub-projects/${projectId}/edit`)}>Редактировать</Button>}
            {project.discord_message_id ? (
              data.can_publish_discord && <Button variant="outline" onClick={() => unpublishDiscord.mutate()}>Убрать из Discord</Button>
            ) : (
              data.can_publish_discord && <Button variant="outline" onClick={() => publishDiscord.mutate()}>Опубликовать в Discord</Button>
            )}
            {data.can_change_owner && (
              <Button variant="outline" onClick={() => setChangeOwnerOpen(true)}><UserCog className="size-4" /> Сменить владельца</Button>
            )}
            {data.can_delete_this && (
              <Button variant="destructive" onClick={() => setConfirmDelete(true)}>Удалить</Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><div className="text-muted-foreground">Тип</div>{project.project_type ?? "-"}</div>
          <div><div className="text-muted-foreground">Экипаж</div>{project.crew ?? "-"}</div>
          <div><div className="text-muted-foreground">Реактор</div>{project.has_reactor ?? "-"}</div>
          <div><div className="text-muted-foreground">Палубы</div>{project.decks ?? "-"}</div>
          {project.description && <div className="col-span-full"><div className="text-muted-foreground">Описание</div>{project.description}</div>}
          {project.features && <div className="col-span-full"><div className="text-muted-foreground">Особенности</div>{project.features}</div>}
          <div className="col-span-full">
            <div className="mb-1.5 text-muted-foreground">Категории</div>
            <div className="flex flex-wrap items-center gap-1.5">
              {data.categories.map((c) => (
                <Badge key={c.id} variant="secondary" className="gap-1" style={{ borderColor: c.color }}>
                  {c.icon} {c.name}
                  {data.can_edit_categories && (
                    <button onClick={() => removeCategory.mutate(c.id)} className="ml-0.5 hover:text-destructive">
                      <X className="size-3" />
                    </button>
                  )}
                </Badge>
              ))}
              {!data.categories.length && <span className="text-sm text-muted-foreground">Не назначены</span>}
              {data.can_edit_categories && (
                <Select value="" onValueChange={(v) => assignCategory.mutate(Number(v))}>
                  <SelectTrigger className="h-7 w-auto min-w-[160px] text-xs"><SelectValue placeholder="+ Добавить категорию" /></SelectTrigger>
                  <SelectContent>
                    {data.all_categories
                      .filter((c) => !data.categories.some((assigned) => assigned.id === c.id))
                      .map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>{c.icon} {c.name}</SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Версии</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {data.can_upload_version && (
            <div className="flex flex-wrap items-center gap-2">
              <Input type="file" accept=".sub" onChange={(e) => setSubFile(e.target.files?.[0] ?? null)} className="max-w-xs" />
              <Input value={changelog} onChange={(e) => setChangelog(e.target.value)} placeholder="Changelog" className="max-w-xs" />
              <Button size="sm" disabled={!subFile} onClick={() => uploadVersion.mutate()}>Загрузить</Button>
            </div>
          )}
          {(() => {
            const canDeployAny = data.can_deploy_server_1 || data.can_deploy_server_2 || data.can_undeploy_server_1 || data.can_undeploy_server_2
            const hasActionsCol = data.can_download || canDeployAny || data.can_delete_version
            return (
              <Table>
                <TableHeader>
                  <TableRow><TableHead>Версия</TableHead><TableHead>Размер</TableHead><TableHead>Загружена</TableHead><TableHead>Серверы</TableHead>{hasActionsCol && <TableHead />}</TableRow>
                </TableHeader>
                <TableBody>
                  {data.versions.map((v) => (
                    <TableRow key={v.id}>
                      <TableCell>
                        v{v.version_number} {v.changelog && <span className="text-xs text-muted-foreground">- {v.changelog}</span>}
                        <div className="font-mono text-xs text-muted-foreground">{v.filename}</div>
                      </TableCell>
                      <TableCell>{fmtSize(v.file_size)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{fmtDate(v.uploaded_at)}</TableCell>
                      <TableCell className="space-x-1">
                        <Badge variant={v.deployed_server_1 ? "default" : "secondary"}>{s1}</Badge>
                        <Badge variant={v.deployed_server_2 ? "default" : "secondary"}>{s2}</Badge>
                      </TableCell>
                      {hasActionsCol && (
                        <TableCell className="space-x-1 whitespace-nowrap text-right">
                          {data.can_download && (
                            <Button size="sm" variant="outline" asChild>
                              <a href={`/api/v1/admin/sub-projects/${projectId}/versions/${v.id}/download`}><Download className="size-3.5" /></a>
                            </Button>
                          )}
                          {(v.deployed_server_1 ? data.can_undeploy_server_1 : data.can_deploy_server_1) && (
                            <Button size="sm" variant="outline" onClick={() => (v.deployed_server_1 ? undeploy : deploy).mutate({ vid: v.id, server: 1 })}>{v.deployed_server_1 ? `Удалить с ${s1}` : `Загрузить на ${s1}`}</Button>
                          )}
                          {(v.deployed_server_2 ? data.can_undeploy_server_2 : data.can_deploy_server_2) && (
                            <Button size="sm" variant="outline" onClick={() => (v.deployed_server_2 ? undeploy : deploy).mutate({ vid: v.id, server: 2 })}>{v.deployed_server_2 ? `Удалить с ${s2}` : `Загрузить на ${s2}`}</Button>
                          )}
                          {data.can_delete_version && <Button size="sm" variant="destructive" onClick={() => deleteVersion.mutate(v.id)}>✕</Button>}
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                  {!data.versions.length && <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">Версий нет</TableCell></TableRow>}
                </TableBody>
              </Table>
            )
          })()}
        </CardContent>
      </Card>

      {data.can_restore_version && data.deleted_versions.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Удалённые версии</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow><TableHead>Версия</TableHead><TableHead>Размер</TableHead><TableHead>Загружена</TableHead><TableHead /></TableRow>
              </TableHeader>
              <TableBody>
                {data.deleted_versions.map((v) => (
                  <TableRow key={v.id}>
                    <TableCell>
                      v{v.version_number} {v.changelog && <span className="text-xs text-muted-foreground">- {v.changelog}</span>}
                      <div className="font-mono text-xs text-muted-foreground">{v.filename}</div>
                    </TableCell>
                    <TableCell>{fmtSize(v.file_size)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{fmtDate(v.uploaded_at)}</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="outline" onClick={() => restoreVersion.mutate(v.id)}><Undo2 className="size-3.5" /> Восстановить</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Dialog open={changeOwnerOpen} onOpenChange={setChangeOwnerOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Сменить владельца проекта</DialogTitle></DialogHeader>
          <Select value={newOwnerId} onValueChange={setNewOwnerId}>
            <SelectTrigger><SelectValue placeholder="Выберите администратора" /></SelectTrigger>
            <SelectContent>
              {ownerCandidates?.admins.map((a) => (
                <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button variant="outline" onClick={() => setChangeOwnerOpen(false)}>Отмена</Button>
            <Button disabled={!newOwnerId || changeOwner.isPending} onClick={() => changeOwner.mutate()}>Сохранить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Удалить проект?"
        description="Проект, все его версии и файлы будут удалены безвозвратно."
        onConfirm={() => { deleteProject.mutate(); setConfirmDelete(false) }}
      />
    </div>
  )
}
