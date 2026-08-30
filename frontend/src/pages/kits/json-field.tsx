import { useState } from "react"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"
import { Wrench } from "lucide-react"

/** JSON textarea with live validation feedback + a format button — mirrors the
 * original kits.html's validateJson()/formatJson() vanilla-JS behavior.
 * `allowEmpty` treats a blank value as valid (unset) rather than an error —
 * used for the optional per-variation items fields (item 15), where empty
 * means "fall back to variation 1" rather than invalid JSON. */
export function JsonField({ value, onChange, rows = 4, allowEmpty = false }: { value: string; onChange: (v: string) => void; rows?: number; allowEmpty?: boolean }) {
  const [error, setError] = useState<string | null>(null)

  const handleChange = (v: string) => {
    onChange(v)
    if (allowEmpty && !v.trim()) {
      setError(null)
      return
    }
    try {
      JSON.parse(v)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const format = () => {
    try {
      onChange(JSON.stringify(JSON.parse(value), null, 2))
      setError(null)
    } catch {
      // leave as-is if invalid
    }
  }

  const isEmpty = allowEmpty && !value.trim()

  return (
    <div className="space-y-1">
      <Textarea value={value} onChange={(e) => handleChange(e.target.value)} rows={rows} className="font-mono text-xs" />
      <div className="flex items-center justify-between">
        <span className={`text-[11px] font-semibold ${isEmpty ? "text-muted-foreground" : error ? "text-destructive" : "text-success"}`}>
          {isEmpty ? "— не задано (используется вариация 1)" : error ? `✗ Ошибка: ${error}` : "✓ JSON корректен"}
        </span>
        <Button type="button" size="sm" variant="ghost" className="h-6 text-xs" onClick={format}><AdaptiveIcon emoji="🔧" icon={Wrench} /> Форматировать</Button>
      </div>
    </div>
  )
}
