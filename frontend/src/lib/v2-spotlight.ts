/**
 * Light under the pointer for the hard-edge design: the card being hovered
 * gets the pointer position as `--v2-mx` / `--v2-my` and a `data-v2-lit`
 * attribute, and index.css paints its frame and face from those.
 *
 * One passive listener for the whole document, at most one update per frame,
 * and only the single card under the pointer is touched — the rest of the
 * page never restyles while the mouse moves.
 */
const CARD = '[data-slot="card"]'

let lit: HTMLElement | null = null
let pending: PointerEvent | null = null
let frame = 0

function enabled() {
  const root = document.documentElement
  return root.dataset.v2Spotlight === "on" && root.dataset.v2Skin !== "mission"
}

function release() {
  lit?.removeAttribute("data-v2-lit")
  lit = null
}

function update() {
  frame = 0
  const event = pending
  pending = null
  if (!event || !enabled()) {
    release()
    return
  }
  const target = event.target instanceof Element ? event.target.closest<HTMLElement>(CARD) : null
  if (target !== lit) release()
  if (!target) return
  const box = target.getBoundingClientRect()
  target.style.setProperty("--v2-mx", `${event.clientX - box.left}px`)
  target.style.setProperty("--v2-my", `${event.clientY - box.top}px`)
  target.setAttribute("data-v2-lit", "")
  lit = target
}

export function installV2Spotlight() {
  document.addEventListener(
    "pointermove",
    (event) => {
      // Touch and pen have no hover; a light that jumps on every tap is noise.
      if (event.pointerType !== "mouse") return
      pending = event
      if (!frame) frame = requestAnimationFrame(update)
    },
    { passive: true },
  )
  document.documentElement.addEventListener("pointerleave", release)
  window.addEventListener("blur", release)
}
