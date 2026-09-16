import assert from "node:assert/strict"
import test from "node:test"
import type { IpRow, KillRow, NameRow, RoleActionRow, SessionRow } from "../src/pages/players/detail-types"
import {
  getActivitySummary,
  groupRoleActions,
  isSessionActive,
  sortIpRows,
  sortKillRows,
  sortNameRows,
} from "../src/pages/players/history-utils"

const now = Date.parse("2026-09-16T12:00:00Z")

test("activity summary uses only the last 30 days and includes an active session", () => {
  const sessions: SessionRow[] = [
    { id: 1, session_number: 3, login_time: "2026-09-15T10:00:00Z", logout_time: "2026-09-15T11:00:00Z", session_duration: 3600, avg_ping: 40 },
    { id: 2, session_number: 2, login_time: "2026-09-16T11:00:00Z", logout_time: null, session_duration: null, avg_ping: 60 },
    { id: 3, session_number: 1, login_time: "2026-08-10T12:00:00Z", logout_time: "2026-08-10T13:00:00Z", session_duration: 3600, avg_ping: 20 },
  ]

  assert.deepEqual(getActivitySummary(sessions, now), {
    sessionCount: 2,
    totalDuration: 7200,
    averageDuration: 3600,
    averagePing: 50,
  })
})

test("empty activity summary has zero durations and no ping", () => {
  assert.deepEqual(getActivitySummary([], now), {
    sessionCount: 0,
    totalDuration: 0,
    averageDuration: 0,
    averagePing: null,
  })
})

test("active session is detected by a missing logout time", () => {
  assert.equal(isSessionActive({ id: 1, session_number: 1, login_time: "2026-09-16T11:00:00Z", logout_time: null, session_duration: null, avg_ping: null }), true)
  assert.equal(isSessionActive({ id: 2, session_number: 2, login_time: "2026-09-15T11:00:00Z", logout_time: "2026-09-15T12:00:00Z", session_duration: 3600, avg_ping: null }), false)
})

test("IP history is sorted by last use with null dates last", () => {
  const ips: IpRow[] = [
    { id: 1, ip: "203.0.113.1", subnet: "203.0.113.0/24", first_seen: "2026-09-01T00:00:00Z", last_seen: "2026-09-10T00:00:00Z" },
    { id: 3, ip: "203.0.113.3", subnet: "203.0.113.0/24", first_seen: "2026-09-01T00:00:00Z", last_seen: "2026-09-12T00:00:00Z" },
    { id: 2, ip: "203.0.113.2", subnet: "203.0.113.0/24", first_seen: null, last_seen: null },
  ]

  assert.deepEqual(sortIpRows(ips).map((row) => row.id), [3, 1, 2])
  assert.deepEqual(ips.map((row) => row.id), [1, 3, 2])
})

test("nickname history is sorted by last use with null dates last", () => {
  const names: NameRow[] = [
    { id: 1, nickname: "старый", first_seen: "2026-09-01T00:00:00Z", last_seen: "2026-09-10T00:00:00Z" },
    { id: 2, nickname: "новый", first_seen: "2026-09-02T00:00:00Z", last_seen: "2026-09-12T00:00:00Z" },
    { id: 3, nickname: "без даты", first_seen: null, last_seen: null },
  ]

  assert.deepEqual(sortNameRows(names).map((row) => row.id), [2, 1, 3])
  assert.deepEqual(names.map((row) => row.id), [1, 2, 3])
})

test("monster kills are sorted from the most frequent to the least frequent", () => {
  const kills: KillRow[] = [
    { id: 1, monster_type: "husk", kills_count: 2 },
    { id: 2, monster_type: "crawler", kills_count: 8 },
  ]

  assert.deepEqual(sortKillRows(kills).map((row) => row.monster_type), ["crawler", "husk"])
})

test("role actions are grouped by role and sorted by value inside each group", () => {
  const actions: RoleActionRow[] = [
    { id: 1, role: "mechanic", action_type: "repair_hull", value: 4 },
    { id: 2, role: "engineer", action_type: "operate_reactor", value: 10 },
    { id: 3, role: "mechanic", action_type: "repair_device", value: 9 },
  ]

  assert.deepEqual(groupRoleActions(actions), [
    { role: "engineer", actions: [actions[1]] },
    { role: "mechanic", actions: [actions[2], actions[0]] },
  ])
})
