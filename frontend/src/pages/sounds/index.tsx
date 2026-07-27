import { useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Music, Upload, Trash2, Pencil, RotateCcw, Settings, Download, AlertTriangle,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface SoundItem {
  filename: string
  size_bytes: number
  modified_at: string | null
  has_backup: boolean
  uploaded_by: string | null
  uploaded_at: string | null
  deleted_at?: string | null
  deleted_by?: string | null
  is_deleted_remotely: boolean
}
interface SoundsResponse {
  server_name: string
  files: SoundItem[]
  deleted: SoundItem[]
  not_configured: boolean
  error?: string
}
interface SoundPermissions {
  can_upload: boolean
  can_rename: boolean
  can_delete: boolean
  can_restore: boolean
  is_superuser: boolean
  servers: string[]
  max_size_mb: number
}
interface ServerSetting {
  server_number: number
  server_name: string
  sftp_host: string | null
  sftp_configured: boolean
  sounds_dir: string | null
}

function formatSize(bytes: number): string {
  if (!bytes) return "-"
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

/** Настройки SFTP-каталогов со звуками. Только суперпользователь: путь
 *  определяет, куда пишутся файлы на боевых игровых серверах. */
function SettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const queryClient = useQueryClient()
  const [dirs, setDirs] = useState<Record<number, string>>({})

  const { data } = useQuery({
    queryKey: ["sounds-settings"],
    queryFn: async () => {
      const res = await api.get<{ servers: ServerSetting[] }>("/api/v1/admin/sounds/settings")
      setDirs(Object.fromEntries(res.servers.map((s) => [s.server_number, s.sounds_dir ?? ""])))
      return res
    },
    enabled: open,
  })

  const save = useMutation({
    mutationFn: () => api.post("/api/v1/admin/sounds/settings", {
      servers: (data?.servers ?? []).map((s) => ({
        server_number: s.server_number,
        sounds_dir: dirs[s.server_number] ?? "",
      })),
    }),
    onSuccess: () => {
      toast.success("Настройки сохранены")
      queryClient.invalidateQueries({ queryKey: ["sounds-settings"] })
      queryClient.invalidateQueries({ queryKey: ["sounds"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const test = useMutation({
    mutationFn: (server_number: number) =>
      api.post<{ ok: boolean; total_entries: number; sound_files: number }>(
        "/api/v1/admin/sounds/settings/test",
        { server_number, sounds_dir: dirs[server_number] ?? "" },
      ),
    onSuccess: (res) => toast.success(`Каталог доступен: звуков — ${res.sound_files}, всего файлов — ${res.total_entries}`),
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Настройки хранилища звуков</DialogTitle>
          <DialogDescription>
            Укажите каталог со звуками на каждом игровом сервере. Подключение (хост, логин,
            пароль) берётся из уже настроенного SFTP этого сервера — здесь оно не дублируется.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {data?.servers.map((s) => (
            <div key={s.server_number} className="space-y-1.5 rounded-md border p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold">{s.server_name}</span>
                {s.sftp_configured
                  ? <Badge variant="secondary">{s.sftp_host}</Badge>
                  : <Badge variant="destructive">SFTP не настроен в .env</Badge>}
              </div>
              <Label className="text-xs text-muted-foreground">Каталог со звуками</Label>
              <div className="flex gap-2">
                <Input
                  value={dirs[s.server_number] ?? ""}
                  onChange={(e) => setDirs((c) => ({ ...c, [s.server_number]: e.target.value }))}
                  placeholder="/LocalMods/EventSounds/"
                  disabled={!s.sftp_configured}
                />
                <Button
                  variant="outline"
                  onClick={() => test.mutate(s.server_number)}
                  disabled={!s.sftp_configured || test.isPending || !(dirs[s.server_number] ?? "").trim()}
                >
                  Проверить
                </Button>
              </div>
            </div>
          ))}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function SoundsPage() {
  const queryClient = useQueryClient()
  const [server, setServer] = useState<string>("")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [renaming, setRenaming] = useState<SoundItem | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const fileInput = useRef<HTMLInputElement>(null)

  const { data: perms } = useQuery({
    queryKey: ["sounds-permissions"],
    queryFn: async () => {
      const res = await api.get<SoundPermissions>("/api/v1/admin/sounds/permissions")
      if (!server && res.servers.length) setServer(res.servers[0])
      return res
    },
  })

  const { data, isLoading } = useQuery({
    queryKey: ["sounds", server],
    queryFn: () => api.get<SoundsResponse>(`/api/v1/admin/sounds?server=${encodeURIComponent(server)}`),
    enabled: !!server,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["sounds", server] })

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append("file", file)
      return api.postForm(`/api/v1/admin/sounds/upload?server=${encodeURIComponent(server)}`, form)
    },
    onSuccess: () => { toast.success("Звук загружен"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const rename = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/sounds/rename?server=${encodeURIComponent(server)}`, {
      filename: renaming?.filename, new_name: renameValue,
    }),
    onSuccess: () => { toast.success("Переименовано"); setRenaming(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (filename: string) =>
      api.delete(`/api/v1/admin/sounds?server=${encodeURIComponent(server)}&filename=${encodeURIComponent(filename)}`),
    onSuccess: (res: unknown) => {
      const r = res as { restorable?: boolean }
      toast.success(r?.restorable
        ? "Удалено с сервера — локальная копия сохранена, звук можно восстановить"
        : "Удалено с сервера (локальной копии не было — восстановить нельзя)")
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const restore = useMutation({
    mutationFn: (filename: string) =>
      api.post(`/api/v1/admin/sounds/restore?server=${encodeURIComponent(server)}`, { filename }),
    onSuccess: () => { toast.success("Звук восстановлен на сервере"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const purge = useMutation({
    mutationFn: (filename: string) =>
      api.delete(`/api/v1/admin/sounds/backup?server=${encodeURIComponent(server)}&filename=${encodeURIComponent(filename)}`),
    onSuccess: () => { toast.success("Локальная копия удалена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const fileUrl = (filename: string) =>
    `/api/v1/admin/sounds/file?server=${encodeURIComponent(server)}&filename=${encodeURIComponent(filename)}`

  return (
    <div>
      <PageHeader
        title="Звуки для ивентов"
        description="Файлы .wav/.mp3 на игровых серверах — их проигрывает плагин во время ивентов"
        icon={Music}
        actions={
          <div className="flex items-center gap-2">
            {perms?.is_superuser && (
              <Button variant="outline" onClick={() => setSettingsOpen(true)}>
                <Settings className="mr-1 size-4" /> Настройки
              </Button>
            )}
            {perms?.can_upload && (
              <>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".wav,.mp3,audio/wav,audio/mpeg"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) upload.mutate(f)
                    e.target.value = ""
                  }}
                />
                <Button onClick={() => fileInput.current?.click()} disabled={!server || upload.isPending}>
                  <Upload className="mr-1 size-4" /> Загрузить звук
                </Button>
              </>
            )}
          </div>
        }
      />

      <div className="mb-4 flex items-center gap-2">
        <Select value={server} onValueChange={setServer}>
          <SelectTrigger className="w-48"><SelectValue placeholder="Сервер" /></SelectTrigger>
          <SelectContent>
            {(perms?.servers ?? []).map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        {perms && <span className="text-xs text-muted-foreground">Максимальный размер файла — {perms.max_size_mb} МБ</span>}
      </div>

      {data?.not_configured && (
        <Card className="mb-4 border-warning/40">
          <CardContent className="flex items-start gap-2 pt-6 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
            <div>
              <div>{data.error}</div>
              {perms?.is_superuser
                ? <div className="mt-1 text-muted-foreground">Откройте «Настройки» и укажите каталог со звуками.</div>
                : <div className="mt-1 text-muted-foreground">Обратитесь к суперпользователю, чтобы он настроил каталог.</div>}
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Файл</TableHead>
                  <TableHead className="w-28">Размер</TableHead>
                  <TableHead className="w-64">Прослушать</TableHead>
                  <TableHead className="w-44">Загрузил</TableHead>
                  <TableHead className="w-40 text-right">Действия</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.files ?? []).map((f) => (
                  <TableRow key={f.filename}>
                    <TableCell className="font-medium">
                      {f.filename}
                      {!f.has_backup && (
                        <Badge variant="outline" className="ml-2 text-warning" title="Локальной копии нет — после удаления восстановить будет нельзя">
                          без копии
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatSize(f.size_bytes)}</TableCell>
                    <TableCell>
                      <audio controls preload="none" src={fileUrl(f.filename)} className="h-8 w-full" />
                    </TableCell>
                    <TableCell className="text-muted-foreground">{f.uploaded_by ?? "-"}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon-sm" asChild title="Скачать">
                          <a href={fileUrl(f.filename)} download={f.filename}><Download className="size-4" /></a>
                        </Button>
                        {perms?.can_rename && (
                          <Button variant="ghost" size="icon-sm" title="Переименовать"
                            onClick={() => { setRenaming(f); setRenameValue(f.filename) }}>
                            <Pencil className="size-4" />
                          </Button>
                        )}
                        {perms?.can_delete && (
                          <Button variant="ghost" size="icon-sm" title="Удалить с сервера"
                            onClick={() => {
                              if (confirm(`Удалить «${f.filename}» с сервера ${server}?`)) remove.mutate(f.filename)
                            }}>
                            <Trash2 className="size-4 text-destructive" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {!data?.files.length && !data?.not_configured && (
                  <TableRow>
                    <TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">
                      Звуков на этом сервере пока нет
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {!!data?.deleted.length && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle className="text-sm">Удалённые с сервера ({data.deleted.length})</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Файл</TableHead>
                  <TableHead className="w-28">Размер</TableHead>
                  <TableHead className="w-44">Удалил</TableHead>
                  <TableHead className="w-52 text-right">Действия</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.deleted.map((f) => (
                  <TableRow key={f.filename}>
                    <TableCell className="font-medium">{f.filename}</TableCell>
                    <TableCell className="text-muted-foreground">{formatSize(f.size_bytes)}</TableCell>
                    <TableCell className="text-muted-foreground">{f.deleted_by ?? "-"}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {perms?.can_restore && (
                          <Button variant="outline" size="sm" onClick={() => restore.mutate(f.filename)}>
                            <RotateCcw className="mr-1 size-3.5" /> Восстановить
                          </Button>
                        )}
                        {perms?.can_delete && (
                          <Button variant="ghost" size="icon-sm" title="Удалить локальную копию навсегда"
                            onClick={() => {
                              if (confirm(`Удалить локальную копию «${f.filename}»? Восстановить звук после этого будет нельзя.`)) purge.mutate(f.filename)
                            }}>
                            <Trash2 className="size-4 text-destructive" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Dialog open={!!renaming} onOpenChange={(o) => { if (!o) setRenaming(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Переименовать звук</DialogTitle>
            <DialogDescription>
              Файл будет переименован на сервере. Расширение должно остаться .wav или .mp3.
            </DialogDescription>
          </DialogHeader>
          <Input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenaming(null)}>Отмена</Button>
            <Button onClick={() => rename.mutate()} disabled={rename.isPending || !renameValue.trim()}>
              Переименовать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  )
}
