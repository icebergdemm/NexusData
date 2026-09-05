import assert from "node:assert/strict"
import test from "node:test"

import { buildCommunicationQuery, communicationChannelLabels } from "../frontend/src/pages/logs/communications-utils.ts"

test("communication query serializes the investigation filters", () => {
  const query = new URLSearchParams(buildCommunicationQuery({
    search: "опасный текст",
    source: "voice",
    channel: "radio",
    server: "ASPER1",
    asrStatus: "recognized",
    lobby: true,
  }, 3))

  assert.equal(query.get("page"), "3")
  assert.equal(query.get("source"), "voice")
  assert.equal(query.get("channel"), "radio")
  assert.equal(query.get("asr_status"), "recognized")
  assert.equal(query.get("lobby"), "true")
  assert.equal(query.get("search"), "опасный текст")
})

test("communication channel labels cover chat and voice channels", () => {
  assert.equal(communicationChannelLabels.private, "личный")
  assert.equal(communicationChannelLabels.dead, "мёртвые")
  assert.equal(communicationChannelLabels.local, "локальный")
})
