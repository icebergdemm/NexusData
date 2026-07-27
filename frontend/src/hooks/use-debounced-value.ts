import { useEffect, useState } from "react"

/**
 * Delays propagating a fast-changing value (typically a search input) until
 * it stops changing for `delayMs`.
 *
 * Point of it: a `useQuery` whose queryKey contains the raw input fires one
 * request per keystroke — typing "владимир" meant 8 requests, each running
 * an unindexed full-table scan over the logs, which piled up and timed the
 * backend out entirely. Feed the debounced value into the queryKey instead
 * and keep the raw one bound to the input, so typing stays responsive while
 * only the final value is actually queried.
 */
export function useDebouncedValue<T>(value: T, delayMs = 350): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])

  return debounced
}
