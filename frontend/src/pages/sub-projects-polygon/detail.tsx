import { useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { ArrowLeft, CheckCircle2, ClipboardCheck, Download, Loader2, RotateCw, XCircle } from "lucide-react"
import { api } from "@/lib/api"
import { SubCheckDialog } from "@/pages/sub-checker/report"
import type { CheckResponse, SubReport } from "@/pages/sub-checker/types"
import type { PolygonProjectDetailResponse } from "@/pages/sub-projects/types"

function fmtDate(iso: string | null): string {
  if (!iso) return "-"
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  const normalized = hasTz ? iso : iso + "Z"
  return new Date(normalized).toLocaleString("ru-RU")
}
function fmtSize(bytes: number | null): string {
  if (!bytes) return "-"
  return `${(bytes / 1024 / 1024).toFixed(2)} МБ`
}

export function SubProjectsPolygonDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [changelog, setChangelog] = useState("")
  const [subFile, setSubFile] = useState<File | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState("")
  const [editAuthor, setEditAuthor] = useState("")
  const [checkReport, setCheckReport] = useState<SubReport | null>(null)
  const [checkOpen, setCheckOpen] = useState(false)
  const [checkVersionId, setCheckVersionId] = useState("")
  const queryKey = ["sub-project-polygon-detail", projectId]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<PolygonProjectDetailResponse>(`/api/v1/admin/sub-projects-polygon/${projectId}`),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey })

  const uploadVersion = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("sub_file", subFile!)
      form.set("changelog", changelog)
      return api.postForm<{ check: CheckResponse | null }>(`/api/v1/admin/sub-projects-polygon/${projectId}/versions`, form)
    },
    onSuccess: (res) => {
      toast.success("Версия загружена")
      setChangelog(""); setSubFile(null); invalidate()
      // Автопроверка по ГОСТу приезжает тем же ответом — сразу показываем отчёт.
      if (res?.check?.report) { setCheckReport(res.check.report); setCheckOpen(true) }
      else toast.warning("Автопроверка по ГОСТу не выполнена — проверьте корабль вручную")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const runCheck = useMutation({
    mutationFn: (versionId: number) =>
      api.post<CheckResponse>(`/api/v1/admin/sub-checker/projects/${projectId}/versions/${versionId}/check`),
    onSuccess: (res) => { setCheckReport(res.report); setCheckOpen(true); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  // Версия уже проверялась — открываем готовый отчёт вместо повторного
  // прогона (см. тот же приём в обычной зоне Sub-проектов).
  const [openingVersionId, setOpeningVersionId] = useState<number | null>(null)
  const openExistingReport = async (versionId: number, resultId: number) => {
    setOpeningVersionId(versionId)
    try {
      const res = await api.get<{ report: SubReport }>(`/api/v1/admin/sub-checker/results/${resultId}`)
      setCheckReport(res.report)
      setCheckOpen(true)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Не удалось загрузить отчёт")
    } finally {
      setOpeningVersionId(null)
    }
  }
  const checkOrOpen = (versionId: number) => {
    const existing = data?.checks?.[String(versionId)]
    if (existing) openExistingReport(versionId, existing.id)
    else runCheck.mutate(versionId)
  }

  const deleteVersion = useMutation({
    mutationFn: (vid: number) => api.delete(`/api/v1/admin/sub-projects-polygon/${projectId}/versions/${vid}`),
    onSuccess: () => { toast.success("Версия удалена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deploy = useMutation({
    mutationFn: (vid: number) => api.post(`/api/v1/admin/sub-projects-polygon/${projectId}/versions/${vid}/deploy`),
    onSuccess: () => { toast.success("Развёрнуто на Сервере 2"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const undeploy = useMutation({
    mutationFn: (vid: number) => api.post(`/api/v1/admin/sub-projects-polygon/${projectId}/versions/${vid}/undeploy`),
    onSuccess: () => { toast.success("Снято с Сервера 2"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteProject = useMutation({
    mutationFn: () => api.delete(`/api/v1/admin/sub-projects-polygon/${projectId}`),
    onSuccess: () => { toast.success("Проект удалён"); navigate("/sub-projects-polygon") },
  })

  const updateProject = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("name", editName.trim())
      form.set("author", editAuthor.trim())
      return api.postForm(`/api/v1/admin/sub-projects-polygon/${projectId}`, form)
    },
    onSuccess: () => { toast.success("Сохранено"); setEditing(false); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  if (isLoading || !data) return <Skeleton className="h-96" />
  const { project } = data

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={() => navigate("/sub-projects-polygon")} className="gap-1.5">
        <ArrowLeft className="size-4" /> К списку
      </Button>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>{project.name}</CardTitle>
            <div className="text-sm text-muted-foreground">
              Автор: {project.author} · <Badge variant="secondary">{data.department_name}</Badge>
            </div>
          </div>
          {data.can_edit_this && (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => { setEditName(project.name); setEditAuthor(project.author); setEditing(true) }}>Редактировать</Button>
              <Button variant="destructive" onClick={() => setConfirmDelete(true)}>Удалить</Button>
            </div>
          )}
        </CardHeader>
      </Card>

      {editing && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Редактирование</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div>
              <Label className="mb-1.5 block">Название</Label>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
            </div>
            <div>
              <Label className="mb-1.5 block">Автор</Label>
              <Input value={editAuthor} onChange={(e) => setEditAuthor(e.target.value)} />
            </div>
            <div className="flex gap-2">
              <Button disabled={!editName.trim() || !editAuthor.trim() || updateProject.isPending} onClick={() => updateProject.mutate()}>Сохранить</Button>
              <Button variant="outline" onClick={() => setEditing(false)}>Отмена</Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0">
          <CardTitle className="text-sm">Версии</CardTitle>
          {data.can_check_gost && data.versions.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <Select value={checkVersionId || String(data.versions[0].id)} onValueChange={setCheckVersionId}>
                <SelectTrigger className="h-8 w-[190px] text-xs"><SelectValue placeholder="Версия" /></SelectTrigger>
                <SelectContent>
                  {data.versions.map((v) => (
                    <SelectItem key={v.id} value={String(v.id)}>v{v.version_number} · {fmtDate(v.uploaded_at)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {(() => {
                const selectedId = Number(checkVersionId || data.versions[0].id)
                const hasReport = !!data.checks?.[String(selectedId)]
                const busy = runCheck.isPending || openingVersionId === selectedId
                return (
                  <>
                    <Button size="sm" disabled={busy} onClick={() => checkOrOpen(selectedId)}>
                      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <ClipboardCheck className="size-3.5" />}
                      {hasReport ? "Открыть отчёт" : "Проверить корабль"}
                    </Button>
                    {hasReport && (
                      <Button
                        size="sm" variant="outline" title="Проверить заново"
                        disabled={busy} onClick={() => runCheck.mutate(selectedId)}
                      >
                        <RotateCw className="size-3.5" />
                      </Button>
                    )}
                  </>
                )
              })()}
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {data.can_edit_this && (
            <div className="flex flex-wrap items-center gap-2">
              <Input type="file" accept=".sub" onChange={(e) => setSubFile(e.target.files?.[0] ?? null)} className="max-w-xs" />
              <Input value={changelog} onChange={(e) => setChangelog(e.target.value)} placeholder="Changelog" className="max-w-xs" />
              <Button size="sm" disabled={!subFile} onClick={() => uploadVersion.mutate()}>Загрузить</Button>
            </div>
          )}
          <Table>
            <TableHeader>
              <TableRow><TableHead>Версия</TableHead><TableHead>Размер</TableHead><TableHead>Загружена</TableHead><TableHead>ГОСТ</TableHead><TableHead>{data.sftp_server_2_name}</TableHead><TableHead /></TableRow>
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
                  <TableCell className="whitespace-nowrap">
                    {(() => {
                      const check = data.checks?.[String(v.id)]
                      if (!check) return <span className="text-xs text-muted-foreground">не проверялась</span>
                      return (
                        <span className="flex flex-wrap items-center gap-1">
                          {check.passed
                            ? <Badge className="gap-1 bg-success text-white"><CheckCircle2 className="size-3" /> ОК</Badge>
                            : <Badge variant="destructive" className="gap-1"><XCircle className="size-3" /> {check.error_count}</Badge>}
                          {check.warning_count > 0 && (
                            <Badge variant="outline" className="border-warning/50 text-warning">{check.warning_count}</Badge>
                          )}
                          {check.ship_class && <span className="text-xs text-muted-foreground">{check.ship_class}</span>}
                        </span>
                      )
                    })()}
                  </TableCell>
                  <TableCell><Badge variant={v.deployed_server_2 ? "default" : "secondary"}>{v.deployed_server_2 ? "Развёрнуто" : "Не развёрнуто"}</Badge></TableCell>
                  <TableCell className="space-x-1 whitespace-nowrap text-right">
                    {data.can_check_gost && (
                      <Button
                        size="sm" variant="outline"
                        title={data.checks?.[String(v.id)] ? "Открыть отчёт ГОСТ" : "Проверить по ГОСТу"}
                        disabled={runCheck.isPending || openingVersionId === v.id}
                        onClick={() => checkOrOpen(v.id)}
                      >
                        <ClipboardCheck className="size-3.5" />
                      </Button>
                    )}
                    {data.can_check_gost && data.checks?.[String(v.id)] && (
                      <Button
                        size="sm" variant="outline" title="Проверить заново"
                        disabled={runCheck.isPending} onClick={() => runCheck.mutate(v.id)}
                      >
                        <RotateCw className="size-3.5" />
                      </Button>
                    )}
                    <Button size="sm" variant="outline" asChild>
                      <a href={`/api/v1/admin/sub-projects-polygon/${projectId}/versions/${v.id}/download`}><Download className="size-3.5" /></a>
                    </Button>
                    {data.can_edit_this && (
                      <>
                        <Button size="sm" variant="outline" onClick={() => (v.deployed_server_2 ? undeploy : deploy).mutate(v.id)}>
                          {v.deployed_server_2 ? "Удалить с сервера" : "Загрузить на сервер"}
                        </Button>
                        <Button size="sm" variant="destructive" onClick={() => deleteVersion.mutate(v.id)}>✕</Button>
                      </>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {!data.versions.length && <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground">Версий нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <SubCheckDialog
        report={checkReport}
        open={checkOpen}
        onOpenChange={setCheckOpen}
        title={`Проверка по ГОСТу: ${project.name}`}
      />

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
