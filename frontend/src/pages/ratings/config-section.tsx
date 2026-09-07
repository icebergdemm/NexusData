import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FlaskConical, Save, Settings2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { getRatingProfiles, prepareRatingConfigPayload, type RatingMode } from "./rating-v2-contract"

type UnknownRecord = Record<string, unknown>

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {}
}

function configuredProfile(value: unknown): string {
  const config = asRecord(value)
  const nested = asRecord(config.config)
  const selected = config.active_profile ?? config.profile ?? nested.active_profile ?? nested.profile
  return typeof selected === "string" ? selected : ""
}

export function RatingConfigSection({ mode }: { mode: RatingMode }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState("{}")
  const [profile, setProfile] = useState("")
  const [preview, setPreview] = useState<unknown>(null)
  const { data, isLoading, isError } = useQuery({
    queryKey: ["rating-v2-config", mode],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-config/${mode}`),
  })

  useEffect(() => {
    if (data !== undefined) {
      setDraft(JSON.stringify(data, null, 2))
      setProfile(configuredProfile(data))
    }
  }, [data])

  const parseDraft = (): UnknownRecord | null => {
    try {
      const parsed: unknown = JSON.parse(draft)
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not object")
      return prepareRatingConfigPayload(parsed, profile)
    } catch {
      toast.error("Расширенная конфигурация должна быть JSON-объектом")
      return null
    }
  }

  const save = useMutation({
    mutationFn: (payload: UnknownRecord) => api.put<unknown>(`/api/v2/admin/rating-config/${mode}`, payload),
    onSuccess: (result) => {
      toast.success("Настройки рейтинга сохранены")
      setDraft(JSON.stringify(result, null, 2))
      queryClient.invalidateQueries({ queryKey: ["rating-v2-config", mode] })
      queryClient.invalidateQueries({ queryKey: ["ratings-v2"] })
    },
    onError: (error: unknown) => toast.error(error instanceof Error ? error.message : "Не удалось сохранить настройки"),
  })
  const previewMutation = useMutation({
    mutationFn: (payload: UnknownRecord) => api.post<unknown>(`/api/v2/admin/rating-config/${mode}/preview`, payload),
    onSuccess: setPreview,
    onError: (error: unknown) => toast.error(error instanceof Error ? error.message : "Не удалось построить предпросмотр"),
  })

  if (isLoading) return <Card className="mb-4"><CardContent className="p-4 text-sm text-muted-foreground">Загрузка конфигурации…</CardContent></Card>
  if (isError) return <Card className="mb-4"><CardContent className="p-4 text-sm text-muted-foreground">Конфигурация недоступна для этой учётной записи.</CardContent></Card>

  const profiles = getRatingProfiles(data)
  return <Card className="mb-4">
    <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 border-b py-3">
      <CardTitle className="flex items-center gap-2 text-sm"><Settings2 className="size-4" /> Настройки рейтинга: {mode === "pvp" ? "PvP" : "Mission"}</CardTitle>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={previewMutation.isPending} onClick={() => { const payload = parseDraft(); if (payload) previewMutation.mutate(payload) }}><FlaskConical className="size-4" /> Предпросмотр</Button>
        <Button size="sm" disabled={save.isPending} onClick={() => { const payload = parseDraft(); if (payload) save.mutate(payload) }}><Save className="size-4" /> Сохранить</Button>
      </div>
    </CardHeader>
    <CardContent className="space-y-4 pt-4">
      {profiles.length ? <div className="max-w-md space-y-1.5">
        <Label>Профиль</Label>
        <Select value={profile || profiles[0].id} onValueChange={setProfile}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>{profiles.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">Обычная настройка: выбирает профиль, не подменяя остальные серверные поля.</p>
      </div> : <p className="text-sm text-muted-foreground">Сервер не вернул список профилей; доступна расширенная настройка.</p>}

      {preview !== null && <div className="rounded-md border bg-muted/30 p-3"><p className="mb-1 text-sm font-medium">Предпросмотр</p><pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(preview, null, 2)}</pre></div>}

      <details className="rounded-md border p-3">
        <summary className="cursor-pointer text-sm font-medium">Расширенная конфигурация</summary>
        <p className="mt-2 text-xs text-muted-foreground">Прямой JSON для редких параметров. Здесь нет магии и нет защиты от плохой конфигурации — сервер валидирует окончательное слово.</p>
        <Textarea className="mt-3 min-h-72 font-mono text-xs" value={draft} onChange={(event) => setDraft(event.target.value)} spellCheck={false} />
      </details>
    </CardContent>
  </Card>
}
