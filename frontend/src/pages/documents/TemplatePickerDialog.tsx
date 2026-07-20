import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FileText, Trash2, Upload } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { documentsApi } from "@/pages/documents/api"
import { ImportDocumentDialog } from "./ImportDocumentDialog"

export function TemplatePickerDialog({ open, onOpenChange, onPick, onImported, canManage, folderId, departmentId, departmentFolderId }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onPick: (templateId?: number) => void
  onImported?: (documentId: number) => void
  canManage: boolean
  folderId?: number
  departmentId?: number
  departmentFolderId?: number
}) {
  const queryClient = useQueryClient()
  const [importOpen, setImportOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ["document-templates"],
    queryFn: () => documentsApi.templates.list(),
    enabled: open,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => documentsApi.templates.delete(id),
    onSuccess: () => { toast.success("Шаблон удалён"); queryClient.invalidateQueries({ queryKey: ["document-templates"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const templates = data?.templates ?? []

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Создать документ</DialogTitle>
          <DialogDescription>Выберите шаблон — документ откроется предзаполненным и готовым к редактированию.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <button
            onClick={() => onPick(undefined)}
            className="group flex flex-col items-center gap-2 rounded-lg border border-dashed p-4 text-center transition-colors hover:border-primary/50 hover:bg-accent"
          >
            <div className="flex size-12 items-center justify-center rounded-lg bg-muted text-muted-foreground">
              <FileText className="size-6" />
            </div>
            <div>
              <div className="text-sm font-medium">Пустой документ</div>
              <div className="text-xs text-muted-foreground">Начать с чистого листа</div>
            </div>
          </button>

          {templates.map((t) => (
            <div key={t.id} className="group relative flex flex-col items-center gap-2 rounded-lg border p-4 text-center transition-colors hover:border-primary/50 hover:bg-accent">
              <button onClick={() => onPick(t.id)} className="flex flex-col items-center gap-2">
                <div className="flex size-12 items-center justify-center rounded-lg bg-primary/10 text-xl">
                  {t.icon || <FileText className="size-6 text-primary" />}
                </div>
                <div>
                  <div className="text-sm font-medium">{t.name}</div>
                  {t.description && <div className="text-xs text-muted-foreground">{t.description}</div>}
                </div>
              </button>
              {canManage && (
                <Button
                  size="icon-xs"
                  variant="ghost"
                  className="absolute top-1.5 right-1.5 text-destructive opacity-0 group-hover:opacity-100"
                  onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(t.id) }}
                  title="Удалить шаблон"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              )}
            </div>
          ))}

          {onImported && (
            <button
              onClick={() => setImportOpen(true)}
              className="group flex flex-col items-center gap-2 rounded-lg border border-dashed p-4 text-center transition-colors hover:border-primary/50 hover:bg-accent"
            >
              <div className="flex size-12 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                <Upload className="size-6" />
              </div>
              <div>
                <div className="text-sm font-medium">Загрузить файл</div>
                <div className="text-xs text-muted-foreground">.docx или .xlsx</div>
              </div>
            </button>
          )}
        </div>
        {!templates.length && (
          <p className="text-center text-xs text-muted-foreground">
            Пока нет сохранённых шаблонов — их можно создать из меню «…» открытого документа («Сохранить как шаблон»).
          </p>
        )}
      </DialogContent>
    </Dialog>
    {onImported && (
      <ImportDocumentDialog open={importOpen} onOpenChange={setImportOpen} onImported={onImported} folderId={folderId} departmentId={departmentId} departmentFolderId={departmentFolderId} />
    )}
    </>
  )
}
