import { useId } from "react"
import { RotateCcw } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  MISSION_ACCENT_PRESETS,
  MISSION_FONTS,
  MISSION_FONT_LABELS,
  MISSION_LINE_PRESETS,
  resetMissionTuning,
  useMissionTuning,
  type MissionFont,
} from "@/lib/v2-mission-tuning"

function ColorField({ label, value, presets, onChange }: {
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

function RangeField({ label, value, min, max, onChange, hint }: {
  label: string
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  hint?: string
}) {
  const id = useId()
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{label}</Label>
        <span className="font-mono text-[10px] tabular-nums opacity-70">{value}%</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full cursor-pointer"
        style={{ accentColor: "var(--v2-hot)" }}
      />
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** Live fine-tuning of the "mission" design; every change applies at once. */
export function V2MissionTuningDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [tuning, setTuning] = useMissionTuning()
  const ringsId = useId()
  const motionId = useId()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Настройка дизайна «Миссия»</DialogTitle>
          <DialogDescription>
            Изменения видны сразу и сохраняются в этом браузере.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          <ColorField
            label="Цвет выделения"
            value={tuning.accent}
            presets={MISSION_ACCENT_PRESETS}
            onChange={(accent) => setTuning({ accent })}
          />
          <ColorField
            label="Цвет линий"
            value={tuning.line}
            presets={MISSION_LINE_PRESETS}
            onChange={(line) => setTuning({ line })}
          />

          <RangeField
            label="Свечение текста"
            value={tuning.glow}
            min={0}
            max={100}
            onChange={(glow) => setTuning({ glow })}
          />
          <RangeField
            label="Подсветка фона"
            value={tuning.backdropGlow}
            min={0}
            max={100}
            onChange={(backdropGlow) => setTuning({ backdropGlow })}
          />
          <RangeField
            label="Линии в таблицах и списках"
            value={tuning.borders}
            min={10}
            max={100}
            onChange={(borders) => setTuning({ borders })}
            hint="Рамки карточек и полей всегда остаются яркими."
          />

          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor={ringsId}>Кольца на фоне</Label>
              <Switch id={ringsId} checked={tuning.rings} onCheckedChange={(rings) => setTuning({ rings })} />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor={motionId}>Вращение колец</Label>
              <Switch
                id={motionId}
                checked={tuning.ringsMotion}
                disabled={!tuning.rings}
                onCheckedChange={(ringsMotion) => setTuning({ ringsMotion })}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label>Шрифт интерфейса</Label>
            <Select value={tuning.font} onValueChange={(font) => setTuning({ font: font as MissionFont })}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {MISSION_FONTS.map((font) => (
                  <SelectItem key={font} value={font}>{MISSION_FONT_LABELS[font]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={resetMissionTuning}><RotateCcw /> Сбросить</Button>
          <Button onClick={() => onOpenChange(false)}>Готово</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
