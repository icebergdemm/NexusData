import test from "node:test"
import assert from "node:assert/strict"
import { applyVoiceState, discordSearchSnowflake, groupMembersByRole, groupThreadsByParent, mergeAuditEntries, parseDiscordSearch } from "../src/pages/discord-bot/viewer-utils.ts"

test("threads are grouped by parent, sorted and orphan threads are ignored", () => {
  const grouped = groupThreadsByParent([
    { id: "1", name: "general", position: 1, type: "text" },
    { id: "2", name: "later", position: 2, type: "thread", parent_id: "1", archived: true },
    { id: "3", name: "first", position: 1, type: "thread", parent_id: "1" },
    { id: "4", name: "orphan", position: 0, type: "thread", parent_id: "404" },
  ])
  assert.deepEqual(grouped.get("1")?.map((thread) => thread.id), ["3", "2"])
})

test("members appear in each Discord role group with unassigned members at bottom", () => {
  const grouped = groupMembersByRole([
    { discord_id: "1", username: "a", display_name: "A", role_ids: ["r1", "r2"] },
    { discord_id: "2", username: "b", display_name: "B", role_ids: [] },
  ], [
    { id: "r1", name: "Low", color: null, position: 1 },
    { id: "r2", name: "High", color: "#fff", position: 2 },
  ])
  assert.deepEqual(grouped.map((group) => [group.role?.name ?? "none", group.members.map((member) => member.discord_id)]), [
    ["High", ["1"]], ["Low", ["1"]], ["none", ["2"]],
  ])
})

test("voice events join, move and disconnect members without duplicating them", () => {
  const tree = [{ id: "voice", voice_members: [{ id: "1" }] }, { id: "other", voice_members: [] }]
  const joined = applyVoiceState(tree, { channel_id: "voice", voice: { member_id: "2", connected: true, member: { id: "2" } } })
  assert.deepEqual(joined[0].voice_members?.map((member) => member.id), ["1", "2"])
  const disconnected = applyVoiceState(joined, { channel_id: "voice", voice: { member_id: "1", connected: false } })
  assert.deepEqual(disconnected[0].voice_members?.map((member) => member.id), ["2"])
})

test("search parser separates Discord filters from quoted text", () => {
  assert.deepEqual(parseDiscordSearch('"reactor offline" in:general from:mod has:file pinned:true before:2026-09-28'), {
    query: '"reactor offline"', filters: { in: ["general"], from: ["mod"], has: ["file"], pinned: ["true"], before: ["2026-09-28"] },
  })
  assert.deepEqual(parseDiscordSearch('mentions:@everyone mentions_role:Engineers replied_to:mod replied_to_message:123 embed:video provider:youtube domain:discord.com filename:log.csv extension:csv sort:oldest'), {
    query: "", filters: {
      mention_everyone: ["true"], mentions_role_id: ["Engineers"], replied_to_user_id: ["mod"],
      replied_to_message_id: ["123"], embed_type: ["video"], embed_provider: ["youtube"],
      link_hostname: ["discord.com"], attachment_filename: ["log.csv"], attachment_extension: ["csv"], sort: ["oldest"],
    },
  })
  assert.equal(discordSearchSnowflake("invalid"), null)
  assert.equal(discordSearchSnowflake("2015-01-01T00:00:00Z"), "0")
})

test("audit entries deduplicate by snowflake and preserve newest first", () => {
  assert.deepEqual(mergeAuditEntries([{ id: "2", label: "old" }, { id: "1", label: "old" }], [{ id: "2", label: "updated" }, { id: "3", label: "new" }]), [
    { id: "3", label: "new" }, { id: "2", label: "updated" }, { id: "1", label: "old" },
  ])
})
