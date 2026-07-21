import { useState } from "react"
import { Package } from "lucide-react"
import { itemIconUrl } from "@/lib/items"

/** Renders a Barotrauma item's icon (see lib/items.ts), falling back to a
 * generic placeholder if the identifier has no extracted icon or the image
 * 404s — not every item made it through tools/extract_barotrauma_items.py
 * (some are icon-less by design, e.g. pure "container" prefabs). Shared
 * component — any page that shows an item identifier can use this. */
export function ItemIcon({ identifier, size = 28, className, title }: {
  identifier: string
  size?: number
  className?: string
  /** Defaults to the bare identifier — pass a richer tooltip (name/ID/etc.)
   * from the caller when one is available. */
  title?: string
}) {
  const [failed, setFailed] = useState(false)
  const resolvedTitle = title ?? identifier

  if (failed) {
    return (
      <div
        className={`flex shrink-0 items-center justify-center rounded bg-muted text-muted-foreground ${className ?? ""}`}
        style={{ width: size, height: size }}
        title={resolvedTitle}
      >
        <Package style={{ width: size * 0.55, height: size * 0.55 }} />
      </div>
    )
  }

  return (
    <img
      src={itemIconUrl(identifier)}
      alt={identifier}
      title={resolvedTitle}
      onError={() => setFailed(true)}
      className={`shrink-0 rounded bg-muted/40 object-contain ${className ?? ""}`}
      style={{ width: size, height: size }}
    />
  )
}
