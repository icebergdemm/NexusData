import { clampInt, createPersistedStore, usePersistedStore } from "./persisted-store"

/**
 * Fine-tuning of the hard-edge design. Applied as CSS custom properties and a
 * data attribute on <html>; the mission design sets its own line colour and
 * ignores these.
 */
export interface EdgeTuning {
  /** Brightness of frames and rules, as a share of ink, 15–100. */
  lines: number
  /** Light that follows the pointer across a card's frame. */
  spotlight: boolean
  /** Radius of that light, in pixels, 120–600. */
  spotlightSize: number
  /** Brightness of that light, 10–100. */
  spotlightStrength: number
}

/*
  Pure ink at 2px on pure paper is what DESIGN.md specifies, and on a dark
  screen full of cards it glares. 40% keeps every frame legible while the
  light under the pointer brings the one being worked on back to full ink.
*/
export const DEFAULT_EDGE_TUNING: EdgeTuning = {
  lines: 40,
  spotlight: true,
  spotlightSize: 260,
  spotlightStrength: 80,
}

export function normalizeEdgeTuning(raw: unknown): EdgeTuning {
  const value = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const d = DEFAULT_EDGE_TUNING
  return {
    lines: clampInt(value.lines, 15, 100, d.lines),
    spotlight: typeof value.spotlight === "boolean" ? value.spotlight : d.spotlight,
    spotlightSize: clampInt(value.spotlightSize, 120, 600, d.spotlightSize),
    spotlightStrength: clampInt(value.spotlightStrength, 10, 100, d.spotlightStrength),
  }
}

function apply(tuning: EdgeTuning) {
  const root = document.documentElement
  root.style.setProperty("--v2-edge-lines", String(tuning.lines / 100))
  root.style.setProperty("--v2-spot-size", `${tuning.spotlightSize}px`)
  root.style.setProperty("--v2-spot-strength", String(tuning.spotlightStrength / 100))
  root.dataset.v2Spotlight = tuning.spotlight ? "on" : "off"
}

const store = createPersistedStore("nexusdata-v2-edge-tuning", DEFAULT_EDGE_TUNING, normalizeEdgeTuning, apply)

export const installEdgeTuning = store.install
export const resetEdgeTuning = store.reset

export function useEdgeTuning(): [EdgeTuning, (next: Partial<EdgeTuning>) => void] {
  return usePersistedStore(store)
}
