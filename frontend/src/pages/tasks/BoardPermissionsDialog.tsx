import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CheckCircle2,
  Eye,
  Info,
  Layers3,
  LockKeyhole,
  Pencil,
  Plus,
  Shield,
  Trash2,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/layout/SearchInput";
import { api } from "@/lib/api";
import { tasksApi, type BoardPermission } from "./api";

const permissions = [
  {
    key: "can_view" as const,
    label: "Просмотр",
    short: "Видеть",
    icon: Eye,
    tone: "text-sky-600",
    description: "Открывать доску и читать задачи",
  },
  {
    key: "can_create" as const,
    label: "Создание",
    short: "Создавать",
    icon: Plus,
    tone: "text-emerald-600",
    description: "Добавлять новые задачи",
  },
  {
    key: "can_edit" as const,
    label: "Изменение",
    short: "Изменять",
    icon: Pencil,
    tone: "text-amber-600",
    description: "Редактировать задачи и колонки",
  },
  {
    key: "can_delete" as const,
    label: "Удаление",
    short: "Удалять",
    icon: Trash2,
    tone: "text-rose-600",
    description: "Удалять задачи и содержимое",
  },
];
const grantTypes = [
  { value: "user", label: "Пользователь", icon: UserRound },
  { value: "group_nd", label: "Группа ND", icon: Users },
  { value: "dept_level", label: "Уровень отдела", icon: Layers3 },
] as const;
const presets = [
  { label: "Только просмотр", values: [true, false, false, false] },
  { label: "Участник", values: [true, true, false, false] },
  { label: "Редактор", values: [true, true, true, false] },
  { label: "Полный доступ", values: [true, true, true, true] },
];

