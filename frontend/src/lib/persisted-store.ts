import { useSyncExternalStore } from "react"

/** A stored number, rounded and kept within range; anything else is `fallback`. */
export function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const number = typeof value === "number" ? value : Number.NaN
  return Number.isFinite(number) ? Math.round(Math.min(max, Math.max(min, number))) : fallback
}

export interface PersistedStore<T> {
  /** Applies the stored value; call before React renders to avoid a repaint. */
  install: () => void
  get: () => T
  set: (next: Partial<T>) => void
  reset: () => void
  subscribe: (listener: () => void) => () => void
}

/**
 * A small localStorage-backed settings object for the design tuning panels.
 * `normalize` is the only way in, so a stale or hand-edited value can never
 * reach `apply` — and through it the page's CSS.
 */
export function createPersistedStore<T extends object>(
  key: string,
  defaults: T,
  normalize: (raw: unknown) => T,
  apply: (value: T) => void,
): PersistedStore<T> {
  const listeners = new Set<() => void>()

  let current: T = defaults
  try {
    const raw = window.localStorage.getItem(key)
    current = normalize(raw ? JSON.parse(raw) : null)
  } catch {
    // No storage (tests, private mode) or unreadable JSON: keep the defaults.
  }

  function set(next: Partial<T>) {
    current = normalize({ ...current, ...next })
    apply(current)
    try {
      window.localStorage.setItem(key, JSON.stringify(current))
    } catch {
      // Still applies for this visit when storage is unavailable.
    }
    listeners.forEach((listener) => listener())
  }

  return {
    install: () => apply(current),
    get: () => current,
    set,
    reset: () => set(defaults),
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

export function usePersistedStore<T extends object>(store: PersistedStore<T>): [T, (next: Partial<T>) => void] {
  const value = useSyncExternalStore(store.subscribe, store.get, store.get)
  return [value, store.set]
}
