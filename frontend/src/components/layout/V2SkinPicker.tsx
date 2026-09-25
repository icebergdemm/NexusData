import { useState } from "react"
import { Palette, SlidersHorizontal } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { V2_SKINS, V2_SKIN_LABELS, normalizeV2Skin, useV2Skin } from "@/lib/v2-skin"
import { V2MissionTuningDialog } from "@/components/layout/V2MissionTuningDialog"
import { V2EdgeTuningDialog } from "@/components/layout/V2EdgeTuningDialog"

const DESCRIPTIONS = {
  edge: "Чёрное и белое, жёсткие грани",
  mission: "Консоль корабля: синий, бирюза, оранжевый",
} as const

/** Lets every operator pick their own design; the choice stays in this browser. */
export function V2SkinPicker() {
  const [skin, setSkin] = useV2Skin()
  const [tuningOpen, setTuningOpen] = useState(false)

  return (
    <>
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`Дизайн интерфейса: ${V2_SKIN_LABELS[skin]}`}
          title="Дизайн интерфейса"
        >
          <Palette />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>Дизайн интерфейса</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={skin} onValueChange={(value) => setSkin(normalizeV2Skin(value))}>
          {V2_SKINS.map((option) => (
            <DropdownMenuRadioItem key={option} value={option} className="flex-col items-start gap-0.5">
              <span>{V2_SKIN_LABELS[option]}</span>
              <span className="text-[10px] opacity-60">{DESCRIPTIONS[option]}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => setTuningOpen(true)}>
          <SlidersHorizontal /> Настроить «{V2_SKIN_LABELS[skin]}»…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    {skin === "mission"
      ? <V2MissionTuningDialog open={tuningOpen} onOpenChange={setTuningOpen} />
      : <V2EdgeTuningDialog open={tuningOpen} onOpenChange={setTuningOpen} />}
    </>
  )
}
