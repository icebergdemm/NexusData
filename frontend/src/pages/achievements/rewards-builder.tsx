/** Конструктор наград ачивки: предмет / стат / навык / талант — по одной
 *  карточке на награду, с выбором из игровых справочников вместо ввода
 *  идентификаторов руками (валидатор validate_rewards в
 *  services_achievements.py на неизвестный идентификатор ругается только
 *  предупреждением, так что опечатка иначе сохранится и тихо не сработает).
 */
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ItemField, SearchPicker, type Option } from "./pickers"
import {
  QUALITY_LABELS, REWARD_TYPE_LABELS,
  type AchievementsReference, type RewardSpec,
} from "./types"

function blankReward(type: RewardSpec["type"], reference: AchievementsReference, achievementKey: string): RewardSpec {
  switch (type) {
    case "item":
      return { type: "item", identifier: "", amount: 1 }
    case "stat":
      // identifier — уникальный ключ бонуса, по нему движок отличает наш
      // модификатор от чужих; валидатор требует его непустым.
      return {
        type: "stat",
        stat_type: reference.stat_types[0] ?? "",
        identifier: `ach_${achievementKey || "reward"}`,
        value: 1,
      }
    case "skill":
      return { type: "skill", skill: reference.skills[0] ?? "helm", amount: 10 }
    case "talent":
      return { type: "talent", talent_id: "" }
  }
}

function RewardCard({ reward, onChange, onRemove, reference }: {
  reward: RewardSpec
  onChange: (r: RewardSpec) => void
  onRemove: () => void
  reference: AchievementsReference
}) {
  const talentOptions: Option[] = Object.entries(reference.talents ?? {}).flatMap(([job, list]) =>
    list.map((t) => ({ value: t.identifier, label: `${t.name} · ${job}`, hint: t.identifier })),
  )
  const statOptions: Option[] = reference.stat_types.map((s) => ({ value: s, label: s }))

  return (
    <div className="rounded-md border p-2">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold">{REWARD_TYPE_LABELS[reward.type]}</span>
        <Button type="button" variant="ghost" size="icon-sm" title="Убрать награду" onClick={onRemove}>
          <Trash2 className="size-4 text-destructive" />
        </Button>
      </div>

      {reward.type === "item" && (
        <div className="grid gap-2 sm:grid-cols-4">
          <div className="sm:col-span-2">
            <Label className="mb-1 block text-[11px]">Предмет *</Label>
            <ItemField
              value={reward.identifier}
              onChange={(identifier) => onChange({ ...reward, identifier })}
              className="w-full"
            />
          </div>
          <div>
            <Label className="mb-1 block text-[11px]">Количество</Label>
            <Input
              type="number" min={1} max={100}
              value={reward.amount ?? 1}
              onChange={(e) => onChange({ ...reward, amount: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })}
              className="h-9"
            />
          </div>
          <div>
            <Label className="mb-1 block text-[11px]">Качество</Label>
            <Select
              value={String(reward.quality ?? 0)}
              onValueChange={(v) => onChange({ ...reward, quality: Number(v) })}
            >
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                {QUALITY_LABELS.map((q, i) => <SelectItem key={q} value={String(i)}>{q}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="mb-1 block text-[11px]">Прочность, %</Label>
            <Input
              type="number" min={0} max={100}
              value={reward.condition ?? 100}
              onChange={(e) => onChange({ ...reward, condition: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })}
              className="h-9"
            />
          </div>
        </div>
      )}

      {reward.type === "stat" && (
        <div className="grid gap-2 sm:grid-cols-4">
          <div className="sm:col-span-2">
            <Label className="mb-1 block text-[11px]">Тип модификатора *</Label>
            {statOptions.length ? (
              <SearchPicker
                options={statOptions}
                value={reward.stat_type}
                onPick={(stat_type) => onChange({ ...reward, stat_type })}
                className="w-full"
              />
            ) : (
              <Input
                value={reward.stat_type}
                onChange={(e) => onChange({ ...reward, stat_type: e.target.value })}
                className="h-9 font-mono text-xs"
              />
            )}
          </div>
          <div>
            <Label className="mb-1 block text-[11px]">Значение *</Label>
            <Input
              type="number" step="0.1"
              value={reward.value}
              onChange={(e) => onChange({ ...reward, value: Number(e.target.value) || 0 })}
              className="h-9"
            />
          </div>
          <div>
            <Label className="mb-1 block text-[11px]">Ключ бонуса *</Label>
            <Input
              value={reward.identifier}
              onChange={(e) => onChange({ ...reward, identifier: e.target.value })}
              placeholder="ach_my_achievement"
              className="h-9 font-mono text-xs"
            />
          </div>
          <label className="flex items-center gap-2 text-xs sm:col-span-4">
            <Checkbox
              checked={!!reward.remove_on_death}
              onCheckedChange={(c) => onChange({ ...reward, remove_on_death: c === true })}
            />
            Снимать при смерти персонажа
          </label>
        </div>
      )}

      {reward.type === "skill" && (
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <Label className="mb-1 block text-[11px]">Навык *</Label>
            <Select value={reward.skill} onValueChange={(skill) => onChange({ ...reward, skill })}>
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                {reference.skills.map((s) => (
                  <SelectItem key={s} value={s}>{reference.skill_labels?.[s] ?? s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="mb-1 block text-[11px]">Прибавка (может быть отрицательной) *</Label>
            <Input
              type="number"
              value={reward.amount}
              onChange={(e) => onChange({ ...reward, amount: Number(e.target.value) || 0 })}
              className="h-9"
            />
          </div>
        </div>
      )}

      {reward.type === "talent" && (
        <div>
          <Label className="mb-1 block text-[11px]">Талант *</Label>
          {talentOptions.length ? (
            <SearchPicker
              options={talentOptions}
              value={reward.talent_id}
              onPick={(talent_id) => onChange({ ...reward, talent_id })}
              className="w-full sm:w-96"
            />
          ) : (
            <Input
              value={reward.talent_id}
              onChange={(e) => onChange({ ...reward, talent_id: e.target.value })}
              className="h-9 font-mono text-xs"
            />
          )}
          <p className="mt-1 text-[10px] text-muted-foreground">
            Талант выдастся, даже если он из дерева другой профессии — движок его просто проигнорирует.
          </p>
        </div>
      )}
    </div>
  )
}

export function RewardsBuilder({ rewards, onChange, reference, achievementKey }: {
  rewards: RewardSpec[]
  onChange: (r: RewardSpec[]) => void
  reference: AchievementsReference
  achievementKey: string
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <Label className="text-xs">Награды при разблокировке</Label>
        <div className="flex flex-wrap gap-1">
          {(Object.keys(REWARD_TYPE_LABELS) as RewardSpec["type"][]).map((t) => (
            <Button
              key={t}
              type="button" variant="outline" size="sm" className="h-7 text-xs"
              onClick={() => onChange([...rewards, blankReward(t, reference, achievementKey)])}
            >
              <Plus className="mr-1 size-3" /> {REWARD_TYPE_LABELS[t].split(" (")[0]}
            </Button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        {rewards.map((r, i) => (
          <RewardCard
            key={i}
            reward={r}
            reference={reference}
            onChange={(next) => onChange(rewards.map((x, j) => (j === i ? next : x)))}
            onRemove={() => onChange(rewards.filter((_, j) => j !== i))}
          />
        ))}
        {!rewards.length && (
          <p className="rounded-md border border-dashed py-5 text-center text-xs text-muted-foreground">
            Наград нет — ачивка будет чисто статусной (запись + уведомление в Discord)
          </p>
        )}
      </div>
    </div>
  )
}
