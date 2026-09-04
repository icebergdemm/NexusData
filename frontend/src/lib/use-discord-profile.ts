import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

export interface DiscordProfileMedia {
  discord_id: string
  username: string
  display_name: string
  avatar_url: string | null
  banner_url: string | null
  accent_color: string | null
}

export function useDiscordProfile(discordId: string | null | undefined) {
  return useQuery({
    queryKey: ["discord-profile", discordId],
    queryFn: () => api.get<DiscordProfileMedia>(
      `/api/v1/admin/discord-profiles/${encodeURIComponent(discordId!)}`,
    ),
    enabled: Boolean(discordId),
    staleTime: 10 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
    retry: false,
  })
}
