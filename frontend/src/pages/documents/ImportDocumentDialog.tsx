import { useEffect, useRef, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { Upload, FileText, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { documentsApi } from "./api"

export function ImportDocumentDialog({ open, onOpenChange, onImported, departmentId, folderId, departmentFolderId, parentDocumentId, isOrderTemplate, isNews, initialFile }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: (documentId: number) => void
  departmentId?: number
  folderId?: number
  departmentFolderId?: number
  parentDocumentId?: number
  isOrderTemplate?: boolean
  isNews?: boolean
  // Pre-selects a file dropped on the page (drag-and-drop) so the dialog
  // opens straight to the confirm step instead of the file picker.
  initialFile?: File | null
}) {
  const [file, setFile] = useState<File | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open && initialFile) setFile(initialFile)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialFile])

  const importMutation = useMutation({
    mutationFn: () => documentsApi.import(file!, {
      department_id: departmentId, folder_id: folderId, department_folder_id: departmentFolderId, parent_document_id: parentDocumentId, is_order_template: isOrderTemplate, is_news: isNews,
    }),
    onSuccess: (res) => {
      toast.success("Документ загружен")
      onOpenChange(false)
      setFile(null)
      onImported(res.id)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setFile(null) }}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Загрузить документ</DialogTitle>
          <DialogDescription>
            Поддерживаются файлы .docx, .xlsx, .pdf, а также изображения (.jpg, .png, .gif, .webp) — файл сохраняется как есть; word/cell/pdf открываются в редакторе ONLYOFFICE, изображения — во встроенном просмотрщике.
          </DialogDescription>
        </DialogHeader>

        <div
          className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center hover:border-primary/50 hover:bg-accent"
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".docx,.xlsx,.pdf,.jpg,.jpeg,.png,.gif,.webp"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <>
              <FileText className="size-6 text-primary" />
              <span className="text-sm font-medium">{file.name}</span>
              <span className="text-xs text-muted-foreground">{(file.size / 1024 / 1024).toFixed(2)} МБ</span>
            </>
          ) : (
            <>
              <Upload className="size-6 text-muted-foreground" />
              <span className="text-sm text-muted-foreground">Нажмите, чтобы выбрать файл</span>
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!file || importMutation.isPending} onClick={() => importMutation.mutate()} className="gap-1.5">
            {importMutation.isPending && <Loader2 className="size-4 animate-spin" />}
            Импортировать
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
