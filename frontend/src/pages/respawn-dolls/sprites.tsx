/** Отрисовка внешности куклы из настоящих спрайт-листов игры.
 *
 *  Листы (Human_male_heads.png и т.д.) выгружены скриптом
 *  tools/build_doll_catalog.py в /static/appearance/ вместе с координатами
 *  ячеек. Сетка листа 4×4, ячейка 128×128 — те же цифры, по которым рисует
 *  сам движок (SourceRect из HumanDefaultRagdoll.xml).
 *
 *  Два способа рисования, и это осознанно:
 *   • SpriteStack — CSS background-position. Дёшево, годится для десятков
 *     миниатюр в выборе причёски: браузер держит одну картинку в кэше и
 *     просто сдвигает фон.
 *   • CharacterPreview — canvas. Нужен ради ЦВЕТА: движок тонирует голову
 *     цветом кожи, а причёску с бородой — своими цветами, и повторить это
 *     средствами CSS без artefacts нельзя. На canvas тонирование делается
 *     честно: multiply по цвету, затем destination-in по самому спрайту,
 *     чтобы вернуть исходную прозрачность.
 */
import { useEffect, useRef, useState } from "react"
import type { AppearanceCatalog, Identity, SpriteRef } from "./types"

const FALLBACK_COLUMNS = 4

function sheetUrl(catalog: AppearanceCatalog, sheet: string): string {
  return `${catalog.sprite_base_url || "/static/appearance/"}${sheet}`
}

/** Слои внешности снизу вверх — тот же порядок, что и в игре. */
export function appearanceLayers(
  identity: Identity,
  catalog: AppearanceCatalog,
): { sprite: SpriteRef; color?: string }[] {
  const tags = identity.head_tags ?? []
  const gender = tags.includes("female") ? "female" : "male"
  const headTag = tags.find((t) => /^head\d+$/.test(t))

  const head =
    catalog.heads.find((h) => h.gender === gender && (!headTag || h.tags.includes(headTag))) ??
    catalog.heads.find((h) => h.gender === gender)
  if (!head) return []

  const wear = catalog.wearables[gender]
  const layers: { sprite: SpriteRef; color?: string }[] = [
    { sprite: head, color: identity.skin_color },
  ]

  const pick = (list: SpriteRef[] | undefined, index: number | undefined, color?: string) => {
    if (index === undefined || index < 0 || !list?.[index]) return
    layers.push({ sprite: list[index], color })
  }
  pick(wear?.face_attachment, identity.face_attachment_index)
  pick(wear?.beard, identity.beard_index, identity.facial_hair_color)
  pick(wear?.hair, identity.hair_index, identity.hair_color)
  return layers
}

/** Миниатюра: стопка спрайтов через background-position, без тонирования. */
export function SpriteStack({ layers, catalog, size = 64, className }: {
  layers: SpriteRef[]
  catalog: AppearanceCatalog
  size?: number
  className?: string
}) {
  const columns = catalog.sprite_grid?.columns ?? FALLBACK_COLUMNS
  const rows = catalog.sprite_grid?.rows ?? FALLBACK_COLUMNS
  return (
    <div className={`relative shrink-0 ${className ?? ""}`} style={{ width: size, height: size }}>
      {layers.map((layer, i) => (
        <div
          key={`${layer.sheet}-${layer.cell.join(",")}-${i}`}
          className="absolute inset-0"
          style={{
            backgroundImage: `url(${sheetUrl(catalog, layer.sheet)})`,
            backgroundSize: `${size * columns}px ${size * rows}px`,
            backgroundPosition: `${-layer.cell[0] * size}px ${-layer.cell[1] * size}px`,
            imageRendering: "auto",
          }}
        />
      ))}
    </div>
  )
}

/** Кэш загруженных листов на весь сеанс: их всего шесть, перезагружать
 *  каждый раз при открытии диалога незачем. */
const imageCache = new Map<string, HTMLImageElement>()

function loadSheet(url: string): Promise<HTMLImageElement> {
  const cached = imageCache.get(url)
  if (cached?.complete) return Promise.resolve(cached)
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => { imageCache.set(url, img); resolve(img) }
    img.onerror = () => reject(new Error(url))
    img.src = url
  })
}

/** Тонирование: multiply цветом, потом destination-in исходным спрайтом,
 *  иначе заливка закрасила бы и прозрачный фон ячейки. */
function drawTinted(
  target: CanvasRenderingContext2D,
  img: HTMLImageElement,
  cell: [number, number],
  cellSize: number,
  size: number,
  color?: string,
) {
  const sx = cell[0] * cellSize
  const sy = cell[1] * cellSize

  if (!color) {
    target.drawImage(img, sx, sy, cellSize, cellSize, 0, 0, size, size)
    return
  }

  const scratch = document.createElement("canvas")
  scratch.width = size
  scratch.height = size
  const ctx = scratch.getContext("2d")
  if (!ctx) return

  ctx.drawImage(img, sx, sy, cellSize, cellSize, 0, 0, size, size)
  ctx.globalCompositeOperation = "multiply"
  ctx.fillStyle = color
  ctx.fillRect(0, 0, size, size)
  ctx.globalCompositeOperation = "destination-in"
  ctx.drawImage(img, sx, sy, cellSize, cellSize, 0, 0, size, size)

  target.drawImage(scratch, 0, 0)
}

/** Крупный предпросмотр «как в игре»: голова нужного пресета, поверх —
 *  аксессуар, борода и причёска, всё в выбранных цветах. */
export function CharacterPreview({ identity, catalog, size = 192, className }: {
  identity: Identity
  catalog: AppearanceCatalog
  size?: number
  className?: string
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [failed, setFailed] = useState(false)
  const layers = appearanceLayers(identity, catalog)
  // Ключ пересборки: рисуем заново только когда реально поменялась внешность,
  // а не на каждый ререндер диалога.
  const key = JSON.stringify(layers)
  const cellSize = catalog.sprite_grid?.cell ?? 128

  useEffect(() => {
    let cancelled = false
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    Promise.all(layers.map((l) => loadSheet(sheetUrl(catalog, l.sprite.sheet))))
      .then((images) => {
        if (cancelled) return
        ctx.clearRect(0, 0, size, size)
        images.forEach((img, i) => {
          drawTinted(ctx, img, layers[i].sprite.cell, cellSize, size, layers[i].color)
        })
        setFailed(false)
      })
      .catch(() => { if (!cancelled) setFailed(true) })

    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, size, cellSize])

  return (
    <div className={`relative ${className ?? ""}`} style={{ width: size, height: size }}>
      <canvas ref={canvasRef} width={size} height={size} className="size-full" />
      {failed && (
        <div className="absolute inset-0 flex items-center justify-center rounded bg-muted text-center text-xs text-muted-foreground">
          Спрайты внешности не загрузились
        </div>
      )}
    </div>
  )
}