export function BoardPermissionsDialog({
  boardId,
  open,
  onOpenChange,
}: {
  boardId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { data: permsData } = useQuery({
    queryKey: ["board-perms", boardId],
    queryFn: () => tasksApi.boards.permissions.get(boardId),
    enabled: open,
  });
  const { data: candidates } = useQuery({
    queryKey: ["tasks-assignee-candidates"],
    queryFn: () => tasksApi.assigneeCandidates(),
    enabled: open,
  });
  const { data: groupsData } = useQuery({
    queryKey: ["admin-groups"],
    queryFn: () =>
      api.get<{ groups: { id: number; name: string }[] }>(
        "/api/v1/admin/users",
      ),
    enabled: open,
  });
  const [rows, setRows] = useState<BoardPermission[]>([]);
  const [tab, setTab] = useState("rules");
  const [type, setType] = useState<BoardPermission["grantee_type"]>("user");
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [deptId, setDeptId] = useState<number | null>(null);
  const [levelQuery, setLevelQuery] = useState("");
  const [view, setView] = useState(true);
  const [create, setCreate] = useState(false);
  const [edit, setEdit] = useState(false);
  const [del, setDel] = useState(false);
  useEffect(() => {
    if (permsData) setRows(permsData.permissions);
  }, [permsData]);
  const { data: deptDetail } = useQuery({
    queryKey: ["dept-levels", deptId],
    queryFn: () =>
      api.get<{ access_levels: { key: string; name: string; kind: string }[] }>(
        `/api/v1/admin/departments/${deptId}`,
      ),
    enabled: !!deptId && open,
  });
  const save = useMutation({
    mutationFn: () => tasksApi.boards.permissions.set(boardId, rows),
    onSuccess: () => {
      toast.success("Права сохранены");
      queryClient.invalidateQueries({ queryKey: ["board-perms", boardId] });
      onOpenChange(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const list = (items: { id: number; name: string | null }[]) =>
    items
      .filter(
        (x) =>
          !query.trim() ||
          `${x.name} ${x.id}`.toLowerCase().includes(query.toLowerCase()),
      )
      .slice(0, 8);
  const add = () => {
    const key = type === "dept_level" ? selected : selected;
    if (!key) {
      toast.error("Выберите получателя доступа");
      return;
    }
    if (rows.some((r) => r.grantee_type === type && r.grantee_key === key)) {
      toast.error("Это правило уже добавлено");
      return;
    }
    setRows([
      ...rows,
      {
        grantee_type: type,
        grantee_key: key,
        can_view: view,
        can_create: create,
        can_edit: edit,
        can_delete: del,
      },
    ]);
    setSelected("");
    setView(true);
    setCreate(false);
    setEdit(false);
    setDel(false);
    setTab("rules");
  };
  const label = (r: BoardPermission) => {
    if (r.grantee_type === "user")
      return (
        candidates?.admins.find((a) => String(a.id) === r.grantee_key)?.name ??
        `Пользователь #${r.grantee_key}`
      );
    if (r.grantee_type === "group_nd")
      return (
        groupsData?.groups.find((g) => String(g.id) === r.grantee_key)?.name ??
        `Группа #${r.grantee_key}`
      );
    const [d, l] = r.grantee_key.split(":");
    return `${candidates?.departments.find((x) => String(x.id) === d)?.name ?? `Отдел #${d}`} · ${l}`;
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-hidden p-0">
        <DialogHeader className="border-b bg-gradient-to-br from-primary/10 via-background to-background px-6 py-5">
          <div className="flex items-start gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary/15 text-primary">
              <LockKeyhole className="size-5" />
            </div>
            <div>
              <DialogTitle className="text-lg">Настройки доступа</DialogTitle>
              <DialogDescription className="mt-1">
                Раздельно настройте, кто видит доску и что может на ней делать.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <Tabs
          value={tab}
          onValueChange={setTab}
          className="flex min-h-0 flex-1 flex-col"
        >
          <TabsList className="mx-6 mt-4 grid w-auto grid-cols-2">
            <TabsTrigger value="rules">
              Правила доступа{" "}
              <Badge variant="secondary" className="ml-1.5">
                {rows.length}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="add">
              <Plus className="mr-1.5 size-3.5" />
              Добавить правило
            </TabsTrigger>
          </TabsList>
          <TabsContent
            value="rules"
            className="mt-0 min-h-0 flex-1 overflow-y-auto px-6 pb-5 pt-4"
          >
            <div className="mb-4 flex gap-2 rounded-lg border border-sky-200 bg-sky-50/70 p-3 text-xs text-sky-900 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-100">
              <Info className="mt-0.5 size-4 shrink-0" />
              <span>
                Если правил нет, используются глобальные права. Более конкретное
                правило имеет приоритет.
              </span>
            </div>
            <div className="space-y-2">
              {rows.map((r, i) => (
                <div
                  key={`${r.grantee_type}-${r.grantee_key}`}
                  className="rounded-xl border bg-card p-3"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex size-9 items-center justify-center rounded-lg bg-muted">
                      <Shield className="size-4 text-muted-foreground" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {label(r)}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {
                          grantTypes.find((t) => t.value === r.grantee_type)
                            ?.label
                        }{" "}
                        · {permissions.filter((p) => r[p.key]).length} из 4
                        разрешений
                      </div>
                    </div>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      className="text-muted-foreground hover:text-destructive"
                      onClick={() => setRows(rows.filter((_, x) => x !== i))}
                    >
                      <X className="size-4" />
                    </Button>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                    {permissions.map((p) => {
                      const Icon = p.icon;
                      return (
                        <label
                          key={p.key}
                          title={p.description}
                          className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 text-xs ${r[p.key] ? "border-primary/30 bg-primary/5" : "bg-muted/20 opacity-70"}`}
                        >
                          <Checkbox
                            checked={r[p.key]}
                            onCheckedChange={(checked) =>
                              setRows(
                                rows.map((x, y) =>
                                  y === i
                                    ? { ...x, [p.key]: checked === true }
                                    : x,
                                ),
                              )
                            }
                          />
                          <Icon
                            className={`size-3.5 ${r[p.key] ? p.tone : "text-muted-foreground"}`}
                          />
                          <span>{p.short}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}
              {!rows.length && (
                <div className="rounded-xl border border-dashed py-12 text-center">
                  <Shield className="mx-auto mb-2 size-8 text-muted-foreground/50" />
                  <p className="text-sm font-medium">
                    Дополнительных правил пока нет
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Добавьте первое правило доступа для этой доски.
                  </p>
                  <Button
                    size="sm"
                    className="mt-4"
                    onClick={() => setTab("add")}
                  >
                    <Plus className="mr-1.5 size-3.5" />
                    Добавить правило
                  </Button>
                </div>
              )}
            </div>
          </TabsContent>
          <TabsContent
            value="add"
            className="mt-0 min-h-0 flex-1 overflow-y-auto px-6 pb-5 pt-4"
          >
            <div className="space-y-4">
              <div>
                <Label className="mb-2 block text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Кому выдать доступ
                </Label>
                <div className="grid grid-cols-3 gap-2">
                  {grantTypes.map((t) => {
                    const Icon = t.icon;
                    return (
                      <button
                        key={t.value}
                        type="button"
                        onClick={() => {
                          setType(t.value);
                          setSelected("");
                        }}
                        className={`flex flex-col items-center gap-1.5 rounded-xl border p-3 text-xs transition-all ${type === t.value ? "border-primary bg-primary/5 text-primary ring-1 ring-primary/30" : "hover:border-primary/40 hover:bg-muted/50"}`}
                      >
                        <Icon className="size-5" />
                        <span>{t.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="rounded-xl border bg-muted/20 p-3">
                {type !== "dept_level" && (
                  <>
                    <SearchInput
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder={
                        type === "user"
                          ? "Поиск по имени или ID..."
                          : "Поиск группы..."
                      }
                      className="h-9"
                    />
                    <div className="mt-2 max-h-36 overflow-y-auto rounded-lg border bg-background">
                      {(type === "user"
                        ? list(candidates?.admins ?? [])
                        : list(groupsData?.groups ?? [])
                      ).map((x) => (
                        <button
                          key={x.id}
                          type="button"
                          onClick={() => setSelected(String(x.id))}
                          className={`flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-accent ${selected === String(x.id) ? "bg-primary/10" : ""}`}
                        >
                          {x.name ?? `#${x.id}`}
                          {selected === String(x.id) && (
                            <CheckCircle2 className="size-4 text-primary" />
                          )}
                        </button>
                      ))}
                    </div>
                  </>
                )}
                {type === "dept_level" && (
                  <>
                    {!deptId ? (
                      <div className="grid gap-2 sm:grid-cols-2">
                        {(candidates?.departments ?? []).map((d) => (
                          <button
                            key={d.id}
                            type="button"
                            onClick={() => setDeptId(d.id)}
                            className="rounded-lg border bg-background p-2 text-left text-xs hover:border-primary/40"
                          >
                            {d.name}
                            <span className="ml-1 text-muted-foreground">
                              #{d.id}
                            </span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <>
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            setDeptId(null);
                            setSelected("");
                          }}
                        >
                          ← Выбрать отдел
                        </Button>
                        <Input
                          value={levelQuery}
                          onChange={(e) => setLevelQuery(e.target.value)}
                          placeholder="Поиск уровня..."
                          className="mt-2 h-8"
                        />
                        <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                          {(deptDetail?.access_levels ?? [])
                            .filter(
                              (l) =>
                                !levelQuery ||
                                `${l.name} ${l.key}`
                                  .toLowerCase()
                                  .includes(levelQuery.toLowerCase()),
                            )
                            .map((l) => (
                              <button
                                key={l.key}
                                type="button"
                                onClick={() =>
                                  setSelected(`${deptId}:${l.key}`)
                                }
                                className={`rounded-lg border p-2 text-left text-xs ${selected === `${deptId}:${l.key}` ? "border-primary bg-primary/5" : "bg-background"}`}
                              >
                                {l.name}
                                <span className="ml-1 font-mono text-[10px] text-muted-foreground">
                                  {l.key}
                                </span>
                              </button>
                            ))}
                        </div>
                      </>
                    )}
                  </>
                )}
              </div>
              <div>
                <Label className="mb-2 block text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Набор разрешений
                </Label>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {permissions.map((p) => {
                    const Icon = p.icon;
                    const checked =
                      p.key === "can_view"
                        ? view
                        : p.key === "can_create"
                          ? create
                          : p.key === "can_edit"
                            ? edit
                            : del;
                    const set =
                      p.key === "can_view"
                        ? setView
                        : p.key === "can_create"
                          ? setCreate
                          : p.key === "can_edit"
                            ? setEdit
                            : setDel;
                    return (
                      <label
                        key={p.key}
                        className={`flex cursor-pointer items-center gap-2 rounded-lg border p-2.5 text-xs ${checked ? "border-primary/30 bg-primary/5" : "bg-muted/20"}`}
                      >
                        <Checkbox
                          checked={checked}
                          onCheckedChange={(v) => set(v === true)}
                        />
                        <Icon
                          className={`size-3.5 ${checked ? p.tone : "text-muted-foreground"}`}
                        />
                        <span>{p.short}</span>
                      </label>
                    );
                  })}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {presets.map((p) => (
                    <Button
                      key={p.label}
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        setView(p.values[0]);
                        setCreate(p.values[1]);
                        setEdit(p.values[2]);
                        setDel(p.values[3]);
                      }}
                    >
                      {p.label}
                    </Button>
                  ))}
                </div>
                <Button className="mt-4 w-full" onClick={add} disabled={!selected}>
                  <Plus className="mr-2 size-4" />
                  Добавить правило
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
        <DialogFooter className="border-t px-6 py-4">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          {tab === "add" && (
            <Button onClick={add} disabled={!selected}>
              <Plus className="mr-1.5 size-4" />
              Добавить правило
            </Button>
          )}
          {tab === "rules" && (
            <Button onClick={() => save.mutate()} disabled={save.isPending}>
              Сохранить изменения
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
