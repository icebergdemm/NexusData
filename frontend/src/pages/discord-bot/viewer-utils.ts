export interface ViewerChannel {
  id: string; name: string; position: number; parent_id?: string | null
  category_id?: string | null; archived?: boolean; type: string
}
export interface ViewerMember { discord_id: string; display_name: string | null; username: string | null; avatar_url?: string | null; role_ids: string[] }
export interface ViewerRole { id: string; name: string; color: string | null; position: number; hoist?: boolean }

export function groupThreadsByParent<T extends ViewerChannel>(channels: T[]) {
  const channelsById = new Map<string, T>(channels.filter((c) => c.type !== "thread").map((c) => [c.id, c]))
  const threadsByParent = new Map<string, T[]>()
  for (const thread of channels.filter((c) => c.type === "thread")) {
    const parentId = thread.parent_id
    if (!parentId || !channelsById.has(parentId)) continue
    threadsByParent.set(parentId, [...(threadsByParent.get(parentId) ?? []), thread])
  }
  for (const threads of threadsByParent.values()) threads.sort((a, b) => a.position - b.position || a.name.localeCompare(b.name))
  return threadsByParent
}

export function mergeViewerChannels<T extends { id: string }>(current: T[], incoming: T[]) {
  const channels = new Map(current.map((channel) => [channel.id, channel]))
  for (const channel of incoming) channels.set(channel.id, channel)
  return [...channels.values()]
}

export function groupMembersByRole(members: ViewerMember[], roles: ViewerRole[]) {
  const groups = [...roles].filter((role) => role.hoist).sort((a, b) => b.position - a.position).map((role) => ({ role, label: role.name, members: [] as ViewerMember[] }))
  const otherMembers = { role: null, label: "Участники", members: [] as ViewerMember[] }
  for (const member of members) {
    const group = groups.find(({ role }) => member.role_ids.includes(role.id))
    ;(group ?? otherMembers).members.push(member)
  }
  return [...groups, otherMembers].filter((group) => group.members.length)
}

export function applyVoiceState<T extends { id: string; voice_members?: { id: string }[] }>(channels: T[], event: { channel_id: string; voice: { member_id: string; connected: boolean; member?: { id: string } } }) {
  return channels.map((channel) => {
    if (channel.id !== event.channel_id || !channel.voice_members) return channel
    const members = channel.voice_members.filter((member) => member.id !== event.voice.member_id)
    if (event.voice.connected && event.voice.member) members.push(event.voice.member as NonNullable<T["voice_members"]>[number])
    return { ...channel, voice_members: members }
  })
}

export function parseDiscordSearch(input: string): { query: string; filters: Record<string, string[]> } {
  const filters: Record<string, string[]> = {}
  const text: string[] = []
  const aliases: Record<string, string> = {
    in: "in", from: "from", mentions: "mentions", mention: "mentions",
    mentions_role: "mentions_role_id", mentions_role_id: "mentions_role_id",
    mention_everyone: "mention_everyone", replied_to: "replied_to_user_id",
    replied_to_user_id: "replied_to_user_id", replied_to_message: "replied_to_message_id",
    replied_to_message_id: "replied_to_message_id", has: "has", before: "before",
    after: "after", during: "during", pinned: "pinned", author_type: "author_type",
    embed: "embed_type", embed_type: "embed_type", provider: "embed_provider",
    embed_provider: "embed_provider", domain: "link_hostname", link_hostname: "link_hostname",
    filename: "attachment_filename", attachment_filename: "attachment_filename",
    extension: "attachment_extension", attachment_extension: "attachment_extension",
    sort: "sort", sort_by: "sort_by", sort_order: "sort_order", slop: "slop",
    include_nsfw: "include_nsfw",
  }
  for (const token of input.match(/(?:"[^"]*"|\S+)/g) ?? []) {
    const match = token.match(/^([^:]+):(.+)$/)
    const key = match && aliases[match[1].toLowerCase()]
    if (!match || !key) { text.push(token); continue }
    const value = match[2].replace(/^"|"$/g, "")
    const target = key === "mentions" && /^@?everyone$/i.test(value) ? "mention_everyone" : key
    filters[target] = [...(filters[target] ?? []), target === "mention_everyone" && value !== "false" ? "true" : value]
  }
  return { query: text.join(" "), filters }
}

export function discordSearchSnowflake(date: string): string | null {
  const timestamp = Date.parse(date)
  if (!Number.isFinite(timestamp)) return null
  return String(BigInt(Math.max(0, Math.floor(timestamp - 1420070400000))) << 22n)
}

export function mergeAuditEntries<T extends { id: string }>(current: T[], incoming: T[]) {
  const entries = new Map(incoming.map((entry) => [entry.id, entry]))
  for (const entry of current) if (!entries.has(entry.id)) entries.set(entry.id, entry)
  return [...entries.values()].sort((a, b) => BigInt(b.id) > BigInt(a.id) ? 1 : BigInt(b.id) < BigInt(a.id) ? -1 : 0)
}
