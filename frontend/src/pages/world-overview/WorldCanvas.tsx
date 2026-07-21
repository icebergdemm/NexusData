import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Maximize2 } from "lucide-react"
import type { WorldGeometry, WorldState, WorldCharacter } from "./api"
import { resolveCharacterItems } from "./api"
import { CharacterInfoCard } from "./CharacterInfoCard"

const TEAM_COLORS: Record<string, string> = {
  Team1: "#5AA0FF",
  Team2: "#FF8C5A",
  None: "#B4B4B4",
  FriendlyNPC: "#7CD992",
}

interface View { scale: number; offsetX: number; offsetY: number }

const POPUP_WIDTH = 260

/** 2D canvas renderer for the live round: static geometry (outline/hulls/
 * walls/doors, sub-local — translated to world coordinates using the
 * latest sub positions per WORLD_STATE_API_GUIDE.md §5) plus the dynamic
 * layer (hull water/fire fill, door openness, characters). Pan with drag,
 * zoom with the wheel, click a character to select it (see the side panel
 * in index.tsx for details on the selected one). */
export function WorldCanvas({ geometry, state, selectedCharacterId, onSelectCharacter }: {
  geometry: WorldGeometry
  state: WorldState
  selectedCharacterId: number | null
  onSelectCharacter: (id: number | null) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState<View>({ scale: 1, offsetX: 0, offsetY: 0 })
  const viewRef = useRef(view)
  viewRef.current = view
  const dragRef = useRef<{ startX: number; startY: number; startOffsetX: number; startOffsetY: number; moved: boolean } | null>(null)
  const [fittedFor, setFittedFor] = useState<string | null>(null)
  // Tracks which character we've already auto-centered on, so re-selecting
  // the SAME character on every ~3s poll doesn't keep snapping the camera
  // back if the user has since panned away manually.
  const centeredForRef = useRef<number | null>(null)

  const fitView = () => {
    const container = containerRef.current
    if (!container) return
    const w = geometry.outline_w || geometry.level_w || 100000
    const h = geometry.outline_h || geometry.level_h || 50000
    const cw = container.clientWidth || 800
    const ch = container.clientHeight || 500
    const scale = Math.min(cw / w, ch / h) * 0.9
    setView({ scale: scale || 0.01, offsetX: cw / 2, offsetY: ch / 2 })
  }

  // Re-fit whenever a different round is selected.
  useEffect(() => {
    if (fittedFor !== geometry.round_id) {
      fitView()
      setFittedFor(geometry.round_id)
      centeredForRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry.round_id])

  // Center the camera on a newly-selected character (from either a canvas
  // click or the sidebar list — both go through the same selectedCharacterId
  // prop) — once per selection, not on every subsequent ~3s state refresh.
  useEffect(() => {
    if (selectedCharacterId == null) {
      centeredForRef.current = null
      return
    }
    if (centeredForRef.current === selectedCharacterId) return
    const c = state.characters.find((ch) => ch.id === selectedCharacterId)
    const container = containerRef.current
    if (!c || !container) return
    const cw = container.clientWidth
    const ch = container.clientHeight
    setView((v) => ({ ...v, offsetX: cw / 2 - c.x * v.scale, offsetY: ch / 2 + c.y * v.scale }))
    centeredForRef.current = selectedCharacterId
  }, [selectedCharacterId, state.characters])

  const toScreen = (x: number, y: number, v: View): [number, number] => [
    v.offsetX + x * v.scale,
    v.offsetY - y * v.scale, // game Y grows "up", canvas Y grows down
  ]
  const toWorld = (sx: number, sy: number, v: View): [number, number] => [
    (sx - v.offsetX) / v.scale,
    (v.offsetY - sy) / v.scale,
  ]

  // Redraw on every relevant change.
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const cw = container.clientWidth
    const ch = container.clientHeight
    if (canvas.width !== cw * dpr || canvas.height !== ch * dpr) {
      canvas.width = cw * dpr
      canvas.height = ch * dpr
    }
    canvas.style.width = `${cw}px`
    canvas.style.height = `${ch}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cw, ch)
    ctx.fillStyle = "#0A0E14"
    ctx.fillRect(0, 0, cw, ch)

    const subPosById = new Map(state.subs.map((s) => [s.id, s]))
    const hullStateById = new Map(state.hulls_state.map((h) => [h.id, h]))
    const doorStateById = new Map(state.doors_state.map((d) => [d.id, d]))

    // Static level outline (world coords, no sub offset).
    ctx.fillStyle = "#141a22"
    ctx.strokeStyle = "#2a3540"
    ctx.lineWidth = 1
    for (const poly of geometry.outline) {
      if (!poly.length) continue
      ctx.beginPath()
      poly.forEach(([x, y], i) => {
        const [sx, sy] = toScreen(x, y, view)
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy)
      })
      ctx.closePath()
      ctx.fill()
      ctx.stroke()
    }

    const drawSubRect = (subId: number, r: [number, number, number, number]) => {
      const sub = subPosById.get(subId)
      const wx = (sub?.x ?? 0) + r[0]
      const wy = (sub?.y ?? 0) + r[1]
      const [x1, y1] = toScreen(wx, wy + r[3], view)
      const [x2, y2] = toScreen(wx + r[2], wy, view)
      return { x1, y1, w: x2 - x1, h: y2 - y1 }
    }

    for (const hull of geometry.hulls) {
      const { x1, y1, w, h } = drawSubRect(hull.sub, hull.r)
      ctx.fillStyle = "#18222C"
      ctx.fillRect(x1, y1, w, h)
      const hs = hullStateById.get(hull.id)
      if (hs && hs.water > 0) {
        const waterH = h * (hs.water / 100)
        ctx.fillStyle = "rgba(40,90,140,0.66)"
        ctx.fillRect(x1, y1 + h - waterH, w, waterH)
      }
      if (hs && hs.fire > 0) {
        ctx.fillStyle = `rgba(255,80,40,${Math.min(0.55, hs.fire / 255)})`
        ctx.fillRect(x1, y1, w, h)
      }
      ctx.strokeStyle = "#1E2830"
      ctx.strokeRect(x1, y1, w, h)
    }

    for (const wall of geometry.walls) {
      const { x1, y1, w, h } = drawSubRect(wall.sub, wall.r)
      ctx.fillStyle = wall.p ? "#5A646E" : "#8C96A0"
      ctx.fillRect(x1, y1, w, h)
    }

    for (const door of geometry.doors) {
      const { x1, y1, w, h } = drawSubRect(door.sub, door.r)
      const ds = doorStateById.get(door.id)
      const openPct = ds ? ds.open / 100 : 0
      ctx.globalAlpha = 0.35 + 0.65 * (1 - openPct)
      ctx.fillStyle = openPct > 50 ? "#50C86E" : "#C8463C"
      ctx.fillRect(x1, y1, w, h)
      ctx.globalAlpha = 1
    }

    for (const c of state.characters) {
      const [sx, sy] = toScreen(c.x, c.y, view)
      const radius = c.human ? 5 : 7
      ctx.beginPath()
      ctx.arc(sx, sy, radius, 0, Math.PI * 2)
      ctx.fillStyle = c.dead ? "#5A5A5A" : (TEAM_COLORS[c.team] ?? "#C85A5A")
      ctx.fill()
      if (c.id === selectedCharacterId) {
        ctx.lineWidth = 2
        ctx.strokeStyle = "#FFE678"
        ctx.stroke()
      }
      if (c.human) {
        ctx.fillStyle = "rgba(255,255,255,0.85)"
        ctx.font = "10px sans-serif"
        ctx.textAlign = "center"
        ctx.fillText(c.name, sx, sy - radius - 4)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry, state, view, selectedCharacterId])

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const v = viewRef.current
    const [wx, wy] = toWorld(mx, my, v)
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
    const nextScale = Math.min(Math.max(v.scale * factor, 0.001), 5)
    // Keep the point under the cursor fixed while zooming.
    const nextOffsetX = mx - wx * nextScale
    const nextOffsetY = my + wy * nextScale
    setView({ scale: nextScale, offsetX: nextOffsetX, offsetY: nextOffsetY })
  }

  const onMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { startX: e.clientX, startY: e.clientY, startOffsetX: view.offsetX, startOffsetY: view.offsetY, moved: false }
  }
  const onMouseMove = (e: React.MouseEvent) => {
    const drag = dragRef.current
    if (!drag) return
    const dx = e.clientX - drag.startX
    const dy = e.clientY - drag.startY
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.moved = true
    setView((v) => ({ ...v, offsetX: drag.startOffsetX + dx, offsetY: drag.startOffsetY + dy }))
  }
  const onMouseUp = (e: React.MouseEvent) => {
    const drag = dragRef.current
    dragRef.current = null
    if (!drag || drag.moved) return

    // Click (not a drag) — hit-test the nearest character within ~10px.
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    let closest: WorldCharacter | null = null
    let closestDist = 12 // px
    for (const c of state.characters) {
      const [sx, sy] = toScreen(c.x, c.y, view)
      const d = Math.hypot(sx - mx, sy - my)
      if (d < closestDist) { closestDist = d; closest = c }
    }
    onSelectCharacter(closest ? closest.id : null)
  }

  const selectedCharacter = selectedCharacterId != null
    ? state.characters.find((c) => c.id === selectedCharacterId) ?? null
    : null

  // Anchor the popup next to the character on screen, clamped so it never
  // spills outside the canvas viewport (item 1 — used to only show details
  // in the sidebar, disconnected from where the character actually is).
  let popupStyle: React.CSSProperties | null = null
  if (selectedCharacter) {
    const container = containerRef.current
    const cw = container?.clientWidth ?? 800
    const chh = container?.clientHeight ?? 500
    const [sx, sy] = toScreen(selectedCharacter.x, selectedCharacter.y, view)
    const estimatedHeight = 180
    let left = sx + 16
    if (left + POPUP_WIDTH > cw - 8) left = sx - POPUP_WIDTH - 16
    left = Math.max(8, Math.min(left, cw - POPUP_WIDTH - 8))
    let top = sy - estimatedHeight / 2
    top = Math.max(8, Math.min(top, chh - estimatedHeight - 8))
    popupStyle = { left, top, width: POPUP_WIDTH }
  }

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full cursor-grab overflow-hidden rounded-lg border bg-[#0A0E14] active:cursor-grabbing"
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={() => { dragRef.current = null }}
    >
      <canvas ref={canvasRef} />
      <Button
        size="icon-sm"
        variant="outline"
        className="absolute right-2 top-2 bg-background/80"
        title="Сбросить вид"
        onClick={fitView}
      >
        <Maximize2 className="size-4" />
      </Button>

      {selectedCharacter && popupStyle && (
        <div
          className="absolute z-10"
          style={popupStyle}
          onMouseDown={(e) => e.stopPropagation()}
          onMouseUp={(e) => e.stopPropagation()}
        >
          <CharacterInfoCard
            character={selectedCharacter}
            items={resolveCharacterItems(state, selectedCharacter.id)}
            onClose={() => onSelectCharacter(null)}
          />
        </div>
      )}
    </div>
  )
}
