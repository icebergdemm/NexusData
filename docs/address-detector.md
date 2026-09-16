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
It is an external MIT-licensed artifact and is not stored in Git. Install the
three files into `/home/container/models/address-detector` on the NexusData
runtime host (or use another directory and point the two environment settings
at it):

```sh
set -eu
model_dir=/home/container/models/address-detector
mkdir -p "$model_dir"
curl -fL --retry 3 -o "$model_dir/model.onnx" \
  'https://huggingface.co/MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli/resolve/main/onnx/model.onnx?download=true'
curl -fL --retry 3 -o "$model_dir/tokenizer.json" \
  'https://huggingface.co/MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli/resolve/main/onnx/tokenizer.json?download=true'
curl -fL --retry 3 -o "$model_dir/config.json" \
  'https://huggingface.co/MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli/resolve/main/config.json?download=true'
sha256sum "$model_dir/model.onnx" "$model_dir/tokenizer.json" "$model_dir/config.json"
```

The checksum output is the installation record; keep it with the server
deployment notes. `config.json` is required because the NLI adapter reads the
`id2label` mapping instead of assuming a particular logits order. The bot
never downloads the model at startup.

If the model or tokenizer is absent, `hybrid` stays in rules-only degraded mode:
obvious rules still classify, while unresolved candidates become `UNCERTAIN`
and are sent to moderators.

## Rollout

1. Install the pinned Python dependencies from `requirements.txt`.
2. Place the model outside the repository and verify CPU load/latency on the
   target server:

   ```sh
   python scripts/address_detector_benchmark.py
   ```

   The command reports cold load time, warm p50/p95 latency and the result of
   a small Russian smoke set. It does not send Discord messages.
3. Configure a test Discord channel, signal channel/thread, moderator role,
   and emoji in the third admin tab.
4. Start with `ADDRESS_ANALYZER_MODE=rules`; manually check reaction lifecycle,
   edit/delete handling, and incident aggregation.
5. Set `ONNX_MODEL_PATH` and `ONNX_TOKENIZER_PATH` to the installed files, then
   set `ADDRESS_ANALYZER_MODE=hybrid` only after the CPU benchmark and manual
   examples are acceptable. The UI journal reports `model_loaded` or the
   rules-only degraded reason after restart.
6. Deploy through the normal NexusData flow with `update restart`.

There is no automatic punishment path in this feature. The footer in every
signal is intentionally explicit: it is an administrative signal, not a
sanction.
