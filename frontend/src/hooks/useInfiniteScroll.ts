import { useEffect, useRef, useCallback } from "react"

export function useInfiniteScroll(
  onLoadMore: () => void,
  hasMore: boolean,
  isLoading: boolean,
  options?: IntersectionObserverInit,
) {
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const callbackRef = useRef(onLoadMore)
  callbackRef.current = onLoadMore

  const observerRef = useRef<IntersectionObserver | null>(null)

  const setSentinel = useCallback(
    (node: HTMLDivElement | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect()
        observerRef.current = null
      }
      sentinelRef.current = node
      if (!node) return
      if (!hasMore || isLoading) return
      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries[0]?.isIntersecting && hasMore && !isLoading) {
            callbackRef.current()
          }
        },
        { rootMargin: "400px", threshold: 0, ...options },
      )
      observerRef.current.observe(node)
    },
    [hasMore, isLoading, options],
  )

  useEffect(() => {
    return () => observerRef.current?.disconnect()
  }, [])

  // re-observe when hasMore/isLoading change while node exists
  useEffect(() => {
    if (!sentinelRef.current) return
    setSentinel(sentinelRef.current)
  }, [hasMore, isLoading, setSentinel])

  return setSentinel
}
