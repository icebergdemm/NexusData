import { useEffect, useCallback } from "react"
import { X, ChevronLeft, ChevronRight, ExternalLink, Download } from "lucide-react"
import { Button } from "@/components/ui/button"

export interface LightboxImage {
  url: string
  filename: string
}

export function ImageLightbox({
  images,
  index,
  onClose,
  onIndexChange,
}: {
  images: LightboxImage[]
  index: number
  onClose: () => void
  onIndexChange: (next: number) => void
}) {
  const total = images.length
  const current = images[index]
  const hasPrev = index > 0
  const hasNext = index < total - 1

  const goPrev = useCallback(() => { if (hasPrev) onIndexChange(index - 1) }, [hasPrev, index, onIndexChange])
  const goNext = useCallback(() => { if (hasNext) onIndexChange(index + 1) }, [hasNext, index, onIndexChange])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      if (e.key === "ArrowLeft") goPrev()
      if (e.key === "ArrowRight") goNext()
    }
    window.addEventListener("keydown", onKey)
    // lock body scroll
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => {
      window.removeEventListener("keydown", onKey)
      document.body.style.overflow = prev
    }
  }, [onClose, goPrev, goNext])

  if (!current) return null

  return (
    <div className="fixed inset-0 z-[100] flex flex-col bg-black/90 backdrop-blur-sm animate-in fade-in-0">
      {/* Top bar */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-white/90 shrink-0">
        <div className="min-w-0 flex-1 truncate text-sm">
          <span className="font-medium">{current.filename}</span>
          {total > 1 && <span className="ml-2 text-white/60 text-xs">{index + 1} / {total}</span>}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <Button size="icon-sm" variant="ghost" className="text-white hover:bg-white/10 hover:text-white" onClick={() => window.open(current.url, "_blank")} title="Открыть в новой вкладке">
            <ExternalLink className="size-4" />
          </Button>
          <a href={current.url} download={current.filename} className="inline-flex size-7 items-center justify-center rounded-md text-white hover:bg-white/10" title="Скачать">
            <Download className="size-4" />
          </a>
          <Button size="icon-sm" variant="ghost" className="text-white hover:bg-white/10 hover:text-white" onClick={onClose} title="Закрыть (Esc)">
            <X className="size-5" />
          </Button>
        </div>
      </div>

      {/* Image area */}
      <div
        className="relative flex flex-1 min-h-0 items-center justify-center p-4 md:p-8"
        onClick={onClose}
      >
        {/* Prev */}
        {hasPrev && (
          <Button
            size="icon"
            variant="ghost"
            className="absolute left-2 md:left-4 z-10 rounded-full bg-black/40 text-white backdrop-blur hover:bg-black/60 hover:text-white"
            onClick={(e) => { e.stopPropagation(); goPrev() }}
            title="Назад (←)"
          >
            <ChevronLeft className="size-6" />
          </Button>
        )}
        {/* Image */}
        <img
          src={current.url}
          alt={current.filename}
          className="max-h-full max-w-full object-contain rounded-lg shadow-2xl select-none"
          onClick={(e) => e.stopPropagation()}
          draggable={false}
        />
        {/* Next */}
        {hasNext && (
          <Button
            size="icon"
            variant="ghost"
            className="absolute right-2 md:right-4 z-10 rounded-full bg-black/40 text-white backdrop-blur hover:bg-black/60 hover:text-white"
            onClick={(e) => { e.stopPropagation(); goNext() }}
            title="Вперёд (→)"
          >
            <ChevronRight className="size-6" />
          </Button>
        )}
      </div>

      {/* Thumbnails */}
      {total > 1 && (
        <div className="flex gap-1.5 justify-center overflow-x-auto px-3 py-2 shrink-0">
          {images.map((img, i) => (
            <button
              key={`${img.url}-${i}`}
              onClick={() => onIndexChange(i)}
              className={`relative h-12 w-16 shrink-0 overflow-hidden rounded border-2 transition-all ${i === index ? "border-white scale-105" : "border-white/20 opacity-60 hover:opacity-100 hover:border-white/40"}`}
              title={img.filename}
            >
              <img src={img.url} alt="" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {/* Click backdrop handled via outer div onClick */}
    </div>
  )
}
