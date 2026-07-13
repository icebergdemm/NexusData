import { useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
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
    }
  }, [data])

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

  if (isEdit && isLoading) return <Skeleton className="h-96" />

  return (
    <Card className="mx-auto max-w-2xl">
      <CardHeader><CardTitle>{isEdit ? "Редактировать проект" : "Новый проект"}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          {FIELDS.map((f) => (
            <div key={f.key} className={f.key === "name" || f.key === "author" ? "col-span-2" : ""}>
              <Label>{f.label}</Label>
              <Input value={fields[f.key] ?? ""} onChange={(e) => setFields((s) => ({ ...s, [f.key]: e.target.value }))} />
            </div>
          ))}
        </div>
        <div>
          <Label>Особенности</Label>
          <Textarea value={features} onChange={(e) => setFeatures(e.target.value)} />
        </div>
        <div>
          <Label>Описание</Label>
          <Textarea value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <Label>Изображение</Label>
          <Input type="file" accept="image/*" onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
        </div>
        <Button
          disabled={!fields.name?.trim() || !fields.author?.trim() || saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {isEdit ? "Сохранить" : "Создать"}
        </Button>
      </CardContent>
    </Card>
  )
}
