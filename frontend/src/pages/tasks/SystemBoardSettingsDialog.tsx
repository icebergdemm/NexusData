import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Archive, ClipboardList, LoaderCircle, Settings2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { tasksApi, type SystemTaskBoardSettings } from "./api"

export function SystemBoardSettingsDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<SystemTaskBoardSettings | null>(null)
  const settingsQuery = useQuery({
    queryKey: ["system-task-board-settings"],
    queryFn: tasksApi.boards.systemSettings.get,
    enabled: open,
  })

  useEffect(() => {
    if (settingsQuery.data) setDraft(settingsQuery.data.settings)
  }, [settingsQuery.data])

  const saveMutation = useMutation({
    mutationFn: () => tasksApi.boards.systemSettings.update(draft as SystemTaskBoardSettings),
    onSuccess: ({ settings }) => {
      setDraft(settings)
      queryClient.setQueryData(["system-task-board-settings"], { settings })
      toast.success("Настройки заявок сохранены")
      onOpenChange(false)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const limit = draft?.request_limit
  const cleanup = draft?.archive_cleanup
  const validLimit = !!limit && Number.isInteger(limit.max_active) && limit.max_active >= 1 && limit.max_active <= 1000
  const validCleanup = !!cleanup && Number.isInteger(cleanup.retention_days) && cleanup.retention_days >= 1 && cleanup.retention_days <= 3650

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] w-[calc(100vw-2rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <div className="mb-1 flex size-9 items-center justify-center rounded-md border bg-muted/40 text-primary">
            <Settings2 className="size-4" />
          </div>
          <DialogTitle>Настройки доски заявок</DialogTitle>
          <DialogDescription>Правила для пользовательских заявок. Изменения применяются сразу после сохранения.</DialogDescription>
        </DialogHeader>

        {settingsQuery.isError ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4">
            <p className="text-sm font-medium">Не удалось загрузить настройки доски</p>
            <p className="mt-1 text-xs text-muted-foreground">Проверьте подключение и попробуйте ещё раз.</p>
            <Button className="mt-3" size="sm" variant="outline" onClick={() => settingsQuery.refetch()}>Повторить</Button>
          </div>
        ) : (
          <div className="space-y-6">
          <section aria-labelledby="request-limit-heading" className="space-y-3">
            <div className="flex items-start gap-3 border-b pb-3">
              <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                <ClipboardList className="size-4" />
              </div>
              <div>
                <h3 id="request-limit-heading" className="text-sm font-semibold">Лимит заявок</h3>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">Ограничьте число необработанных заявок, которые пользователь может отправить одновременно.</p>
              </div>
            </div>

            <div className="rounded-md border bg-card p-4">
              {settingsQuery.isLoading || !limit ? (
                <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Загружаю настройки…</div>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <Label htmlFor="request-limit-enabled" className="text-sm font-medium">Ограничение заявок на пользователя</Label>
                      <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">Считаются только заявки в колонке «Рассмотрение». После смены статуса слот освобождается.</p>
                    </div>
                    <Switch
                      id="request-limit-enabled"
                      checked={limit.enabled}
                      disabled={saveMutation.isPending}
                      onCheckedChange={(enabled) => setDraft((current) => current ? { ...current, request_limit: { ...current.request_limit, enabled } } : current)}
                    />
                  </div>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
                    <div className="space-y-1">
                      <Label htmlFor="request-limit-count" className="text-sm font-medium">Максимум на рассмотрении</Label>
                      <p className="text-xs text-muted-foreground">Суммарно по всем типам заявок</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Input
                        id="request-limit-count"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={1000}
                        step={1}
                        value={limit.max_active}
                        disabled={saveMutation.isPending}
                        onChange={(event) => {
                          const value = event.target.valueAsNumber
                          setDraft((current) => current ? { ...current, request_limit: { ...current.request_limit, max_active: Number.isNaN(value) ? 0 : value } } : current)
                        }}
                        className="w-24 text-center tabular-nums"
                      />
                      <span className="text-xs text-muted-foreground">заявки</span>
                    </div>
                  </div>
                  {!validLimit && <p role="alert" className="mt-3 text-xs text-destructive">Введите целое число от 1 до 1000.</p>}
                </>
              )}
            </div>
          </section>
          <section aria-labelledby="archive-cleanup-heading" className="space-y-3">
            <div className="flex items-start gap-3 border-b pb-3">
              <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                <Archive className="size-4" />
              </div>
              <div>
                <h3 id="archive-cleanup-heading" className="text-sm font-semibold">Очистка архива</h3>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">Старые отклонённые и архивные заявки удаляются автоматически раз в месяц.</p>
              </div>
            </div>

            <div className="rounded-md border bg-card p-4">
              {settingsQuery.isLoading || !cleanup ? (
                <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Загружаю настройки…</div>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <Label htmlFor="archive-cleanup-enabled" className="text-sm font-medium">Автоматически очищать архив</Label>
                      <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">Активные заявки очистка не затрагивает.</p>
                    </div>
                    <Switch
                      id="archive-cleanup-enabled"
                      checked={cleanup.enabled}
                      disabled={saveMutation.isPending}
                      onCheckedChange={(enabled) => setDraft((current) => current ? { ...current, archive_cleanup: { ...current.archive_cleanup, enabled } } : current)}
                    />
                  </div>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
                    <div className="space-y-1">
                      <Label htmlFor="archive-retention-days" className="text-sm font-medium">Хранить в архиве</Label>
                      <p className="text-xs text-muted-foreground">Срок после отклонения или архивации</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Input
                        id="archive-retention-days"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={3650}
                        step={1}
                        value={cleanup.retention_days}
                        disabled={saveMutation.isPending}
                        onChange={(event) => {
                          const value = event.target.valueAsNumber
                          setDraft((current) => current ? { ...current, archive_cleanup: { ...current.archive_cleanup, retention_days: Number.isNaN(value) ? 0 : value } } : current)
                        }}
                        className="w-24 text-center tabular-nums"
                      />
                      <span className="text-xs text-muted-foreground">дней</span>
                    </div>
                  </div>
                  {!validCleanup && <p role="alert" className="mt-3 text-xs text-destructive">Введите целое число от 1 до 3650 дней.</p>}
                </>
              )}
            </div>
          </section>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button
            disabled={!draft || !validLimit || !validCleanup || settingsQuery.isLoading || settingsQuery.isError || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending && <LoaderCircle className="size-4 animate-spin" />}
            Сохранить настройки
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
