import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Ship, Plus } from "lucide-react"
import { cn } from "@/lib/utils"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { api } from "@/lib/api"
import type { PolygonProjectListResponse } from "@/pages/sub-projects/types"

function ProjectThumbnail({ filename, alt }: { filename: string | null; alt: string }) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)

  if (!filename || failed) {
    return (
      <div className="flex h-32 w-full items-center justify-center bg-muted/40 text-muted-foreground">
        <Ship className="h-8 w-8" />
      </div>
    )
  }

  return (
    <div className="relative h-32 w-full overflow-hidden bg-muted/40">
      {!loaded && <Skeleton className="absolute inset-0 h-full w-full rounded-none" />}
      <img
        src={`/sub_projects/thumbs/${filename}`}
        alt={alt}
        loading="lazy"
        decoding="async"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className={cn("h-32 w-full object-cover transition-opacity duration-300", loaded ? "opacity-100" : "opacity-0")}
      />
    </div>
  )
}

export function SubProjectsPolygonListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState("")
  const [author, setAuthor] = useState("")
  const [departmentId, setDepartmentId] = useState("")
  const [image, setImage] = useState<File | null>(null)

  const queryKey = ["sub-projects-polygon", search]
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams()
      if (search.trim().length >= 2) params.set("search", search.trim())
      return api.get<PolygonProjectListResponse>(`/api/v1/admin/sub-projects-polygon?${params}`)
    },
  })

  const create = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("name", name.trim())
      form.set("author", author.trim())
      form.set("department_id", departmentId)
      if (image) form.set("image", image)
      return api.postForm<{ project: { id: number } }>("/api/v1/admin/sub-projects-polygon", form)
    },
    onSuccess: (res) => {
      toast.success("Проект создан")
      setCreateOpen(false)
      setName(""); setAuthor(""); setDepartmentId(""); setImage(null)
      queryClient.invalidateQueries({ queryKey: ["sub-projects-polygon"] })
      navigate(`/sub-projects-polygon/${res.project.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader
        title="Тестовый полигон ASPER"
        description="Проекты отделов для тестирования — деплой только на Сервер 2 (ASPER2)"
        icon={Ship}
        actions={
          !!data?.creatable_departments.length && (
            <Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Новый проект</Button>
          )
        }
      />

      <div className="mb-4">
        <SearchInput value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по названию..." className="max-w-xs" />
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-48" />)}</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data?.projects.map(({ project, department_name, version_count }) => (
            <Card key={project.id} className="cursor-pointer overflow-hidden py-0 transition-all hover:border-primary/50 hover:shadow-md" onClick={() => navigate(`/sub-projects-polygon/${project.id}`)}>
              <ProjectThumbnail filename={project.image_filename} alt={project.name} />
              <CardContent className="space-y-2 py-4">
                <div className="font-semibold">{project.name}</div>
                <div className="text-xs text-muted-foreground">Автор: {project.author}</div>
                {department_name && <Badge variant="secondary">{department_name}</Badge>}
                <div className="text-xs text-muted-foreground">{version_count} версий</div>
              </CardContent>
            </Card>
          ))}
          {!data?.projects.length && (
            <div className="col-span-full py-12 text-center text-muted-foreground">
              {data?.has_any_access === false
                ? "У вас нет доступа к тестовому серверу ни в одном отделе"
                : "Проектов не найдено"}
            </div>
          )}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Новый проект Полигона</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">Отдел</Label>
              <Select value={departmentId} onValueChange={setDepartmentId}>
                <SelectTrigger><SelectValue placeholder="Выберите отдел" /></SelectTrigger>
                <SelectContent>
                  {data?.creatable_departments.map((d) => (
                    <SelectItem key={d.id} value={String(d.id)}>{d.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="mb-1.5 block">Название</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            </div>
            <div>
              <Label className="mb-1.5 block">Автор</Label>
              <Input value={author} onChange={(e) => setAuthor(e.target.value)} />
            </div>
            <div>
              <Label className="mb-1.5 block">Картинка (необязательно)</Label>
              <Input type="file" accept="image/*" onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Отмена</Button>
            <Button disabled={!name.trim() || !author.trim() || !departmentId || create.isPending} onClick={() => create.mutate()}>Создать</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
