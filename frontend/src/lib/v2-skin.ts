import { useSyncExternalStore } from "react"

/**
 * Selectable designs of the V2 shell. The classic /admin shell is not
 * affected — every skin rule is scoped to html[data-ui-version="v2"].
 *
 * - `edge`    — the "hard edge" language: ink and paper only, zero radius,
 *               monospaced uppercase chrome, inversion as the only
 *               interaction gesture, cut corners on primary actions.
 * - `mission` — a starship mission computer built on the same structure:
 *               navy, cyan instrument lines, teal control bars and orange for
 *               whatever is selected or under the pointer.
 */
export const V2_SKINS = ["edge", "mission"] as const
export type V2Skin = (typeof V2_SKINS)[number]

export const DEFAULT_V2_SKIN: V2Skin = "edge"

export const V2_SKIN_LABELS: Record<V2Skin, string> = {
  edge: "Грань",
  mission: "Миссия",
}

const STORAGE_KEY = "nexusdata-v2-skin"
const listeners = new Set<() => void>()

/** Unknown or stale stored values fall back to the default design. */
export function normalizeV2Skin(value: unknown): V2Skin {
  return typeof value === "string" && (V2_SKINS as readonly string[]).includes(value)
    ? (value as V2Skin)
    : DEFAULT_V2_SKIN
}

/** Only the hard-edge design has light and dark variants. */
export function v2SkinHasLightDark(skin: V2Skin): boolean {
  return skin === "edge"
}

function readStoredSkin(): V2Skin {
  try {
    return normalizeV2Skin(window.localStorage.getItem(STORAGE_KEY))
  } catch {
    return DEFAULT_V2_SKIN
  }
}

let current: V2Skin = typeof window === "undefined" ? DEFAULT_V2_SKIN : readStoredSkin()

/**
 * Called synchronously in main.tsx before React renders: the page has no
 * server-side rendering, so applying the skin any later would repaint the
 * whole screen once in the wrong design.
 */
export function installV2SkinAttribute() {
  document.documentElement.dataset.v2Skin = current
}

export function setV2Skin(skin: V2Skin) {
  current = normalizeV2Skin(skin)
  document.documentElement.dataset.v2Skin = current
  try {
    window.localStorage.setItem(STORAGE_KEY, current)
  } catch {
    // The choice still applies for this visit when storage is unavailable.
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useV2Skin(): [V2Skin, (skin: V2Skin) => void] {
  const skin = useSyncExternalStore(subscribe, () => current, () => DEFAULT_V2_SKIN)
  return [skin, setV2Skin]
}
