import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Pencil, Check, X } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { PlatformIcon, PlatformIconField } from "@/components/layout/PlatformIcon"

interface LabelCategory { id: number; name: string; color: string; icon: string }

export function LabelCategoriesPage() {
  const [form, setForm] = useState({ name: "", color: "#888888", icon: "🏷" })
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState({ name: "", color: "#888888", icon: "🏷" })
  const queryClient = useQueryClient()

  const { data } = useQuery({
    queryKey: ["label-categories"],
    queryFn: () => api.get<{ categories: LabelCategory[] }>("/api/v1/admin/label-categories"),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["label-categories"] })

  const create = useMutation({
    mutationFn: () => api.post("/api/v1/admin/label-categories", form),
    onSuccess: () => { toast.success("Категория создана"); setForm({ name: "", color: "#888888", icon: "🏷" }); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/label-categories/${id}`),
    onSuccess: () => { toast.success("Категория удалена"); invalidate() },
  })

  const update = useMutation({
    mutationFn: ({ id, ...body }: { id: number; name: string; color: string; icon: string }) =>
      api.post(`/api/v1/admin/label-categories/${id}`, body),
    onSuccess: () => { toast.success("Метка обновлена"); setEditingId(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const startEdit = (c: LabelCategory) => {
    setEditingId(c.id)
    setEditDraft({ name: c.name, color: c.color, icon: c.icon })
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>Новая метка</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap items-end gap-2">
          <div><label className="mb-1 block text-xs text-muted-foreground">Название</label><Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} /></div>
          <div><label className="mb-1 block text-xs text-muted-foreground">Иконка</label><PlatformIconField value={form.icon} onChange={(icon) => setForm((f) => ({ ...f, icon }))} className="w-16" v2ClassName="w-44" /></div>
          <div><label className="mb-1 block text-xs text-muted-foreground">Цвет</label><Input type="color" value={form.color} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="w-16 p-1" /></div>
          <Button disabled={!form.name.trim()} onClick={() => create.mutate()}>Создать</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Метки игроков</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Метка</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {data?.categories.map((c) => (
                <TableRow key={c.id}>
                  {editingId === c.id ? (
                    <>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-2">
                          <PlatformIconField value={editDraft.icon} onChange={(icon) => setEditDraft((d) => ({ ...d, icon }))} className="w-16" v2ClassName="w-44" />
                          <Input
                            value={editDraft.name}
                            onChange={(e) => setEditDraft((d) => ({ ...d, name: e.target.value }))}
                            className="w-40"
                          />
                          <Input
                            type="color"
                            value={editDraft.color}
                            onChange={(e) => setEditDraft((d) => ({ ...d, color: e.target.value }))}
                            className="w-16 p-1"
                          />
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1.5">
                          <Button
                            size="sm"
                            disabled={!editDraft.name.trim() || update.isPending}
                            onClick={() => update.mutate({ id: c.id, ...editDraft })}
                          >
                            <Check className="size-3.5" /> Сохранить
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>
                            <X className="size-3.5" /> Отмена
                          </Button>
                        </div>
                      </TableCell>
                    </>
                  ) : (
                    <>
                      <TableCell><Badge style={{ backgroundColor: c.color }} className="gap-1.5 text-white"><PlatformIcon value={c.icon} />{c.name}</Badge></TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1.5">
                          <Button size="sm" variant="outline" onClick={() => startEdit(c)}>
                            <Pencil className="size-3.5" /> Изменить
                          </Button>
                          <Button size="sm" variant="destructive" onClick={() => remove.mutate(c.id)}>Удалить</Button>
                        </div>
                      </TableCell>
                    </>
                  )}
                </TableRow>
              ))}
              {!data?.categories.length && <TableRow><TableCell colSpan={2} className="text-center text-muted-foreground">Меток нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
