import { useEffect, useRef, useState } from "react"
import { Navigate, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEditor, EditorContent, type JSONContent } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import { TextStyle } from "@tiptap/extension-text-style"
import Color from "@tiptap/extension-color"
import FontFamily from "@tiptap/extension-font-family"
import Highlight from "@tiptap/extension-highlight"
import TextAlign from "@tiptap/extension-text-align"
import TaskList from "@tiptap/extension-task-list"
import TaskItem from "@tiptap/extension-task-item"
import { Table } from "@tiptap/extension-table"
import TableRow from "@tiptap/extension-table-row"
import TableCell from "@tiptap/extension-table-cell"
import TableHeader from "@tiptap/extension-table-header"
import Superscript from "@tiptap/extension-superscript"
import Subscript from "@tiptap/extension-subscript"
import Placeholder from "@tiptap/extension-placeholder"
import CharacterCount from "@tiptap/extension-character-count"
import Image from "@tiptap/extension-image"
import CodeBlockLowlight from "@tiptap/extension-code-block-lowlight"
import { common, createLowlight } from "lowlight"
import { toast } from "sonner"
import {
  ArrowLeft, Share2, History as HistoryIcon, MessageSquare, Star, MoreHorizontal,
  Copy, Trash2, Loader2, Check, CloudOff, Smile,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ApiError } from "@/lib/api"
import { documentsApi, canEditDocument, canCommentDocument } from "@/pages/documents/api"
import { EditorToolbar } from "@/pages/documents/Toolbar"
import { CommentsSidebar, type DraftComment } from "@/pages/documents/CommentsSidebar"
import { ShareDialog } from "@/pages/documents/ShareDialog"
import { VersionHistoryDialog } from "@/pages/documents/VersionHistoryDialog"
import { FontSize } from "@/pages/documents/extensions/font-size"
import { CommentMark } from "@/pages/documents/extensions/comment-mark"
import "./editor.css"

const lowlight = createLowlight(common)
const EMOJI_CHOICES = ["📄", "📝", "📋", "📌", "📖", "🗂️", "💡", "⚙️", "🚀", "✅", "📊", "🔒", "🎯", "📢", "🧭"]

type SaveStatus = "idle" | "pending" | "saving" | "saved" | "error" | "conflict"

function SaveStatusIndicator({ status }: { status: SaveStatus }) {
  if (status === "saving" || status === "pending") {
    return <span className="flex items-center gap-1 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" /> Сохранение…</span>
  }
  if (status === "saved") {
    return <span className="flex items-center gap-1 text-xs text-muted-foreground"><Check className="size-3 text-success" /> Сохранено</span>
  }
  if (status === "error") {
    return <span className="flex items-center gap-1 text-xs text-destructive"><CloudOff className="size-3" /> Ошибка сохранения</span>
  }
  if (status === "conflict") {
    return <span className="flex items-center gap-1 text-xs text-destructive"><CloudOff className="size-3" /> Конфликт версий</span>
  }
  return null
}

