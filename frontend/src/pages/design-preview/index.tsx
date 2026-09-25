import { useState } from "react"
import { toast } from "sonner"
import { useTheme } from "next-themes"
import { MoreHorizontal, Palette, Plus, Radio, Send, ShieldAlert, Trash2 } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { V2_SKINS, V2_SKIN_LABELS, useV2Skin, v2SkinHasLightDark } from "@/lib/v2-skin"

const PLAYERS = [
  { name: "Снегирь-2", role: "Капитан", server: "ASPER1", status: "online" },
  { name: "Тайфун", role: "Механик", server: "ASPER1", status: "in_game" },
  { name: "Гарпун", role: "Врач", server: "ASPER2", status: "offline" },
  { name: "Мурена", role: "Инженер", server: "ASPER2", status: "banned" },
] as const

function StatusBadge({ status }: { status: (typeof PLAYERS)[number]["status"] }) {
  if (status === "banned") return <Badge variant="destructive">Бан</Badge>
  if (status === "offline") return <Badge variant="outline">Оффлайн</Badge>
  if (status === "in_game") return <Badge variant="secondary">В игре</Badge>
  return <Badge>Онлайн</Badge>
}

/**
 * Development-only living style sheet of the V2 shell: the real components in
 * every state on mock data, inside the real sidebar and header. Opened with
 * `npm run dev` at /v2/design-preview — no backend and no login needed.
 */
export function DesignPreviewPage() {
  const [skin, setSkin] = useV2Skin()
  const { resolvedTheme, setTheme } = useTheme()
  const [pressed, setPressed] = useState(2)
  const [notify, setNotify] = useState(true)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Дизайн интерфейса"
        description="Все компоненты V2 во всех состояниях. Бэкенд не нужен."
        icon={Palette}
        actions={
          <>
            {V2_SKINS.map((option) => (
              <Button key={option} variant="outline" size="sm" aria-pressed={skin === option} onClick={() => setSkin(option)}>
                {V2_SKIN_LABELS[option]}
              </Button>
            ))}
            {v2SkinHasLightDark(skin) && (
              <Button variant="outline" size="sm" onClick={() => setTheme(resolvedTheme === "light" ? "dark" : "light")}>
                {resolvedTheme === "light" ? "Тёмная" : "Светлая"} тема
              </Button>
            )}
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Онлайн", "142", "за последний час"],
          ["Раунды", "38", "сегодня"],
          ["Тикеты", "7", "ждут ответа"],
          ["Наказания", "3", "за сутки"],
        ].map(([title, value, hint]) => (
          <Card key={title}>
            <CardHeader>
              <CardDescription>{hint}</CardDescription>
              <CardTitle>{title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold tabular-nums">{value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Кнопки</CardTitle>
          <CardDescription>Главное действие — со срезанными углами; остальные — контур, который инвертируется при наведении.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Button><Send /> Отправить</Button>
            <Button variant="outline">Контур</Button>
            <Button variant="secondary">Вторичная</Button>
            <Button variant="ghost">Прозрачная</Button>
            <Button variant="destructive"><Trash2 /> Удалить</Button>
            <Button variant="link">Ссылка</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="xs">xs</Button>
            <Button size="sm">sm</Button>
            <Button>default</Button>
            <Button size="lg">lg</Button>
            <Button size="icon" aria-label="Добавить"><Plus /></Button>
            <Button size="icon-sm" variant="outline" aria-label="Ещё"><MoreHorizontal /></Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <Button key={n} variant="outline" className="min-w-12" aria-pressed={pressed === n} onClick={() => setPressed(n)}>
                {n}
              </Button>
            ))}
            <Button disabled>Недоступно</Button>
            <Button variant="outline" disabled>Недоступно</Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Форма</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="preview-callsign">Позывной</Label>
              <Input id="preview-callsign" placeholder="Например, Снегирь-2" />
            </div>
            <div className="space-y-2">
              <Label>Сервер</Label>
              <Select defaultValue="ASPER1">
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ASPER1">ASPER1</SelectItem>
                  <SelectItem value="ASPER2">ASPER2</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="preview-note">Сообщение</Label>
              <Textarea id="preview-note" placeholder="Диспетчер, доложите обстановку…" />
            </div>
            <div className="flex flex-wrap items-center gap-6">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={notify} onCheckedChange={setNotify} /> Уведомлять
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox defaultChecked /> Запомнить сервер
              </label>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => toast.success("Настройки сохранены")}>Сохранить</Button>
              <Button variant="outline" onClick={() => toast.error("Сервер недоступен")}>Показать ошибку</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Вкладки и меню</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Tabs defaultValue="radio">
              <TabsList>
                <TabsTrigger value="radio"><Radio /> Радиосвязь</TabsTrigger>
                <TabsTrigger value="warehouse">Склад</TabsTrigger>
                <TabsTrigger value="log">Журнал</TabsTrigger>
              </TabsList>
              <TabsContent value="radio" className="pt-3 text-sm text-muted-foreground">
                Канал ASPER1 на связи. Последнее сообщение 20:01.
              </TabsContent>
              <TabsContent value="warehouse" className="pt-3 text-sm text-muted-foreground">
                На складе 24 позиции, 3 бесконечные.
              </TabsContent>
              <TabsContent value="log" className="pt-3 text-sm text-muted-foreground">
                Журнал пуст.
              </TabsContent>
            </Tabs>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline"><ShieldAlert /> Действия с игроком</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-56">
                <DropdownMenuLabel>Модерация</DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem>Написать сообщение</DropdownMenuItem>
                <DropdownMenuItem>Кикнуть</DropdownMenuItem>
                <DropdownMenuItem variant="destructive">Забанить</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <div className="flex flex-wrap gap-2">
              <Badge>Онлайн</Badge>
              <Badge variant="secondary">В игре</Badge>
              <Badge variant="outline">Оффлайн</Badge>
              <Badge variant="destructive">Бан</Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Таблица</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Игрок</TableHead>
                <TableHead>Роль</TableHead>
                <TableHead>Сервер</TableHead>
                <TableHead>Статус</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {PLAYERS.map((player) => (
                <TableRow key={player.name}>
                  <TableCell className="font-medium">{player.name}</TableCell>
                  <TableCell>{player.role}</TableCell>
                  <TableCell className="font-mono text-xs">{player.server}</TableCell>
                  <TableCell><StatusBadge status={player.status} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Загрузка</CardTitle>
          <CardDescription>Вместо пульсирующих серых блоков — штриховка и бегущая линия.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-32" />
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-2/3" />
          </div>
          <Skeleton className="h-32" />
        </CardContent>
      </Card>
    </div>
  )
}
