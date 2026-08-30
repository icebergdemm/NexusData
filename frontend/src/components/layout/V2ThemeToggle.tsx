import { Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { Button } from "@/components/ui/button"

export function V2ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const isLight = resolvedTheme === "light"
  const label = isLight ? "Включить чёрную тему" : "Включить белую тему"

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      onClick={() => setTheme(isLight ? "dark" : "light")}
    >
      {isLight ? <Moon /> : <Sun />}
    </Button>
  )
}
