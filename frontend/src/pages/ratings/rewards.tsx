import { useQuery } from "@tanstack/react-query"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import { RewardGroupsSection } from "./reward-groups-section"
import { RatingRewardsSection } from "./rating-rewards-section"
import { PlaytimeSection } from "./playtime-section"
import type { PlaytimeCondition, RatingRewardCondition, RewardGroup } from "./rewards-types"

export function RewardsConfigPage() {
  const { data: admin } = useCurrentAdmin()
  // Бэкенд гейтит сохранение здесь именно ratings:manage_rewards, не
  // ratings:edit (см. routes/logs.py) — раньше кнопки редактирования
  // смотрели на другое право, чем реально требовал сервер: с одним правом
  // без другого либо кнопок не было видно вовсе, либо они были видны и
  // сохранение падало 403-й.
  const edit = canDo(admin, "ratings", "manage_rewards")

  const { data: groups, isLoading: loadingGroups } = useQuery({
    queryKey: ["reward-groups"], queryFn: () => api.get<RewardGroup[]>("/api/v1/admin/reward-groups"),
  })
  const { data: ratingRewards, isLoading: loadingRating } = useQuery({
    queryKey: ["rating-rewards"], queryFn: () => api.get<RatingRewardCondition[]>("/api/v1/admin/rating-rewards"),
  })
  const { data: playtimeConditions, isLoading: loadingPlaytime } = useQuery({
    queryKey: ["playtime-conditions"], queryFn: () => api.get<PlaytimeCondition[]>("/api/v1/admin/playtime/conditions"),
  })

  if (loadingGroups || loadingRating || loadingPlaytime) {
    return <div className="space-y-2"><Skeleton className="h-64" /><Skeleton className="h-64" /></div>
  }

  const groupList = groups ?? []

  return (
    <Tabs defaultValue="groups" className="space-y-4">
      <TabsList>
        <TabsTrigger value="groups">📦 Группы наград</TabsTrigger>
        <TabsTrigger value="rating">🏆 По рейтингу</TabsTrigger>
        <TabsTrigger value="playtime">⏱ По времени</TabsTrigger>
      </TabsList>

      <TabsContent value="groups">
        <RewardGroupsSection groups={groupList} canEdit={edit} />
      </TabsContent>
      <TabsContent value="rating">
        <RatingRewardsSection conditions={ratingRewards ?? []} groups={groupList} canEdit={edit} />
      </TabsContent>
      <TabsContent value="playtime">
        <PlaytimeSection conditions={playtimeConditions ?? []} groups={groupList} canEdit={edit} />
      </TabsContent>
    </Tabs>
  )
}
