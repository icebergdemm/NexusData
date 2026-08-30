import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Scroll, Plus, Upload } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"
import { documentsApi, type DocTemplate } from "@/pages/documents/api"
import { ImportDocumentDialog } from "@/pages/documents/ImportDocumentDialog"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import type { DepartmentDetail } from "./api"

export function OrdersTab({ department, canEdit }: { department: DepartmentDetail; canEdit: boolean }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [importOpen, setImportOpen] = useState(false)
  const canView = department.my_access.orders?.view

  const { data, isLoading } = useQuery({
    queryKey: ["department-orders", department.id],
    queryFn: () => documentsApi.byDepartment(department.id, { kind: "orders" }),
    enabled: canView,
  })

  const { data: templatesData } = useQuery({
    queryKey: ["order-templates"],
    queryFn: () => documentsApi.templates.list(),
    enabled: canEdit,
  })
  const orderTemplates = templatesData?.templates.filter((t) => t.is_order_template) ?? []

  const createMutation = useMutation({
    mutationFn: (template?: DocTemplate) =>
      documentsApi.create({ title: template ? undefined : "Новый приказ", department_id: department.id, template_id: template?.id, is_order_template: !template ? true : undefined }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["department-orders", department.id] })
      navigate(`/documents/${res.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!canView) {
    return <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Недостаточно прав для просмотра приказов отдела.</CardContent></Card>
  }

  return (
    <div className="space-y-3">
      {canEdit && (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm"><Plus className="size-4" /> Новый приказ</Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => createMutation.mutate(undefined)}>Пустой документ</DropdownMenuItem>
              {orderTemplates.map((t) => (
                <DropdownMenuItem key={t.id} onClick={() => createMutation.mutate(t)}>
                  <PlatformIcon value={t.icon} fallback={Scroll} /> {t.name}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setImportOpen(true)}><Upload className="mr-1 size-4" /> Импорт .docx/.pdf</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}
      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : !data?.documents.length ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground">
            <Scroll className="size-6 opacity-40" />
            Приказов пока нет.
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-2">
          {data.documents.map((d) => (
            <Card
              key={d.id}
              className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/50"
              onClick={() => navigate(`/documents/${d.id}`)}
            >
              <CardContent className="flex items-center gap-3 p-3">
                <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-lg">
                  <PlatformIcon value={d.icon} fallback={Scroll} className="size-4 text-primary" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{d.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {d.last_edited_by?.name ? `Изменил ${d.last_edited_by.name}` : "Ещё не редактировался"}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <ImportDocumentDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        departmentId={department.id}
        isOrderTemplate
        onImported={(id) => { queryClient.invalidateQueries({ queryKey: ["department-orders", department.id] }); navigate(`/documents/${id}`) }}
      />
    </div>
  )
}
