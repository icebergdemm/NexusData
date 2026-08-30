import { useState, type ComponentType } from "react"
import {
  Bomb,
  Cable,
  Cpu,
  DoorOpen,
  FlaskConical,
  Fuel,
  Lightbulb,
  Package,
  Radio,
  Shield,
  Siren,
  Syringe,
  Wrench,
  Zap,
  type LucideProps,
} from "lucide-react"
import { itemIconUrl } from "@/lib/items"

function fallbackIcon(identifier: string): ComponentType<LucideProps> {
  const id = String(identifier ?? "").toLocaleLowerCase("en")
  if (/door|hatch|шлюз/.test(id)) return DoorOpen
  if (/wire|cable|connection/.test(id)) return Cable
  if (/lamp|light|neon|glow/.test(id)) return Lightbulb
  if (/syringe|medicine|medical|drug|toxin|blood/.test(id)) return Syringe
  if (/bomb|grenade|explosion|detonator|mine/.test(id)) return Bomb
  if (/fuel|oxygen|tank|canister/.test(id)) return Fuel
  if (/battery|power|generator|coil|electric/.test(id)) return Zap
  if (/radio|sonar|beacon|signal/.test(id)) return Radio
  if (/armor|helmet|suit|shield/.test(id)) return Shield
  if (/alarm|siren|buzzer/.test(id)) return Siren
  if (/component|circuit|terminal|sensor|memory/.test(id)) return Cpu
  if (/chemical|acid|material|sample|extract/.test(id)) return FlaskConical
  if (/tool|wrench|screwdriver|repair/.test(id)) return Wrench
  return Package
}

/** Renders a Barotrauma item's icon (see lib/items.ts), falling back to a
 * generic placeholder if the identifier has no extracted icon or the image
 * 404s — not every item made it through tools/extract_barotrauma_items.py
 * (some are icon-less by design, e.g. pure "container" prefabs). Shared
 * component — any page that shows an item identifier can use this. */
export function ItemIcon({ identifier, size = 28, className, title, hasIcon }: {
  identifier: string
  size?: number
  className?: string
  /** `false` comes from item_catalog.json and skips a guaranteed 404. When
   * omitted, the component probes the URL for modded/new identifiers. */
  hasIcon?: boolean
  /** Defaults to the bare identifier — pass a richer tooltip (name/ID/etc.)
   * from the caller when one is available. */
  title?: string
}) {
  const [loadedIdentifier, setLoadedIdentifier] = useState<string | null>(null)
  const [failedIdentifier, setFailedIdentifier] = useState<string | null>(null)
  const safeIdentifier = String(identifier ?? "").trim()
  const resolvedTitle = (title ?? safeIdentifier) || "Предмет"
  const Fallback = fallbackIcon(`${safeIdentifier} ${resolvedTitle}`)
  const canLoad = Boolean(safeIdentifier && hasIcon !== false && failedIdentifier !== safeIdentifier)
  const isLoaded = canLoad && loadedIdentifier === safeIdentifier

  return (
    <div
      className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded text-muted-foreground ${isLoaded ? "bg-muted/40" : "bg-muted"} ${className ?? ""}`}
      style={{ width: size, height: size }}
      title={resolvedTitle}
      role="img"
      aria-label={resolvedTitle}
      data-item-icon-fallback={isLoaded ? undefined : "true"}
    >
      {/* The fallback is present from the first frame. A slow, missing or
          transparent sprite can therefore never leave an empty icon cell. */}
      <Fallback style={{ width: size * 0.55, height: size * 0.55 }} aria-hidden="true" />
      {canLoad && (
        <img
          key={safeIdentifier}
          src={itemIconUrl(safeIdentifier)}
          alt=""
          aria-hidden="true"
          onLoad={(event) => {
            // Internal emitter/projectile prefabs sometimes export a fully
            // transparent 1×1 or 2×2 PNG. Do not cover the SVG with it.
            if (event.currentTarget.naturalWidth <= 2 || event.currentTarget.naturalHeight <= 2) {
              setFailedIdentifier(safeIdentifier)
              return
            }
            setLoadedIdentifier(safeIdentifier)
          }}
          onError={() => setFailedIdentifier(safeIdentifier)}
          className="absolute inset-0 size-full object-contain"
          style={{ opacity: isLoaded ? 1 : 0 }}
        />
      )}
    </div>
  )
}
