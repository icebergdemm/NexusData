import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Ship, FolderTree, Plus, Users } from "lucide-react"
import { cn } from "@/lib/utils"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { api } from "@/lib/api"
import type { SubProjectsListResponse } from "./types"
import { SubProjectsTestServerPanel } from "./TestServerPanel"

/** Lazy-loaded thumbnail with a skeleton while decoding and a Ship-icon
 * fallback for missing images or ones that fail to load. */
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

export function SubProjectsListPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState("")
  const [categoryId, setCategoryId] = useState("all")

  const { data, isLoading } = useQuery({
    queryKey: ["sub-projects", search, categoryId],
    queryFn: () => {
      const params = new URLSearchParams()
      if (search.trim().length >= 2) params.set("search", search.trim())
      if (categoryId !== "all") params.set("category_id", categoryId)
      return api.get<SubProjectsListResponse>(`/api/v1/admin/sub-projects?${params}`)
    },
  })

  return (
    <div>
      <PageHeader
        title="Sub-карты"
        description="Проекты подводных лодок и станций"
        icon={Ship}
        actions={<>
          {data?.can_manage_categories && <Button variant="outline" onClick={() => navigate("/sub-projects/categories")}><FolderTree className="h-4 w-4" /> Категории</Button>}
          {data?.can_create && <Button onClick={() => navigate("/sub-projects/new")}><Plus className="h-4 w-4" /> Новый проект</Button>}
        </>}
      />

      {data?.can_manage_test_server && <div className="mb-4"><SubProjectsTestServerPanel /></div>}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по названию/автору..." className="max-w-xs" />
        <Select value={categoryId} onValueChange={setCategoryId}>
          <SelectTrigger className="w-56"><SelectValue placeholder="Все категории" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все категории</SelectItem>
            {data?.all_categories.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.icon} {c.name}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-48" />)}</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data?.projects.map(({ project, version_count, categories }) => (
            <Card key={project.id} className="cursor-pointer overflow-hidden py-0 transition-all hover:border-primary/50 hover:shadow-md" onClick={() => navigate(`/sub-projects/${project.id}`)}>
              <ProjectThumbnail filename={project.image_filename} alt={project.name} />
              <CardContent className="space-y-2 py-4">
                <div className="font-semibold">{project.name}</div>
                <div className="text-xs text-muted-foreground">Автор: {project.author} · {project.project_type ?? "-"}</div>
                <div className="flex flex-wrap gap-1">
                  {categories.map((c) => <Badge key={c.id} style={{ backgroundColor: c.color }} className="text-white">{c.icon} {c.name}</Badge>)}
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span>{version_count} версий</span>
                  {project.crew && <span className="flex items-center gap-1"><Users className="h-3 w-3" /> {project.crew}</span>}
                </div>
              </CardContent>
            </Card>
          ))}
          {!data?.projects.length && <div className="col-span-full py-12 text-center text-muted-foreground">Проектов не найдено</div>}
        </div>
      )}
    </div>
  )
}