function DocumentEditorInner({ documentId }: { documentId: number }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => documentsApi.get(documentId),
  })

  const [title, setTitle] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle")
  const [shareOpen, setShareOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [commentsOpen, setCommentsOpen] = useState(false)
  const [draftComment, setDraftComment] = useState<DraftComment | null>(null)
  const [isStarred, setIsStarred] = useState(false)

  const versionRef = useRef(1)
  const pendingRef = useRef<{ title?: string; icon?: string | null; content?: JSONContent }>({})
  const timerRef = useRef<number | undefined>(undefined)
  const loadedRef = useRef(false)

  const accessLevel = data?.access_level
  const editable = canEditDocument(accessLevel)
  const commentable = canCommentDocument(accessLevel)

  const saveMutation = useMutation({
    mutationFn: (payload: { title?: string; icon?: string | null; content?: JSONContent }) =>
      documentsApi.update(documentId, { ...payload, base_version: versionRef.current }),
    onSuccess: (res) => {
      versionRef.current = res.version
      setSaveStatus("saved")
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        setSaveStatus("conflict")
        toast.error("Документ изменён в другом месте", {
          description: "Обновите страницу, чтобы получить актуальную версию.",
          action: { label: "Обновить", onClick: () => queryClient.invalidateQueries({ queryKey: ["document", documentId] }) },
          duration: 15000,
        })
      } else {
        setSaveStatus("error")
        toast.error("Не удалось сохранить изменения")
      }
    },
  })

  function flush() {
    if (Object.keys(pendingRef.current).length === 0) return
    const payload = pendingRef.current
    pendingRef.current = {}
    setSaveStatus("saving")
    saveMutation.mutate(payload)
  }

  function scheduleSave(patch: { title?: string; icon?: string | null; content?: JSONContent }) {
    Object.assign(pendingRef.current, patch)
    setSaveStatus("pending")
    if (timerRef.current) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(flush, 900)
  }

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current)
        flush()
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const editor = useEditor({
    shouldRerenderOnTransaction: true,
    editable: false,
    extensions: [
      StarterKit.configure({
        codeBlock: false,
        link: { openOnClick: false, autolink: true, HTMLAttributes: { rel: "noopener noreferrer", target: "_blank" } },
      }),
      CodeBlockLowlight.configure({ lowlight }),
      TextStyle,
      Color,
      FontFamily,
      FontSize,
      Highlight.configure({ multicolor: true }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      Superscript,
      Subscript,
      Placeholder.configure({ placeholder: "Начните печатать…" }),
      CharacterCount,
      Image,
      CommentMark,
    ],
    onUpdate: ({ editor }) => {
      if (!loadedRef.current) return
      scheduleSave({ content: editor.getJSON() })
    },
  })

  useEffect(() => {
    if (!data || !editor) return
    setTitle(data.title)
    setIcon(data.icon)
    setIsStarred(data.is_starred)
    versionRef.current = data.version
    editor.commands.setContent(data.content ?? { type: "doc", content: [{ type: "paragraph" }] }, { emitUpdate: false })
    editor.setEditable(canEditDocument(data.access_level))
    loadedRef.current = true
    setSaveStatus("saved")
  }, [data, editor])

  const starMutation = useMutation({
    mutationFn: (next: boolean) => (next ? documentsApi.star(documentId) : documentsApi.unstar(documentId)),
  })
  function toggleStar() {
    const next = !isStarred
    setIsStarred(next)
    starMutation.mutate(next)
  }

  const duplicateMutation = useMutation({
    mutationFn: () => documentsApi.duplicate(documentId),
    onSuccess: (res) => { toast.success("Копия создана"); navigate(`/documents/${res.id}`) },
    onError: (e: Error) => toast.error(e.message),
  })

  const trashMutation = useMutation({
    mutationFn: () => documentsApi.trash(documentId),
    onSuccess: () => { toast.success("Перемещено в корзину"); navigate("/documents") },
    onError: (e: Error) => toast.error(e.message),
  })

  function handleTitleChange(value: string) {
    setTitle(value)
    scheduleSave({ title: value })
  }

  function handleIconChange(value: string) {
    setIcon(value)
    scheduleSave({ icon: value })
  }

  function handleAddComment() {
    if (!editor) return
    const { from, to, empty } = editor.state.selection
    if (empty) return
    const text = editor.state.doc.textBetween(from, to, " ")
    const markId = crypto.randomUUID()
    editor.chain().focus().setComment(markId).run()
    scheduleSave({ content: editor.getJSON() })
    setDraftComment({ markId, anchorText: text })
    setCommentsOpen(true)
  }

  async function handleInsertImage() {
    if (!editor) return
    const input = document.createElement("input")
    input.type = "file"
    input.accept = "image/*"
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const { url } = await documentsApi.uploadImage(documentId, file)
        editor.chain().focus().setImage({ src: url }).run()
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Не удалось загрузить изображение")
      }
    }
    input.click()
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-[70vh] w-full" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
        <p className="text-muted-foreground">Документ не найден или у вас нет к нему доступа.</p>
        <Button variant="outline" onClick={() => navigate("/documents")}><ArrowLeft className="mr-1 size-4" /> К документам</Button>
      </div>
    )
  }

  const words = editor?.storage.characterCount?.words() ?? 0
  const characters = editor?.storage.characterCount?.characters() ?? 0

  return (
    <div className="flex flex-col">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="icon-sm" onClick={() => navigate("/documents")}><ArrowLeft className="size-4" /></Button>

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon-sm" disabled={!editable} title="Иконка документа">
              {icon ? <span className="text-lg leading-none">{icon}</span> : <Smile className="size-4 text-muted-foreground" />}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-2">
            <div className="grid grid-cols-5 gap-1">
              {EMOJI_CHOICES.map((e) => (
                <button key={e} className="rounded-md p-1.5 text-lg hover:bg-accent" onClick={() => handleIconChange(e)}>{e}</button>
              ))}
            </div>
          </PopoverContent>
        </Popover>

        <Input
          value={title}
          disabled={!editable}
          onChange={(e) => handleTitleChange(e.target.value)}
          className="h-9 max-w-md border-none px-1 text-lg font-semibold shadow-none focus-visible:ring-0"
          placeholder="Без названия"
        />

        <SaveStatusIndicator status={saveStatus} />

        <div className="flex-1" />

        <Button variant="ghost" size="icon-sm" onClick={toggleStar} title={isStarred ? "Убрать из избранного" : "В избранное"}>
          <Star className={`size-4 ${isStarred ? "fill-warning text-warning" : ""}`} />
        </Button>
        <Button variant="outline" size="sm" onClick={() => setCommentsOpen((v) => !v)} className="gap-1.5">
          <MessageSquare className="size-4" /> Комментарии
        </Button>
        <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)} className="gap-1.5">
          <HistoryIcon className="size-4" /> История
        </Button>
        {accessLevel === "owner" && (
          <Button size="sm" onClick={() => setShareOpen(true)} className="gap-1.5">
            <Share2 className="size-4" /> Доступ
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm"><MoreHorizontal className="size-4" /></Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => duplicateMutation.mutate()}><Copy className="mr-1 size-4" /> Дублировать</DropdownMenuItem>
            {accessLevel === "owner" && (
              <DropdownMenuItem variant="destructive" onClick={() => trashMutation.mutate()}><Trash2 className="mr-1 size-4" /> В корзину</DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="doc-page flex overflow-hidden">
        <div className="flex min-w-0 flex-1 flex-col">
          <EditorToolbar editor={editor} accessLevel={accessLevel} onAddComment={handleAddComment} onInsertImage={handleInsertImage} />
          <div className="mx-auto w-full max-w-[820px] px-6 py-10 sm:px-12">
            <EditorContent editor={editor} className="tiptap" />
          </div>
          <div className="border-t px-4 py-1.5 text-xs text-muted-foreground">{words} слов · {characters} симв.</div>
        </div>
        {commentsOpen && (
          <CommentsSidebar
            documentId={documentId}
            editor={editor}
            canComment={commentable}
            draft={draftComment}
            onDraftConsumed={() => setDraftComment(null)}
          />
        )}
      </div>

      <ShareDialog documentId={documentId} open={shareOpen} onOpenChange={setShareOpen} />
      <VersionHistoryDialog
        documentId={documentId}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onRestored={() => queryClient.invalidateQueries({ queryKey: ["document", documentId] })}
      />
    </div>
  )
}

export function DocumentEditorPage() {
  const { documentId } = useParams()
  const id = Number(documentId)
  if (!documentId || Number.isNaN(id)) return <Navigate to="/documents" replace />
  return <DocumentEditorInner key={id} documentId={id} />
}
