import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  CheckCircle2, ClipboardCheck, History, Loader2, Settings2, Ship, XCircle,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import { CheckerClasses } from "./classes"
import { SubFileDropZone } from "./DropZone"
import { SubCheckDialog, SubCheckReport } from "./report"
import { CheckerSettings } from "./settings"
import type { CheckerConfig, CheckResponse, ConfigResponse, HistoryRow, SubReport } from "./types"

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

function VerdictBadge({ passed, errors, warnings }: { passed: boolean; errors: number; warnings: number }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {passed
        ? <Badge className="gap-1 bg-success text-white"><CheckCircle2 className="size-3" /> Соответствует</Badge>
        : <Badge variant="destructive" className="gap-1"><XCircle className="size-3" /> {errors} наруш.</Badge>}
      {warnings > 0 && <Badge variant="outline" className="border-warning/50 text-warning">{warnings} предупр.</Badge>}
    </span>
  )
}

function UploadTab({ canCheck }: { canCheck: boolean }) {
  const [file, setFile] = useState<File | null>(null)
  const [report, setReport] = useState<SubReport | null>(null)
  const queryClient = useQueryClient()

  const check = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("sub_file", file!)
      return api.postForm<CheckResponse>("/api/v1/admin/sub-checker/check", form)
    },
    onSuccess: (res) => {
      setReport(res.report)
      queryClient.invalidateQueries({ queryKey: ["sub-checker-history"] })
      toast[res.report.passed ? "success" : "error"](res.report.summary ?? "Проверка выполнена")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 p-4">
          <div className="min-w-[280px] flex-1">
            <SubFileDropZone
              file={file}
              disabled={!canCheck}
              onFile={(f) => { setFile(f); setReport(null) }}
            />
          </div>
          <Button disabled={!file || !canCheck || check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? <Loader2 className="size-4 animate-spin" /> : <ClipboardCheck className="size-4" />}
            Проверить
          </Button>
        </CardContent>
      </Card>

      {!canCheck && (
        <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">
          У вас нет права на запуск проверок.
        </CardContent></Card>
      )}

      {report && (
        <Card><CardContent className="p-4"><SubCheckReport report={report} /></CardContent></Card>
      )}
    </div>
  )
}

function HistoryTab() {
  const [page, setPage] = useState(1)
  const [openId, setOpenId] = useState<number | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["sub-checker-history", page],
    queryFn: () => api.get<{ results: HistoryRow[]; total: number; page: number; total_pages: number }>(
      `/api/v1/admin/sub-checker/history?page=${page}`),
  })

  const { data: detail } = useQuery({
    queryKey: ["sub-checker-result", openId],
    queryFn: () => api.get<HistoryRow & { report: SubReport }>(`/api/v1/admin/sub-checker/results/${openId}`),
    enabled: openId != null,
  })

  if (isLoading) return <Skeleton className="h-64" />

  return (
    <>
      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Корабль</TableHead>
                <TableHead>Проект / версия</TableHead>
                <TableHead>Класс</TableHead>
                <TableHead>Итог</TableHead>
                <TableHead>Кто и когда</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.results.map((row) => (
                <TableRow key={row.id} className="cursor-pointer" onClick={() => setOpenId(row.id)}>
                  <TableCell className="font-medium">
                    {row.submarine_name ?? row.filename ?? `#${row.id}`}
                    {row.filename && <div className="font-mono text-xs text-muted-foreground">{row.filename}</div>}
                  </TableCell>
                  <TableCell className="text-sm">
                    {row.project_id ? (
                      <Link to={`/sub-projects/${row.project_id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                        {row.project_name ?? `Проект #${row.project_id}`}
                      </Link>
                    ) : <span className="text-muted-foreground">разовая загрузка</span>}
                    {row.version_number != null && <span className="text-muted-foreground"> · v{row.version_number}</span>}
                  </TableCell>
                  <TableCell className="text-sm">
                    {row.ship_class ?? "-"}
                    {row.tonnage != null && <div className="text-xs text-muted-foreground">{Math.round(row.tonnage)} т</div>}
                  </TableCell>
                  <TableCell>
                    <VerdictBadge passed={row.passed} errors={row.error_count} warnings={row.warning_count} />
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {row.is_auto ? <Badge variant="outline" className="mr-1 text-[10px]">авто</Badge> : null}
                    {row.checked_by ?? "-"}
                    <div className="text-xs">{fmtDate(row.created_at)}</div>
                  </TableCell>
                </TableRow>
              ))}
              {!data?.results.length && (
                <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  Проверок пока не было
                </TableCell></TableRow>
              )}
            </TableBody>
          </Table>
          {data && data.total_pages > 1 && (
            <div className="flex items-center justify-center gap-2 border-t bg-muted/30 py-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
              <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
              <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
            </div>
          )}
        </CardContent>
      </Card>

      <SubCheckDialog
        report={detail?.report ?? null}
        open={openId != null}
        onOpenChange={(open) => { if (!open) setOpenId(null) }}
        title="Отчёт о проверке"
      />
    </>
  )
}

