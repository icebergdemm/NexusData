import { clampInt, createPersistedStore, usePersistedStore } from "./persisted-store"

/**
 * Fine-tuning of the "mission" design. Every value is applied as a CSS
 * custom property or data attribute on <html> that only the mission rules in
 * index.css read, so the hard-edge design is unaffected whatever is stored.
 */
export interface MissionTuning {
  /** Selected / hovered colour. */
  accent: string
  /** Instrument lines, frames and rings. */
  line: string
  /** Text glow, 0–100. */
  glow: number
  /** Radial light behind the console, 0–100. */
  backdropGlow: number
  /** How strongly borders inside tables and lists are drawn, 10–100. */
  borders: number
  rings: boolean
  ringsMotion: boolean
  font: MissionFont
}

export const MISSION_FONTS = ["tech", "mono", "sans"] as const
export type MissionFont = (typeof MISSION_FONTS)[number]

export const MISSION_FONT_LABELS: Record<MissionFont, string> = {
  tech: "Технический",
  mono: "Моноширинный",
  sans: "Обычный",
}

export const DEFAULT_MISSION_TUNING: MissionTuning = {
  accent: "#ff7a1f",
  line: "#4ccfee",
  glow: 30,
  backdropGlow: 100,
  borders: 55,
  rings: true,
  ringsMotion: true,
  font: "tech",
}

export const MISSION_ACCENT_PRESETS = ["#ff7a1f", "#ffb020", "#ff4d6d", "#b388ff", "#3ddc97"] as const
export const MISSION_LINE_PRESETS = ["#4ccfee", "#6aa8ff", "#7cf5c8", "#e6e6e6", "#ffcf5a"] as const

const HEX = /^#[0-9a-f]{6}$/i

/** Anything malformed in storage falls back field by field to the defaults. */
export function normalizeMissionTuning(raw: unknown): MissionTuning {
  const value = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}
  const d = DEFAULT_MISSION_TUNING
  return {
    accent: typeof value.accent === "string" && HEX.test(value.accent) ? value.accent.toLowerCase() : d.accent,
    line: typeof value.line === "string" && HEX.test(value.line) ? value.line.toLowerCase() : d.line,
    glow: clampInt(value.glow, 0, 100, d.glow),
    backdropGlow: clampInt(value.backdropGlow, 0, 100, d.backdropGlow),
    borders: clampInt(value.borders, 10, 100, d.borders),
    rings: typeof value.rings === "boolean" ? value.rings : d.rings,
    ringsMotion: typeof value.ringsMotion === "boolean" ? value.ringsMotion : d.ringsMotion,
    font: (MISSION_FONTS as readonly string[]).includes(value.font as string) ? (value.font as MissionFont) : d.font,
  }
}

function apply(tuning: MissionTuning) {
  const root = document.documentElement
  root.style.setProperty("--v2-mission-accent", tuning.accent)
  root.style.setProperty("--v2-mission-line", tuning.line)
  root.style.setProperty("--v2-mission-glow", String(tuning.glow / 100))
  root.style.setProperty("--v2-mission-backdrop", String(tuning.backdropGlow / 100))
  root.style.setProperty("--v2-mission-borders", String(tuning.borders / 100))
  root.dataset.v2MissionFont = tuning.font
}

const store = createPersistedStore("nexusdata-v2-mission-tuning", DEFAULT_MISSION_TUNING, normalizeMissionTuning, apply)

/** Called in main.tsx before React renders, for the same reason as the skin. */
export const installMissionTuning = store.install
export const setMissionTuning = store.set
export const resetMissionTuning = store.reset

export function useMissionTuning(): [MissionTuning, (next: Partial<MissionTuning>) => void] {
  return usePersistedStore(store)
}
