import { useState } from "react"
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

  const { data: preview } = useQuery({
    queryKey: ["document-revision", documentId, selectedId],
    queryFn: () => documentsApi.revisions.get(documentId, selectedId!),
    enabled: open && selectedId != null,
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
      <DialogContent className="flex max-h-[80vh] max-w-3xl flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><History className="size-4" /> История версий</DialogTitle>
          <DialogDescription>Контрольные точки сохраняются автоматически. Восстановление создаёт новую версию поверх текущей.</DialogDescription>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-cols-[220px_1fr] gap-4">
          <ScrollArea className="h-full rounded-md border">
            <div className="flex flex-col p-1">
              {revisions.length === 0 && <div className="p-3 text-center text-xs text-muted-foreground">Пока нет сохранённых версий</div>}
              {revisions.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setSelectedId(r.id)}
                  className={`rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent ${selectedId === r.id ? "bg-accent" : ""}`}
                >
                  <div className="font-medium">{r.created_at ? new Date(r.created_at).toLocaleString("ru-RU") : "—"}</div>
                  <div className="text-xs text-muted-foreground">{r.saved_by?.name ?? "—"} · v{r.version}</div>
                </button>
              ))}
            </div>
          </ScrollArea>

          <ScrollArea className="h-full rounded-md border p-4">
            {selectedId == null ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Выберите версию слева</div>
            ) : preview ? (
              <div className="prose-sm">
                <div className="mb-2 text-sm font-semibold">{preview.title}</div>
                <RevisionPreview content={preview.content} />
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">Загрузка…</div>
            )}
          </ScrollArea>
        </div>

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

/** Minimal read-only text render of a ProseMirror doc, just for the version preview pane. */
function RevisionPreview({ content }: { content: unknown }) {
  const text = extractText(content)
  return <p className="whitespace-pre-wrap text-sm text-foreground">{text || "(пусто)"}</p>
}

function extractText(node: unknown): string {
  if (!node || typeof node !== "object") return ""
  const n = node as { type?: string; text?: string; content?: unknown[] }
  const parts: string[] = []
  if (n.type === "text" && n.text) parts.push(n.text)
  if (Array.isArray(n.content)) {
    for (const child of n.content) parts.push(extractText(child))
  }
  const joined = parts.join(n.type === "paragraph" || n.type === "heading" ? " " : "\n")
  return n.type === "paragraph" || n.type === "heading" ? `${joined}\n` : joined
}