export function SubCheckerPage() {
  const { data: admin } = useCurrentAdmin()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<CheckerConfig | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)

  const { data: cfg, isLoading } = useQuery({
    queryKey: ["sub-checker-config"],
    queryFn: () => api.get<ConfigResponse>("/api/v1/admin/sub-checker/config"),
  })

  // Черновик живёт локально, пока его не сохранят: правка норматива — это
  // длинная работа, и дёргать сервер на каждое нажатие клавиши незачем.
  useEffect(() => {
    if (cfg?.config) setDraft(cfg.config)
  }, [cfg?.revision, cfg?.config])

  const save = useMutation({
    mutationFn: (config: CheckerConfig) => api.put<ConfigResponse>("/api/v1/admin/sub-checker/config", { config }),
    onSuccess: (res) => {
      toast.success(`Настройки сохранены (ревизия ${res.revision})`)
      queryClient.setQueryData(["sub-checker-config"], res)
      setDraft(res.config)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const reset = useMutation({
    mutationFn: () => api.post<ConfigResponse>("/api/v1/admin/sub-checker/config/reset"),
    onSuccess: (res) => {
      toast.success("Настройки сброшены к ГОСТу")
      queryClient.setQueryData(["sub-checker-config"], res)
      setDraft(res.config)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const dirty = useMemo(
    () => !!draft && !!cfg && JSON.stringify(draft) !== JSON.stringify(cfg.config),
    [draft, cfg],
  )

  const canCheck = canDo(admin, "sub_checker", "check")
  const canRules = canDo(admin, "sub_checker", "manage_rules")
  const canClasses = canDo(admin, "sub_checker", "manage_classes")

  if (isLoading || !draft || !cfg) {
    return (
      <div>
        <PageHeader title="Чекер кораблей" description="Проверка .sub по ГОСТу КБ «Гайка»" icon={Ship} />
        <Skeleton className="h-96" />
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Чекер кораблей"
        description={`Проверка .sub по ГОСТу КБ «Гайка» · ревизия настроек ${cfg.revision}${
          cfg.updated_by ? ` · последняя правка: ${cfg.updated_by}` : ""}`}
        icon={Ship}
      />
      <Tabs defaultValue="check" className="space-y-4">
        <TabsList>
          <TabsTrigger value="check" className="gap-1.5"><ClipboardCheck className="size-3.5" /> Проверка файла</TabsTrigger>
          <TabsTrigger value="history" className="gap-1.5"><History className="size-3.5" /> История</TabsTrigger>
          <TabsTrigger value="settings" className="gap-1.5"><Settings2 className="size-3.5" /> Настройки проверок</TabsTrigger>
          <TabsTrigger value="classes" className="gap-1.5"><Ship className="size-3.5" /> Классы кораблей</TabsTrigger>
        </TabsList>

        <TabsContent value="check"><UploadTab canCheck={canCheck} /></TabsContent>
        <TabsContent value="history"><HistoryTab /></TabsContent>

        <TabsContent value="settings">
          <CheckerSettings
            config={draft}
            onChange={setDraft}
            canEdit={canRules}
            formulaContext={cfg.formula_context}
            saving={save.isPending}
            dirty={dirty}
            onSave={() => save.mutate(draft)}
            onReset={() => setConfirmReset(true)}
          />
        </TabsContent>

        <TabsContent value="classes">
          <CheckerClasses
            classes={draft.classes}
            onChange={(classes) => setDraft({ ...draft, classes })}
            canEdit={canClasses}
            saving={save.isPending}
            dirty={dirty}
            onSave={() => save.mutate(draft)}
            onReset={() => setConfirmReset(true)}
          />
        </TabsContent>
      </Tabs>

      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Сбросить настройки чекера?"
        description="Все правила, формулы, списки запрещённых предметов и классы вернутся к заводским значениям из ГОСТа. Текущая конфигурация сохранится в истории ревизий."
        onConfirm={() => { reset.mutate(); setConfirmReset(false) }}
      />
    </div>
  )
}
