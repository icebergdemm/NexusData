import { useState } from "react"
import type { Editor } from "@tiptap/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, X, Reply, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { documentsApi, type DocComment } from "@/pages/documents/api"

export interface DraftComment {
  markId: string
  anchorText: string
}

function initials(name: string | null | undefined) {
  return (name ?? "?").slice(0, 2).toUpperCase()
}

function CommentThread({ documentId, comment, replies, editor, canManage }: {
  documentId: number
  comment: DocComment
  replies: DocComment[]
  editor: Editor | null
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const [replying, setReplying] = useState(false)
  const [replyText, setReplyText] = useState("")
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["document-comments", documentId] })

  const removeMark = (markId: string | null) => {
    if (markId && editor) editor.chain().focus().unsetComment(markId).run()
  }

  const resolveMutation = useMutation({
    mutationFn: (resolved: boolean) => documentsApi.comments.resolve(documentId, comment.id, resolved),
    onSuccess: (_data, resolved) => { if (resolved) removeMark(comment.mark_id); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => documentsApi.comments.delete(documentId, comment.id),
    onSuccess: () => { removeMark(comment.mark_id); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const replyMutation = useMutation({
    mutationFn: () => documentsApi.comments.create(documentId, { body: replyText, parent_id: comment.id }),
    onSuccess: () => { setReplyText(""); setReplying(false); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className={`rounded-lg border p-3 ${comment.resolved ? "opacity-60" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[10px] font-semibold text-primary">
            {initials(comment.author?.name)}
          </div>
          <div>
            <div className="text-sm font-medium leading-none">{comment.author?.name ?? "—"}</div>
            <div className="text-[11px] text-muted-foreground">{comment.created_at ? new Date(comment.created_at).toLocaleString("ru-RU") : ""}</div>
          </div>
        </div>
        {comment.resolved && <Badge variant="secondary" className="text-[10px]">Решено</Badge>}
      </div>
      {comment.anchor_text && (
        <div className="mt-2 border-l-2 border-warning/60 pl-2 text-xs text-muted-foreground italic">
          «{comment.anchor_text.length > 120 ? `${comment.anchor_text.slice(0, 120)}…` : comment.anchor_text}»
        </div>
      )}
      <p className="mt-2 text-sm">{comment.body}</p>

      {replies.map((r) => (
        <div key={r.id} className="mt-2 ml-4 border-l pl-3">
          <div className="flex items-center gap-2">
            <div className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-[9px] font-semibold">
              {initials(r.author?.name)}
            </div>
            <span className="text-xs font-medium">{r.author?.name ?? "—"}</span>
          </div>
          <p className="mt-1 text-sm">{r.body}</p>
        </div>
      ))}

      <div className="mt-2 flex items-center gap-1">
        {canManage && !comment.resolved && (
          <Button size="xs" variant="ghost" onClick={() => resolveMutation.mutate(true)}><Check className="size-3" /> Решить</Button>
        )}
        {canManage && comment.resolved && (
          <Button size="xs" variant="ghost" onClick={() => resolveMutation.mutate(false)}><X className="size-3" /> Вернуть</Button>
        )}
        <Button size="xs" variant="ghost" onClick={() => setReplying((v) => !v)}><Reply className="size-3" /> Ответить</Button>
        {canManage && (
          <Button size="xs" variant="ghost" className="text-destructive" onClick={() => deleteMutation.mutate()}><Trash2 className="size-3" /></Button>
        )}
      </div>

      {replying && (
        <div className="mt-2 flex gap-2">
          <Textarea rows={2} value={replyText} onChange={(e) => setReplyText(e.target.value)} placeholder="Ответить…" className="text-sm" />
          <Button size="sm" disabled={!replyText.trim()} onClick={() => replyMutation.mutate()}>OK</Button>
        </div>
      )}
    </div>
  )
}

export function CommentsSidebar({ documentId, editor, canComment, draft, onDraftConsumed }: {
  documentId: number
  editor: Editor | null
  canComment: boolean
  draft: DraftComment | null
  onDraftConsumed: () => void
}) {
  const queryClient = useQueryClient()
  const [draftText, setDraftText] = useState("")

  const { data } = useQuery({
    queryKey: ["document-comments", documentId],
    queryFn: () => documentsApi.comments.list(documentId),
  })

  const createMutation = useMutation({
    mutationFn: () => documentsApi.comments.create(documentId, {
      body: draftText, anchor_text: draft?.anchorText, mark_id: draft?.markId,
    }),
    onSuccess: () => {
      setDraftText("")
      onDraftConsumed()
      queryClient.invalidateQueries({ queryKey: ["document-comments", documentId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cancelDraft = () => {
    if (draft?.markId && editor) editor.chain().focus().unsetComment(draft.markId).run()
    setDraftText("")
    onDraftConsumed()
  }

  const comments = data?.comments ?? []
  const topLevel = comments.filter((c) => !c.parent_id)
  const repliesFor = (id: number) => comments.filter((c) => c.parent_id === id)

  return (
    <div className="flex h-full w-80 shrink-0 flex-col border-l">
      <div className="border-b px-3 py-2.5 text-sm font-semibold">Комментарии</div>
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-2 p-3">
          {draft && (
            <div className="rounded-lg border border-primary/40 bg-primary/5 p-3">
              {draft.anchorText && (
                <div className="mb-2 border-l-2 border-warning/60 pl-2 text-xs text-muted-foreground italic">«{draft.anchorText}»</div>
              )}
              <Textarea autoFocus rows={3} value={draftText} onChange={(e) => setDraftText(e.target.value)} placeholder="Введите комментарий…" className="text-sm" />
              <div className="mt-2 flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={cancelDraft}>Отмена</Button>
                <Button size="sm" disabled={!draftText.trim()} onClick={() => createMutation.mutate()}>Отправить</Button>
              </div>
            </div>
          )}
          {topLevel.length === 0 && !draft && (
            <div className="py-8 text-center text-sm text-muted-foreground">
              {canComment ? "Выделите текст и нажмите на значок комментария, чтобы оставить заметку." : "Комментариев пока нет."}
            </div>
          )}
          {topLevel.map((c) => (
            <CommentThread key={c.id} documentId={documentId} comment={c} replies={repliesFor(c.id)} editor={editor} canManage={canComment} />
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
