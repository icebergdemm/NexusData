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
import { Switch } from "@/components/ui/switch"
import { RangeField } from "@/components/layout/V2TuningFields"
import { resetEdgeTuning, useEdgeTuning } from "@/lib/v2-edge-tuning"

/** Live fine-tuning of the hard-edge design; every change applies at once. */
export function V2EdgeTuningDialog({ open, onOpenChange }: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [tuning, setTuning] = useEdgeTuning()
  const spotlightId = useId()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Настройка дизайна «Грань»</DialogTitle>
          <DialogDescription>
            Изменения видны сразу и сохраняются в этом браузере.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          <RangeField
            label="Яркость рамок"
            value={tuning.lines}
            min={15}
            max={100}
            onChange={(lines) => setTuning({ lines })}
            hint="100% — чистый белый (в светлой теме — чёрный), как в исходном макете."
          />

          <div className="flex items-center justify-between gap-3">
            <Label htmlFor={spotlightId}>Подсветка под курсором</Label>
            <Switch
              id={spotlightId}
              checked={tuning.spotlight}
              onCheckedChange={(spotlight) => setTuning({ spotlight })}
            />
          </div>
          <RangeField
            label="Радиус подсветки"
            value={tuning.spotlightSize}
            min={120}
            max={600}
            step={10}
            unit="px"
            onChange={(spotlightSize) => setTuning({ spotlightSize })}
          />
          <RangeField
            label="Сила подсветки"
            value={tuning.spotlightStrength}
            min={10}
            max={100}
            onChange={(spotlightStrength) => setTuning({ spotlightStrength })}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={resetEdgeTuning}><RotateCcw /> Сбросить</Button>
          <Button onClick={() => onOpenChange(false)}>Готово</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
