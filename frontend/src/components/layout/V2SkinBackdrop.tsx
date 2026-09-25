import { IS_V2 } from "@/lib/ui-version"
import { useV2Skin } from "@/lib/v2-skin"
import { useMissionTuning } from "@/lib/v2-mission-tuning"

function arc(r: number, share: number) {
  const length = 2 * Math.PI * r
  return `${length * share} ${length * (1 - share)}`
}

const SVG_PROPS = {
  viewBox: "0 0 1000 1000",
  className: "absolute inset-0 size-full",
  fill: "none",
} as const

/*
  Concentric instrument rings behind the "mission" design. Pure decoration:
  fixed, painted below the page (z -1 over the <html> canvas), invisible to
  the pointer and to assistive tech. SVG rather than CSS circles because the
  V2 shell forces border-radius to zero everywhere.

  Each rotating set is its own static SVG inside an HTML layer, and the layer
  is what rotates. Animating a <g> inside the SVG instead made the browser
  repaint the whole 1400px drawing on every frame, which dropped scrolling a
  long table from 60 to ~50 fps with 33 ms stutters.
*/
export function V2SkinBackdrop() {
  const [skin] = useV2Skin()
  const [tuning] = useMissionTuning()
  if (!IS_V2 || skin !== "mission" || !tuning.rings) return null

  const spin = tuning.ringsMotion ? "v2-orbit" : ""
  const spinReverse = tuning.ringsMotion ? "v2-orbit-reverse" : ""

  return (
    <div className="pointer-events-none fixed inset-0 z-[-1] flex items-center justify-center overflow-hidden" aria-hidden="true">
      <div className="relative aspect-square w-[min(1400px,160vmin)]" style={{ color: "var(--v2-line)" }}>
        <svg {...SVG_PROPS}>
          <circle cx="500" cy="500" r="480" stroke="currentColor" strokeWidth="1" strokeOpacity="0.12" />
          <circle cx="500" cy="500" r="250" stroke="currentColor" strokeWidth="1" strokeOpacity="0.16" />
          <circle cx="500" cy="500" r="170" stroke="currentColor" strokeWidth="2" strokeOpacity="0.12" />
          <g stroke="currentColor" strokeOpacity="0.32" strokeWidth="2">
            <line x1="500" y1="10" x2="500" y2="44" />
            <line x1="500" y1="956" x2="500" y2="990" />
            <line x1="10" y1="500" x2="44" y2="500" />
            <line x1="956" y1="500" x2="990" y2="500" />
          </g>
        </svg>
        <div className={`absolute inset-0 ${spin}`}>
          <svg {...SVG_PROPS}>
            <circle cx="500" cy="500" r="400" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2" strokeDasharray="3 14" />
            <circle cx="500" cy="500" r="400" stroke="currentColor" strokeWidth="6" strokeOpacity="0.32" strokeDasharray={arc(400, 0.08)} />
          </svg>
        </div>
        <div className={`absolute inset-0 ${spinReverse}`}>
          <svg {...SVG_PROPS}>
            <circle cx="500" cy="500" r="330" stroke="currentColor" strokeWidth="10" strokeOpacity="0.18" strokeDasharray={arc(330, 0.22)} />
            <circle cx="500" cy="500" r="300" stroke="currentColor" strokeWidth="3" strokeOpacity="0.28" strokeDasharray={arc(300, 0.12)} transform="rotate(140 500 500)" />
          </svg>
        </div>
      </div>
    </div>
  )
}
