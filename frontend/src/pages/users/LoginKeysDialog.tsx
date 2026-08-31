/** Ключи входа без Discord для конкретного администратора (страница /slogin).
 *
 *  Только для суперпользователя: ключ даёт полноценный вход в чужой аккаунт
 *  со всеми его правами.
 *
 *  Созданный ключ показывается ровно один раз — в базе лежит только хеш, и
 *  восстановить его нельзя. Поэтому свежий ключ держится в состоянии диалога
 *  до его закрытия и сопровождается явным предупреждением: закрыл, не
 *  скопировав — придётся выдавать новый.
 */
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Copy, KeyRound, Ban, AlertTriangle } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"

interface LoginKey {
  id: number
  key_prefix: string
  label: string | null
  created_at: string | null
  created_by: string | null
  expires_at: string | null
  revoked: boolean
  last_used_at: string | null
  last_used_ip: string | null
  use_count: number
}

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

export function LoginKeysDialog({ adminId, adminName, open, onOpenChange }: {
  adminId: number
  adminName: string
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [label, setLabel] = useState("")
  const [expiresDays, setExpiresDays] = useState("")
  const [freshKey, setFreshKey] = useState<string | null>(null)

  const queryKey = ["login-keys", adminId]
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<{ keys: LoginKey[] }>(`/api/v1/admin/users/${adminId}/login-keys`),
    enabled: open,
  })

  const createKey = useMutation({
    mutationFn: () => api.post<{ secret: string }>(`/api/v1/admin/users/${adminId}/login-keys`, {
      label: label.trim() || null,
      expires_days: expiresDays ? Number(expiresDays) : null,
    }),
    onSuccess: (res) => {
      setFreshKey(res.secret)
      setLabel("")
      setExpiresDays("")
      queryClient.invalidateQueries({ queryKey })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const revokeKey = useMutation({
    mutationFn: (keyId: number) => api.delete(`/api/v1/admin/users/${adminId}/login-keys/${keyId}`),
    onSuccess: () => {
      toast.success("Ключ отозван")
      queryClient.invalidateQueries({ queryKey })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      toast.success("Ключ скопирован")
    } catch {
      toast.error("Не удалось скопировать — выделите и скопируйте вручную")
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setFreshKey(null) }}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-1.5">
            <KeyRound className="size-4" /> Ключи входа — {adminName}
          </DialogTitle>
          <DialogDescription>
            Вход без Discord на странице <code className="font-mono">/slogin</code>. Ключ даёт полный доступ
            к этому аккаунту, поэтому выдавайте его лично и отзывайте, как только он перестал быть нужен.
          </DialogDescription>
        </DialogHeader>

        {freshKey && (
          <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/10 p-3">
            <div className="flex items-start gap-2 text-sm text-warning">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <span>
                Скопируйте ключ сейчас — он показывается один раз. В базе хранится только хеш,
                восстановить его будет нельзя.
              </span>
            </div>
            <div className="flex gap-2">
              <Input readOnly value={freshKey} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
              <Button variant="outline" size="icon" onClick={() => copy(freshKey)} title="Скопировать">
                <Copy className="size-4" />
              </Button>
            </div>
          </div>
        )}

        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto] sm:items-end">
          <div>
            <Label className="mb-1 block text-xs">Пометка (для чего выдан)</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Резервный доступ" />
          </div>
          <div>
            <Label className="mb-1 block text-xs">Срок, дней</Label>
            <Input
              type="number" min={1}
              value={expiresDays}
              onChange={(e) => setExpiresDays(e.target.value)}
              placeholder="бессрочно"
              className="w-32"
            />
          </div>
          <Button onClick={() => createKey.mutate()} disabled={createKey.isPending}>
            Создать ключ
          </Button>
        </div>

        <div className="space-y-1">
          {isLoading && Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-12" />)}
          {!isLoading && (data?.keys ?? []).map((k) => (
            <div
              key={k.id}
              className={`flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm ${
                k.revoked ? "opacity-55" : ""
              }`}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-xs">{k.key_prefix}…</span>
                  {k.label && <span className="truncate">{k.label}</span>}
                  {k.revoked && <Badge variant="outline">отозван</Badge>}
                  {!k.revoked && k.expires_at && new Date(k.expires_at) < new Date() && (
                    <Badge variant="outline" className="border-warning/50 text-warning">просрочен</Badge>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  создан {fmtDate(k.created_at)}
                  {k.created_by && ` · ${k.created_by}`}
                  {k.expires_at && ` · до ${fmtDate(k.expires_at)}`}
                  {k.use_count > 0
                    ? ` · использован ${k.use_count} раз, последний ${fmtDate(k.last_used_at)}${k.last_used_ip ? ` с ${k.last_used_ip}` : ""}`
                    : " · ни разу не использован"}
                </div>
              </div>
              {!k.revoked && (
                <Button
                  variant="ghost" size="icon-sm" title="Отозвать"
                  onClick={() => revokeKey.mutate(k.id)}
                >
                  <Ban className="size-4 text-destructive" />
                </Button>
              )}
            </div>
          ))}
          {!isLoading && !data?.keys.length && (
            <p className="rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
              Ключей нет — этот администратор входит только через Discord
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Закрыть</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
