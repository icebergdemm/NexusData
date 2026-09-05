import assert from "node:assert/strict"
import test from "node:test"

import {
  buildCommunicationQuery,
  communicationChannelLabels,
  communicationContextLabels,
  communicationContextSummary,
  communicationAsrStatusLabels,
  communicationsRefreshIntervalMs,
  recipientTextVariant,
} from "../frontend/src/pages/logs/communications-utils.ts"

test("communication query serializes the investigation filters", () => {
  const query = new URLSearchParams(buildCommunicationQuery({
    search: "опасный текст",
    source: "voice",
    channel: "radio",
    server: "ASPER1",
    asrStatus: "recognized",
    context: "lobby",
  }, 3))

  assert.equal(query.get("page"), "3")
  assert.equal(query.get("source"), "voice")
  assert.equal(query.get("channel"), "radio")
  assert.equal(query.get("asr_status"), "recognized")
  assert.equal(query.get("context"), "lobby")
  assert.equal(query.get("search"), "опасный текст")
})

test("communication channel labels cover chat and voice channels", () => {
  assert.equal(communicationChannelLabels.private, "личный чат")
  assert.equal(communicationChannelLabels.dead, "чат мёртвых")
  assert.equal(communicationChannelLabels.default, "локальный чат")
  assert.equal(communicationChannelLabels.local, "локальный голос")
})

test("communication context labels distinguish lobby, round, and unresolved round token", () => {
  assert.equal(communicationContextLabels.lobby, "лобби")
  assert.equal(communicationContextLabels.round, "раунд")
  assert.equal(communicationContextLabels.unmatched, "раунд не сопоставлен")
})

test("communication context summary does not expose internal server session ids", () => {
  assert.equal(communicationContextSummary("lobby"), "лобби")
  assert.equal(communicationContextSummary("round"), "раунд")
  assert.equal(communicationContextSummary("unmatched"), "раунд не сопоставлен")
})

test("communication ASR labels include silent microphone frames", () => {
  assert.equal(communicationAsrStatusLabels.no_speech, "речь не обнаружена")
  assert.equal(communicationsRefreshIntervalMs, 5000)
})

test("recipient text is shown only when server delivery changed the message", () => {
  assert.equal(recipientTextVariant("hello", "hello"), null)
  assert.equal(recipientTextVariant("hello", " hello "), null)
  assert.equal(recipientTextVariant("hello", "hеllo"), "hеllo")
  assert.equal(recipientTextVariant(null, "delivered"), "delivered")
})
