import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"
import { FileUp, Ship, X } from "lucide-react"
import { cn } from "@/lib/utils"

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB"]
  let v = bytes, i = -1
  do { v /= 1024; i++ } while (v >= 1024 && i < units.length - 1)
  return `${v.toFixed(1)} ${units[i]}`
}

/** Зона загрузки .sub файла: перетаскиванием или кликом.
 *
 * Расширение проверяется отдельно от input'а — атрибут `accept` только
 * подсказывает системному диалогу выбора файла, на drop он никак не
 * действует, и без ручной проверки перетащить можно что угодно. */
export function SubFileDropZone({
  file, onFile, disabled, compact = false,
}: {
  file: File | null
  onFile: (f: File | null) => void
  disabled?: boolean
  /** Компактный вид — без второй строки-подсказки, для встраивания в тулбар. */
  compact?: boolean
}) {
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const acceptFile = useCallback((f: File | undefined | null) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith(".sub")) {
      // `accept=".sub"` только подсказывает системному диалогу выбора файла —
      // на drag-and-drop оно не действует, поэтому без явной проверки сюда
      // молча долетит что угодно.
      toast.error("Нужен файл с расширением .sub")
      return
    }
    onFile(f)
  }, [onFile])

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragOver(false)
    if (disabled) return
    acceptFile(e.dataTransfer.files?.[0])
  }

  if (file) {
    return (
      <div className={cn(
        "flex items-center gap-2 rounded-md border bg-muted/30 px-3",
        compact ? "h-9 py-1.5" : "py-2",
      )}>
        <Ship className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{file.name}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{fmtSize(file.size)}</span>
        {!disabled && (
          <button
            type="button"
            onClick={() => onFile(null)}
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Убрать файл"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>
    )
  }

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(e) => { if (!disabled && (e.key === "Enter" || e.key === " ")) inputRef.current?.click() }}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragOver(true) }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-md border border-dashed px-3 transition-colors",
        compact ? "h-9 py-1.5" : "py-4 justify-center",
        disabled ? "cursor-not-allowed opacity-60" : "hover:border-primary/50 hover:bg-accent/30",
        dragOver && !disabled ? "border-primary bg-primary/5" : "border-muted-foreground/30",
      )}
    >
      <input
        ref={inputRef} type="file" accept=".sub" className="hidden" disabled={disabled}
        onChange={(e) => { acceptFile(e.target.files?.[0]); e.target.value = "" }}
      />
      <FileUp className="size-4 shrink-0 text-muted-foreground" />
      <span className="text-sm text-muted-foreground">
        {compact ? "Перетащите .sub или нажмите" : "Перетащите .sub файл сюда или нажмите, чтобы выбрать"}
      </span>
    </div>
  )
}
