import { useEffect, useRef, useState } from "react"
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowLeft, Share2, History as HistoryIcon, Star, MoreHorizontal,
  Copy, Trash2, Send, Bookmark, Smile, Maximize2, Minimize2, Plus, X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { documentsApi, canEditDocument } from "@/pages/documents/api"
import { ApiError } from "@/lib/api"
import { ShareDialog } from "@/pages/documents/ShareDialog"
import { VersionHistoryDialog } from "@/pages/documents/VersionHistoryDialog"
import { SetupPermissionsDialog } from "@/pages/documents/SetupPermissionsDialog"

interface ReturnTarget { pathname: string; state?: Record<string, unknown> }

const EMOJI_CHOICES = ["📄", "📝", "📋", "📌", "📖", "🗂️", "💡", "⚙️", "🚀", "✅", "📊", "🔒", "🎯", "📢", "🧭"]
const EDITOR_CONTAINER_ID = "onlyoffice-editor-container"

declare global {
  interface Window {
    DocsAPI?: { DocEditor: new (id: string, config: Record<string, unknown>) => { destroyEditor: () => void } }
  }
}

function loadOnlyOfficeScript(documentServerUrl: string): Promise<void> {
  const src = `${documentServerUrl.replace(/\/$/, "")}/web-apps/apps/api/documents/api.js`
  const existing = document.querySelector(`script[src="${src}"]`)
  if (existing && window.DocsAPI) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const script = document.createElement("script")
    script.src = src
    script.onload = () => resolve()
    script.onerror = () => reject(new Error("Не удалось загрузить редактор ONLYOFFICE"))
    document.body.appendChild(script)
  })
}

