import { useRef, useState } from "react"
import { FileUp, Paperclip, Send, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { formatBytes } from "./ticket-utils"

function fileKey(file: File): string {
  return `${file.name}-${file.size}-${file.lastModified}`
}

function mergeFiles(current: File[], incoming: File[]): File[] {
  const existing = new Set(current.map(fileKey))
  return [...current, ...incoming.filter((file) => !existing.has(fileKey(file)))]
}

export function TicketComposer({
  text,
  files,
  pending,
  onTextChange,
  onFilesChange,
  onSend,
}: {
  text: string
  files: File[]
  pending: boolean
  onTextChange: (text: string) => void
  onFilesChange: (files: File[]) => void
  onSend: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const canSend = Boolean(text.trim() || files.length)

  const addFiles = (incoming: File[]) => {
    if (!incoming.length) return
    onFilesChange(mergeFiles(files, incoming))
    if (inputRef.current) inputRef.current.value = ""
  }

  return (
    <form
      onSubmit={(event) => { event.preventDefault(); if (canSend && !pending) onSend() }}
      onDragEnter={(event) => { event.preventDefault(); setDragging(true) }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
      }}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        addFiles(Array.from(event.dataTransfer.files))
      }}
      className={cn("relative border-t bg-background p-3", dragging && "ring-2 ring-inset ring-ring")}
    >
      {dragging ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/90 text-sm font-medium">
          <FileUp className="mr-2 size-5" />Отпустите файлы для добавления
        </div>
      ) : null}
      {files.length ? (
        <div className="mb-2 flex max-h-24 flex-wrap gap-1.5 overflow-y-auto" aria-label="Выбранные файлы">
          {files.map((file) => (
            <span key={fileKey(file)} className="inline-flex max-w-full items-center gap-1.5 border bg-muted/40 px-2 py-1 text-[10px]">
              <Paperclip className="size-3 shrink-0" />
              <span className="max-w-44 truncate">{file.name}</span>
              <span className="text-muted-foreground">{formatBytes(file.size)}</span>
              <button
                type="button"
                onClick={() => onFilesChange(files.filter((item) => fileKey(item) !== fileKey(file)))}
                className="ml-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`Убрать ${file.name}`}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex items-end gap-2">
        <input
          ref={inputRef}
          type="file"
          multiple
          className="sr-only"
          accept="image/*,video/*,audio/*,.pdf,.txt,.log,.zip,.7z"
          onChange={(event) => addFiles(Array.from(event.target.files ?? []))}
          aria-label="Добавить вложения"
        />
        <Button type="button" variant="ghost" size="icon" onClick={() => inputRef.current?.click()} title="Добавить файлы" aria-label="Добавить файлы">
          <Paperclip />
        </Button>
        <div className="min-w-0 flex-1">
          <label htmlFor="ticket-message" className="sr-only">Сообщение в тикет</label>
          <Textarea
            id="ticket-message"
            value={text}
            onChange={(event) => onTextChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault()
                if (canSend && !pending) onSend()
              }
            }}
            onPaste={(event) => {
              const pastedFiles = Array.from(event.clipboardData.files)
              if (pastedFiles.length) {
                event.preventDefault()
                addFiles(pastedFiles)
              }
            }}
            rows={2}
            disabled={pending}
            placeholder="Напишите ответ…"
            className="min-h-14 max-h-40 resize-none"
          />
        </div>
        <Button type="submit" size="icon" disabled={!canSend || pending} title="Отправить (Ctrl + Enter)" aria-label={pending ? "Сообщение отправляется" : "Отправить сообщение"}>
          <Send />
        </Button>
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 pl-11 text-[10px] text-muted-foreground">
        <span>Можно вставить или перетащить фото, видео и файлы</span>
        <span className="hidden font-mono sm:inline">Ctrl + Enter</span>
      </div>
    </form>
  )
}
