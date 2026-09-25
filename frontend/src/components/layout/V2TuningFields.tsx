import { useId } from "react"
import { Label } from "@/components/ui/label"

/* Shared controls of the design tuning panels. */

export function ColorField({ label, value, presets, onChange }: {
  label: string
  value: string
  presets: readonly string[]
  onChange: (value: string) => void
}) {
  const id = useId()
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-2">
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            className="size-7 border-2 transition-transform hover:scale-110"
            style={{ backgroundColor: preset, borderColor: value === preset ? "var(--v2-ink)" : "transparent" }}
            aria-label={`${label}: ${preset}`}
            aria-pressed={value === preset}
            onClick={() => onChange(preset)}
          />
        ))}
        <input
          id={id}
          type="color"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="h-7 w-10 cursor-pointer border-0 bg-transparent p-0"
          title="Свой цвет"
        />
        <span className="font-mono text-[10px] uppercase opacity-60">{value}</span>
      </div>
    </div>
  )
}

export function RangeField({ label, value, min, max, onChange, hint, unit = "%", step = 1 }: {
  label: string
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  hint?: string
  unit?: string
  step?: number
}) {
  const id = useId()
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{label}</Label>
        <span className="font-mono text-[10px] tabular-nums opacity-70">{value}{unit}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full cursor-pointer"
        style={{ accentColor: "var(--v2-hot)" }}
      />
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  )
}