function DocumentEditorInner({ documentId }: { documentId: number }) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const editorInstanceRef = useRef<{ destroyEditor: () => void } | null>(null)

  // Where "back"/"leave" should go — set by whichever page opened this
  // document (e.g. a department's Документация tab wants to return to its
  // own tab, not the generic /documents root). See documentation.tsx and
  // departments/detail.tsx for the sending/receiving side of this state.
  const navState = location.state as { returnTo?: ReturnTarget } | null
  const returnTo = navState?.returnTo
  const [setupPromptOpen, setSetupPromptOpen] = useState(false)
  // Set once the exit prompt has been shown/dismissed, so declining it lets
  // the next exit attempt actually leave instead of re-prompting forever.
  const [setupPromptDone, setSetupPromptDone] = useState(false)

  function goBack() {
    if (returnTo) navigate(returnTo.pathname, returnTo.state ? { state: returnTo.state } : undefined)
    else navigate("/documents")
  }

  function requestExit() {
    // Warn the owner on the way out if they never configured sharing —
    // otherwise a document silently keeps whatever was seeded at creation:
    // nothing at all for a personal one (nobody else can see it), or plain
    // department-wide view for a department one. Driven by the backend's
    // permissions_configured flag rather than the justCreated navigation
    // state alone, so it still fires after a page refresh or when the
    // author reopens a document they never got around to sharing. Shown at
    // most once per editor session (setupPromptDone).
    const needsSetup = data?.access_level === "owner" && !data?.permissions_configured
    if (needsSetup && !setupPromptDone && !setupPromptOpen) {
      setSetupPromptOpen(true)
      return
    }
    goBack()
  }

  const { data, isLoading, isError } = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => documentsApi.get(documentId),
  })

  const [title, setTitle] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [shareOpen, setShareOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [isStarred, setIsStarred] = useState(false)
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false)
  const [templateName, setTemplateName] = useState("")
  const [editorError, setEditorError] = useState<string | null>(null)
  const [isFocusMode, setIsFocusMode] = useState(false)

  const accessLevel = data?.access_level
  const editable = canEditDocument(accessLevel)

  const { data: tagsData } = useQuery({
    queryKey: ["document-tags", documentId],
    queryFn: () => documentsApi.tags.list(documentId),
    enabled: !!data?.department_id,
  })

  const addTagMutation = useMutation({
    mutationFn: (tagId: number) => documentsApi.tags.add(documentId, tagId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["document-tags", documentId] }),
    onError: (e: Error) => toast.error(e.message),
  })

  const removeTagMutation = useMutation({
    mutationFn: (tagId: number) => documentsApi.tags.remove(documentId, tagId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["document-tags", documentId] }),
    onError: (e: Error) => toast.error(e.message),
  })

  useEffect(() => {
    if (!data) return
    setTitle(data.title)
    setIcon(data.icon)
    setIsStarred(data.is_starred)
  }, [data])

  const isImage = data?.file_type === "image"

  useEffect(() => {
    if (!data || isImage) return
    let cancelled = false

    documentsApi.onlyofficeConfig(documentId).then(async ({ documentServerUrl, config }) => {
      if (!documentServerUrl) {
        setEditorError("ONLYOFFICE Document Server не настроен (onlyoffice_document_server_url пуст в конфигурации сервера).")
        return
      }
      try {
        await loadOnlyOfficeScript(documentServerUrl)
      } catch (e) {
        if (!cancelled) setEditorError(e instanceof Error ? e.message : "Не удалось загрузить редактор")
        return
      }
      if (cancelled || !window.DocsAPI) return
      editorInstanceRef.current?.destroyEditor()
      editorInstanceRef.current = new window.DocsAPI.DocEditor(EDITOR_CONTAINER_ID, config)
    }).catch((e) => {
      if (!cancelled) setEditorError(e instanceof Error ? e.message : "Не удалось получить конфигурацию редактора")
    })

    return () => {
      cancelled = true
      editorInstanceRef.current?.destroyEditor()
      editorInstanceRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, documentId])

  useEffect(() => {
    if (!isFocusMode) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setIsFocusMode(false)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [isFocusMode])

  const updateMutation = useMutation({
    mutationFn: (payload: { title?: string; icon?: string | null }) => documentsApi.update(documentId, payload),
    onError: (e: Error) => toast.error(e.message),
  })

  function handleTitleBlur() {
    if (data && title !== data.title) updateMutation.mutate({ title })
  }

  function handleIconChange(value: string) {
    setIcon(value)
    updateMutation.mutate({ icon: value })
  }

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

  const saveTemplateMutation = useMutation({
    mutationFn: () => documentsApi.saveAsTemplate(documentId, { name: templateName.trim() }),
    onSuccess: () => { toast.success("Шаблон сохранён"); setSaveTemplateOpen(false); setTemplateName("") },
    onError: (e: Error) => toast.error(e.message),
  })

  const trashMutation = useMutation({
    mutationFn: () => documentsApi.trash(documentId),
    onSuccess: () => { toast.success("Перемещено в корзину"); goBack() },
    onError: (e: Error) => toast.error(e.message),
  })

  const [discordChannelDialogOpen, setDiscordChannelDialogOpen] = useState(false)
  const [discordChannelId, setDiscordChannelId] = useState("")
  const [verifyingChannel, setVerifyingChannel] = useState(false)

  const publishMutation = useMutation({
    mutationFn: (channelId?: string) => documentsApi.publishToDiscord(documentId, channelId),
    onSuccess: (res) => {
      toast.success(res.is_sync ? "Синхронизировано с Discord" : "Опубликовано в Discord")
      setDiscordChannelDialogOpen(false)
      setDiscordChannelId("")
      queryClient.invalidateQueries({ queryKey: ["document", documentId] })
    },
    onError: (e: Error) => {
      // No channel could be auto-resolved (no folder/order/news config) —
      // prompt for one manually instead of just failing the click.
      if (e instanceof ApiError && e.status === 400 && !data?.discord_message_id) {
        setDiscordChannelDialogOpen(true)
      } else {
        toast.error(e.message)
      }
    },
  })

  async function verifyAndPublish() {
    const channelId = discordChannelId.trim()
    if (!channelId) return
    setVerifyingChannel(true)
    try {
      const check = await documentsApi.verifyDiscordChannel(documentId, channelId)
      if (!check.verified) {
        toast.error("Этот канал не принадлежит Discord-категории отдела")
        return
      }
      publishMutation.mutate(channelId)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Ошибка проверки канала")
    } finally {
      setVerifyingChannel(false)
    }
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
        <Button variant="outline" onClick={goBack}><ArrowLeft className="mr-1 size-4" /> К документам</Button>
      </div>
    )
  }

  return (
    <div className={isFocusMode ? "fixed inset-0 z-50 flex flex-col bg-background p-3" : "flex h-[calc(100vh-6rem)] flex-col"}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="icon-sm" onClick={requestExit}><ArrowLeft className="size-4" /></Button>

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
          onChange={(e) => setTitle(e.target.value)}
          onBlur={handleTitleBlur}
          className="h-9 max-w-md border-none px-1 text-lg font-semibold shadow-none focus-visible:ring-0"
          placeholder="Без названия"
        />

        <div className="flex-1" />

        <Button variant="ghost" size="icon-sm" onClick={toggleStar} title={isStarred ? "Убрать из избранного" : "В избранное"}>
          <Star className={`size-4 ${isStarred ? "fill-warning text-warning" : ""}`} />
        </Button>
        <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)} className="gap-1.5">
          <HistoryIcon className="size-4" /> История
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setIsFocusMode((v) => !v)}
          title={isFocusMode ? "Выйти из полноэкранного режима (Esc)" : "Полноэкранный режим"}
        >
          {isFocusMode ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
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
            {editable && !isImage && (
              <DropdownMenuItem onClick={() => { setTemplateName(title); setSaveTemplateOpen(true) }}>
                <Bookmark className="mr-1 size-4" /> Сохранить как шаблон
              </DropdownMenuItem>
            )}
            {editable && data?.department_id && (
              <DropdownMenuItem onClick={() => publishMutation.mutate(undefined)}>
                <Send className="mr-1 size-4" /> {data.discord_message_id ? "Синхронизировать с Discord" : "Опубликовать в Discord"}
              </DropdownMenuItem>
            )}
            {accessLevel === "owner" && (
              <DropdownMenuItem variant="destructive" onClick={() => trashMutation.mutate()}><Trash2 className="mr-1 size-4" /> В корзину</DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {!!data?.department_id && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          {tagsData?.tags.map((t) => (
            <Badge key={t.id} style={{ backgroundColor: `${t.color}22`, color: t.color }} className="gap-1">
              {t.name}
              {editable && (
                <button onClick={() => removeTagMutation.mutate(t.id)}><X className="size-3" /></button>
              )}
            </Badge>
          ))}
          {editable && !!tagsData?.available.length && (
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" size="icon-xs" title="Добавить тег"><Plus className="size-3.5" /></Button>
              </PopoverTrigger>
              <PopoverContent className="w-48 p-1">
                <div className="flex flex-col gap-0.5">
                  {tagsData.available.map((t) => (
                    <button
                      key={t.id}
                      className="flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm hover:bg-accent"
                      onClick={() => addTagMutation.mutate(t.id)}
                    >
                      <Badge style={{ backgroundColor: `${t.color}22`, color: t.color }}>{t.name}</Badge>
                    </button>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border">
        {isImage ? (
          <div className="flex h-full w-full items-center justify-center overflow-auto bg-muted/30 p-4">
            <img
              src={`/api/v1/admin/documents/${documentId}/image`}
              alt={data?.title ?? ""}
              className="max-h-full max-w-full object-contain"
            />
          </div>
        ) : editorError ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
            {editorError}
          </div>
        ) : (
          <div id={EDITOR_CONTAINER_ID} className="h-full w-full" />
        )}
      </div>

      <ShareDialog documentId={documentId} open={shareOpen} onOpenChange={setShareOpen} />
      <SetupPermissionsDialog
        documentId={documentId}
        open={setupPromptOpen}
        isDepartmentDoc={!!data?.department_id}
        onDone={() => { setSetupPromptOpen(false); setSetupPromptDone(true); goBack() }}
      />
      <VersionHistoryDialog
        documentId={documentId}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onRestored={() => queryClient.invalidateQueries({ queryKey: ["document", documentId] })}
      />

      <Dialog open={discordChannelDialogOpen} onOpenChange={setDiscordChannelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Укажите канал Discord</DialogTitle>
            <DialogDescription>
              Для этого документа не настроен канал публикации (ни у папки, ни у отдела). Введите ID канала — если у отдела задана Discord-категория, канал будет проверен на принадлежность ей.
            </DialogDescription>
          </DialogHeader>
          <Input value={discordChannelId} onChange={(e) => setDiscordChannelId(e.target.value)} placeholder="123456789012345678" autoFocus />
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscordChannelDialogOpen(false)}>Отмена</Button>
            <Button disabled={!discordChannelId.trim() || verifyingChannel || publishMutation.isPending} onClick={verifyAndPublish}>
              {verifyingChannel ? "Проверка канала…" : "Проверить и опубликовать"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={saveTemplateOpen} onOpenChange={setSaveTemplateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Сохранить как шаблон</DialogTitle>
            <DialogDescription>Текущий файл документа станет доступен как шаблон для всех при создании новых документов.</DialogDescription>
          </DialogHeader>
          <div>
            <Label className="mb-1.5 block">Название шаблона</Label>
            <Input value={templateName} onChange={(e) => setTemplateName(e.target.value)} autoFocus />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveTemplateOpen(false)}>Отмена</Button>
            <Button disabled={!templateName.trim() || saveTemplateMutation.isPending} onClick={() => saveTemplateMutation.mutate()}>Сохранить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function OnlyOfficeEditorPage() {
  const { documentId } = useParams()
  const id = Number(documentId)
  if (!documentId || Number.isNaN(id)) return <Navigate to="/documents" replace />
  return <DocumentEditorInner key={id} documentId={id} />
}
