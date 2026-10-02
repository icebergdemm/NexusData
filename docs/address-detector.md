# Address Detector

Address Detector classifies every message inside the configured Discord scope with Qwen. Lexical patterns do not gate coverage: quotes, code, meta discussion, imperatives, and messages without «ты» still reach the classifier. The detector never applies sanctions.

## Scheduling

One scheduler drains durable per-message revisions in Discord message order and selects non-empty channels round-robin. Defaults are 0.5 seconds idle debounce, 2 seconds maximum batch delay, 8 new messages per batch, and 24 total payload messages. Selection keeps every new message first, then its distinct reply targets, then the newest ordinary history. At most one `address_detector` gateway request can be queued or running; the remaining backlog stays in detector pending-work storage.

Each active batch is persisted before gateway submission and moves through `prepared`, `submitted`, `terminal_received`, `applied`, then cleared. Its identity is a stable digest of the conversation scope and the ordered `(message_id, revision)` list. On restart the detector reattaches to the existing gateway ticket and uses the saved batch composition. A batch gets at most one retry, and only after a terminal backend failure or invalid model response. Polling/recovery errors keep the active batch for later reattachment.

## Classification

Each requested index must appear exactly once in the compact strict JSON response:

```json
{"direct":[{"i":7,"r":"informal_imperative"}],"ambiguous":[],"quote":[2],"meta":[],"clean":[5]}
```

`direct` and `ambiguous` items include an allowed reason code. `quote`, `meta`, and `clean` contain indexes only. The parser rejects missing, duplicate, extra, or malformed indexes and invalid reasons. Output tokens scale with batch size: `32 + 24 × new_message_count` (224 at the maximum of 8).

- `direct_familiar_address` creates or aggregates a moderator signal and adds the configured marker.
- `ambiguous` is retained for moderator review without an automatic public action.
- `no_violation`, `quoted_or_reported`, and `example_or_metadiscussion` create no moderator signal.
- Exhausted operational failure is terminal, creates no public action, and does not block later revisions.

## Persistence and upgrade

Scoped input is stored in `discord_address_detector_pending_work`, separate from moderator-facing `AddressDetectorSignal` rows. A compact `AddressDetectorRevisionState` row per message preserves the revision fence after terminal queue rows are pruned. These revision rows are retained indefinitely so edits to old Discord messages remain visible; storage therefore grows with the number of distinct scoped messages. `AddressDetectorChannelState` stores the watermark and active batch. One `AddressDetectorBatchAudit` row per batch retains ticket, index-to-message/revision mapping, normalized results, reasons, and actions for 30 days. Direct-signal action intents are persisted before delivery and carry a stable embed marker so restart recovery can find an already-sent moderator message. SQLAlchemy `Base.metadata.create_all` creates the additive tables idempotently during normal startup; older versions ignore them.

Startup cancels active requests from the retired `address_detector_fast` and `address_detector_background` gateway clients even when unified processing is disabled. It requeues a recoverable current revision only when the unified detector is enabled. A completed legacy ticket is left alone to avoid duplicating an action.

The shared gateway priorities are dispatcher 10, dossier 100, monster 120, log analysis 150, future 200, and address detector 210. The detector therefore yields to existing AI work. Other client policies are unchanged.

| Setting | Default |
| --- | ---: |
| `ADDRESS_LLM_ENABLED` | `false` |
| `ADDRESS_LLM_IDLE_DEBOUNCE_SECONDS` | `0.5` |
| `ADDRESS_LLM_HARD_BATCH_DELAY_SECONDS` | `2.0` |
| `ADDRESS_LLM_MAX_NEW_MESSAGES` | `8` |
| `ADDRESS_LLM_MAX_PAYLOAD_MESSAGES` | `24` |
| `ADDRESS_LLM_QUOTA_MIN` | `6` |
| `ADDRESS_LLM_QUEUE_WAIT_TIMEOUT_SECONDS` | `60` |
| `ADDRESS_LLM_BACKEND_TIMEOUT_SECONDS` | `25` |
| `ADDRESS_LLM_MAX_REQUEST_AGE_SECONDS` | `120` |

Runtime counters and latency summaries reset on restart. Pending revisions, active batches, gateway tickets, batch audit, and watermark are durable.
