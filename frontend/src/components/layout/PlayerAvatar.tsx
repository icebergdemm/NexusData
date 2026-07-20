import { useState } from "react"

/** Steam avatar with graceful fallback to initials when missing/broken/loading. */
export function PlayerAvatar({ url, name, size = 40, online }: {
  url: string | null | undefined
  name: string
  size?: number
  online?: boolean
}) {
  const [failed, setFailed] = useState(false)
  const initials = (name || "?").slice(0, 2).toUpperCase()

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {url && !failed ? (
        <img
          src={url}
          alt=""
          onError={() => setFailed(true)}
          className="h-full w-full rounded-full object-cover"
          style={{ width: size, height: size }}
        />
      ) : (
        <div
          className="flex h-full w-full items-center justify-center rounded-full bg-primary/20 font-bold text-primary"
          style={{ fontSize: Math.max(10, size * 0.36) }}
        >
          {initials}
        </div>
      )}
      {online && (
        <span
          className="absolute right-0 bottom-0 rounded-full border-2 border-card bg-success"
          style={{ width: Math.max(8, size * 0.28), height: Math.max(8, size * 0.28) }}
          title="В сети"
        />
      )}
    </div>
  )
}
