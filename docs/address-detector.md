# Detector for informal second-person address

The detector is disabled by default and monitors only the explicit
`MONITORED_CHANNEL_IDS` whitelist. Category IDs never add channels by
themselves. The feature does not send messages to the author, delete content,
or apply moderation actions.

## Configuration

Set defaults in `.env` or use the third tab of `/admin/discord-bot` with the
`discord_bot.manage_config` permission. Database values override `.env`:

- `MODERATION_ADDRESS_DETECTOR_ENABLED`
- `MODERATION_SIGNALS_CHANNEL_ID`
- `MODERATOR_ROLE_ID`
- `REDADMIN_EMOJI_ID`
- `MONITORED_CHANNEL_IDS`
- `EXCLUDED_CHANNEL_IDS`
- `ADDRESS_CONTEXT_MESSAGES`
- `ADDRESS_INCIDENT_WINDOW_SECONDS`
- `ADDRESS_ANALYZER_MODE=rules|hybrid`
- `ADDRESS_LIKELY_THRESHOLD`
- `ADDRESS_CLEAN_THRESHOLD`
- `ADDRESS_ANALYSIS_TIMEOUT_SECONDS`
- `ONNX_MODEL_PATH`
- `ONNX_TOKENIZER_PATH`

The signal target, moderator role, emoji, and monitored channels are validated
when the Discord bot becomes ready. An unavailable signal target disables the
detector. An unavailable emoji degrades the reaction marker but does not drop
the candidate.

## Optional local model

The default candidate is
[`MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli`](https://huggingface.co/MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli/tree/main/onnx).
It is an external MIT-licensed artifact and is not stored in Git. Download the
ONNX file and tokenizer on the operator's server over SSH, then set paths to
the mounted files. The bot never downloads the model at startup.

If the model or tokenizer is absent, `hybrid` stays in rules-only degraded mode:
obvious rules still classify, while unresolved candidates become `UNCERTAIN`
and are sent to moderators.

## Rollout

1. Install the pinned Python dependencies from `requirements.txt`.
2. Place the model outside the repository and verify CPU load/latency on the
   target server.
3. Configure a test Discord channel, signal channel/thread, moderator role,
   and emoji in the third admin tab.
4. Start with `ADDRESS_ANALYZER_MODE=rules`; manually check reaction lifecycle,
   edit/delete handling, and incident aggregation.
5. Set `ADDRESS_ANALYZER_MODE=hybrid` only after the CPU benchmark and manual
   examples are acceptable.
6. Deploy through the normal NexusData flow with `update restart`.

There is no automatic punishment path in this feature. The footer in every
signal is intentionally explicit: it is an administrative signal, not a
sanction.

