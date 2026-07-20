import { useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogCancel,
} from "@/components/ui/alert-dialog"
import { api } from "@/lib/api"
import type { SubProjectDetailResponse } from "./types"

const FIELDS: { key: string; label: string }[] = [
  { key: "name", label: "Название" },
  { key: "author", label: "Автор" },
  { key: "project_type", label: "Тип (Корабль/Станция...)" },
  { key: "crew", label: "Экипаж (напр. 7-10-12)" },
  { key: "weapons", label: "Вооружение" },
  { key: "has_reactor", label: "Реактор (Да/Нет)" },
  { key: "fabricator_medfab", label: "Фабрикатор/Медфаб" },
  { key: "has_deconstructor", label: "Деконструктор (Да/Нет)" },
  { key: "docking_ports", label: "Стыковочные порты" },
  { key: "decks", label: "Палубы" },
  { key: "max_speed", label: "Макс. скорость" },
  { key: "extra_equipment", label: "Доп. оборудование" },
]

export function SubProjectFormPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const isEdit = !!projectId
  const navigate = useNavigate()
  const [fields, setFields] = useState<Record<string, string>>({})
  const [features, setFeatures] = useState("")
  const [description, setDescription] = useState("")
  const [image, setImage] = useState<File | null>(null)
  const [dirty, setDirty] = useState(false)
  const [confirmClose, setConfirmClose] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ["sub-project-detail", projectId],
    queryFn: () => api.get<SubProjectDetailResponse>(`/api/v1/admin/sub-projects/${projectId}`),
    enabled: isEdit,
  })

  useEffect(() => {
    if (data?.project) {
      const p = data.project as unknown as Record<string, string>
      setFields(Object.fromEntries(FIELDS.map((f) => [f.key, p[f.key] ?? ""])))
      setFeatures(data.project.features ?? "")
      setDescription(data.project.description ?? "")
      setDirty(false)
    }
  }, [data])

  const closeTo = () => navigate(isEdit ? `/sub-projects/${projectId}` : "/sub-projects")

  const markDirty = () => setDirty(true)

  const saveMutation = useMutation({
    mutationFn: () => {
      const form = new FormData()
      for (const f of FIELDS) form.set(f.key, fields[f.key] ?? "")
      form.set("features", features)
      form.set("description", description)
      if (image) form.set("image", image)
      return isEdit
        ? api.postForm<{ project: { id: number } }>(`/api/v1/admin/sub-projects/${projectId}`, form)
        : api.postForm<{ project: { id: number } }>("/api/v1/admin/sub-projects", form)
    },
    onSuccess: (r) => {
      toast.success(isEdit ? "Проект обновлён" : "Проект создан")
      navigate(`/sub-projects/${r.project.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const requestClose = () => {
    if (dirty) setConfirmClose(true)
    else closeTo()
  }

  return (
    <Dialog open onOpenChange={(o) => !o && requestClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Редактировать проект" : "Новый проект"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "Измените характеристики проекта и при необходимости замените изображение." : "Заполните характеристики нового проекта подлодки/станции."}
          </DialogDescription>
        </DialogHeader>

        {isEdit && isLoading ? (
          <Skeleton className="h-96" />
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              {FIELDS.map((f) => (
                <div key={f.key} className={f.key === "name" || f.key === "author" ? "col-span-2" : ""}>
                  <Label className="mb-1.5 block">{f.label}</Label>
                  <Input value={fields[f.key] ?? ""} onChange={(e) => { setFields((s) => ({ ...s, [f.key]: e.target.value })); markDirty() }} />
                </div>
              ))}
            </div>
            <div>
              <Label className="mb-1.5 block">Особенности</Label>
              <Textarea value={features} onChange={(e) => { setFeatures(e.target.value); markDirty() }} />
            </div>
            <div>
              <Label className="mb-1.5 block">Описание</Label>
              <Textarea value={description} onChange={(e) => { setDescription(e.target.value); markDirty() }} />
            </div>
            <div>
              <Label className="mb-1.5 block">Изображение</Label>
              <Input type="file" accept="image/*" onChange={(e) => { setImage(e.target.files?.[0] ?? null); markDirty() }} />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={requestClose}>Отмена</Button>
          <Button
            disabled={!fields.name?.trim() || !fields.author?.trim() || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {isEdit ? "Сохранить" : "Создать"}
          </Button>
        </DialogFooter>
      </DialogContent>

      <AlertDialog open={confirmClose} onOpenChange={setConfirmClose}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Сохранить изменения?</AlertDialogTitle>
            <AlertDialogDescription>В форме есть несохранённые изменения. Сохранить их перед закрытием?</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <Button variant="outline" onClick={() => { setConfirmClose(false); closeTo() }}>Не сохранять</Button>
            <Button onClick={() => { setConfirmClose(false); saveMutation.mutate() }}>Сохранить</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}
