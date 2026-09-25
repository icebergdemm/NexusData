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
import { ColorField, RangeField } from "@/components/layout/V2TuningFields"
import {
  MISSION_ACCENT_PRESETS,
  MISSION_FONTS,
  MISSION_FONT_LABELS,
  MISSION_LINE_PRESETS,
  resetMissionTuning,
  useMissionTuning,
  type MissionFont,
} from "@/lib/v2-mission-tuning"

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
