import { useState } from "react"
import { formatBrowserDate } from "@/lib/format-date"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { History } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { documentsApi } from "@/pages/documents/api"

export function VersionHistoryDialog({ documentId, open, onOpenChange, onRestored }: {
  documentId: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onRestored: () => void
}) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data } = useQuery({
    queryKey: ["document-revisions", documentId],
    queryFn: () => documentsApi.revisions.list(documentId),
    enabled: open,
  })

  const restore = useMutation({
    mutationFn: (revisionId: number) => documentsApi.revisions.restore(documentId, revisionId),
    onSuccess: () => {
      toast.success("Версия восстановлена")
      queryClient.invalidateQueries({ queryKey: ["document", documentId] })
      onRestored()
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const revisions = data?.revisions ?? []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] max-w-md flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><History className="size-4" /> История версий</DialogTitle>
          <DialogDescription>Версии сохраняются автоматически при каждом сохранении в редакторе. Восстановление создаёт новую версию поверх текущей — сам файл открывается для просмотра, только когда он текущий.</DialogDescription>
        </DialogHeader>

        <ScrollArea className="h-64 rounded-md border">
          <div className="flex flex-col p-1">
            {revisions.length === 0 && <div className="p-3 text-center text-xs text-muted-foreground">Пока нет сохранённых версий</div>}
            {revisions.map((r) => (
              <button
                key={r.id}
                onClick={() => setSelectedId(r.id)}
                className={`rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent ${selectedId === r.id ? "bg-accent" : ""}`}
              >
                <div className="font-medium">{r.created_at ? formatBrowserDate(r.created_at) : "-"}</div>
                <div className="text-xs text-muted-foreground">{r.saved_by?.name ?? "Автосохранение"} · v{r.version}</div>
              </button>
            ))}
          </div>
        </ScrollArea>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Закрыть</Button>
          <Button disabled={selectedId == null || restore.isPending} onClick={() => selectedId != null && restore.mutate(selectedId)}>
            Восстановить эту версию
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}