import type { ComponentType } from "react"
import {
  Award,
  BookOpen,
  Bot,
  BriefcaseBusiness,
  Building2,
  Camera,
  ChartNoAxesColumn,
  Check,
  Circle,
  Clapperboard,
  ClipboardList,
  Coins,
  Compass,
  FileText,
  Flame,
  FlaskConical,
  FolderOpen,
  Gift,
  GraduationCap,
  Lightbulb,
  LockKeyhole,
  Megaphone,
  Medal,
  MessageSquare,
  NotebookPen,
  Package,
  Pencil,
  Pin,
  Play,
  Radio,
  RefreshCw,
  Rocket,
  Save,
  ScrollText,
  Settings,
  Ship,
  Skull,
  Sparkles,
  Star,
  Stethoscope,
  Tag,
  Target,
  Ticket,
  Timer,
  Trash2,
  TriangleAlert,
  Trophy,
  Users,
  Volume2,
  VolumeX,
  Waves,
  Wrench,
  Zap,
  type LucideProps,
} from "lucide-react"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { IS_V2 } from "@/lib/ui-version"
import { cn } from "@/lib/utils"

type IconComponent = ComponentType<LucideProps>

const ICONS: Record<string, IconComponent> = {
  "🏆": Trophy,
  "🥇": Medal,
  "🥈": Medal,
  "🥉": Medal,
  "🎖": Award,
  "🎖️": Award,
  "🏅": Medal,
  "⭐": Star,
  "💀": Skull,
  "🔧": Wrench,
  "⚡": Zap,
  "🩺": Stethoscope,
  "🐙": Waves,
  "🚢": Ship,
  "🔥": Flame,
  "💰": Coins,
  "🧭": Compass,
  "🧪": FlaskConical,
  "🎯": Target,
  "📄": FileText,
  "📝": NotebookPen,
  "📋": ClipboardList,
  "📌": Pin,
  "📖": BookOpen,
  "🗂": FolderOpen,
  "🗂️": FolderOpen,
  "💡": Lightbulb,
  "⚙": Settings,
  "⚙️": Settings,
  "🚀": Rocket,
  "✅": Check,
  "📊": ChartNoAxesColumn,
  "🔒": LockKeyhole,
  "📢": Megaphone,
  "🏷": Tag,
  "🏷️": Tag,
  "🎫": Ticket,
  "📦": Package,
  "🎁": Gift,
  "🎓": GraduationCap,
  "✨": Sparkles,
  "🎭": BriefcaseBusiness,
  "⏱": Timer,
  "⏱️": Timer,
  "📜": ScrollText,
  "🎬": Clapperboard,
  "📷": Camera,
  "🔊": Volume2,
  "👥": Users,
  "🔇": VolumeX,
  "💬": MessageSquare,
  "⚠": TriangleAlert,
  "⚠️": TriangleAlert,
  "🗑": Trash2,
  "🗑️": Trash2,
  "✏": Pencil,
  "✏️": Pencil,
  "💾": Save,
  "🔄": RefreshCw,
  "▶": Play,
  "🟢": Radio,
  "🟡": Circle,
  "⚪": Circle,
  "🏢": Building2,
  "🤖": Bot,
}

const PLATFORM_ICON_OPTIONS = [
  { value: "📋", label: "Задачи", icon: ClipboardList },
  { value: "🏢", label: "Отдел", icon: Building2 },
  { value: "🩺", label: "Медицина", icon: Stethoscope },
  { value: "🚢", label: "Судно", icon: Ship },
  { value: "🏷", label: "Метка", icon: Tag },
  { value: "🏆", label: "Награда", icon: Trophy },
  { value: "⭐", label: "Избранное", icon: Star },
  { value: "🎯", label: "Цель", icon: Target },
  { value: "📄", label: "Документ", icon: FileText },
  { value: "📝", label: "Запись", icon: NotebookPen },
  { value: "📖", label: "Справочник", icon: BookOpen },
  { value: "🗂️", label: "Каталог", icon: FolderOpen },
  { value: "📌", label: "Закреплено", icon: Pin },
  { value: "💡", label: "Идея", icon: Lightbulb },
  { value: "⚙️", label: "Настройки", icon: Settings },
  { value: "📊", label: "Аналитика", icon: ChartNoAxesColumn },
  { value: "🔒", label: "Закрыто", icon: LockKeyhole },
  { value: "📢", label: "Объявление", icon: Megaphone },
  { value: "📦", label: "Снабжение", icon: Package },
  { value: "🧭", label: "Навигация", icon: Compass },
] as const

function iconFor(value: string | null | undefined, fallback: IconComponent) {
  return (value ? ICONS[value] : undefined) ?? fallback
}

export function PlatformIcon({
  value,
  fallback: Fallback = Circle,
  className,
  label,
}: {
  value?: string | null
  fallback?: IconComponent
  className?: string
  label?: string
}) {
  if (!IS_V2 && value) return <span aria-hidden="true">{value}</span>
  const Icon = iconFor(value, Fallback)
  return <Icon className={cn("size-4 shrink-0", className)} aria-hidden={label ? undefined : true} aria-label={label} />
}

export function AdaptiveIcon({
  emoji,
  icon: Icon,
  className,
}: {
  emoji: string
  icon: IconComponent
  className?: string
}) {
  if (!IS_V2) return <span aria-hidden="true">{emoji}</span>
  return <Icon className={cn("size-4 shrink-0", className)} aria-hidden="true" />
}

export function PlatformIconField({
  value,
  onChange,
  className,
  v2ClassName,
  placeholder,
  disabled = false,
}: {
  value: string
  onChange: (value: string) => void
  className?: string
  v2ClassName?: string
  placeholder?: string
  disabled?: boolean
}) {
  if (!IS_V2) {
    return (
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={className}
        placeholder={placeholder}
        maxLength={8}
        disabled={disabled}
      />
    )
  }

  const selected = PLATFORM_ICON_OPTIONS.some((option) => option.value === value) ? value : PLATFORM_ICON_OPTIONS[0].value
  return (
    <Select value={selected} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger className={v2ClassName ?? className} aria-label="Иконка">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {PLATFORM_ICON_OPTIONS.map((option) => {
          const Icon = option.icon
          return (
            <SelectItem key={option.value} value={option.value}>
              <span className="flex items-center gap-2"><Icon className="size-4" />{option.label}</span>
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}
