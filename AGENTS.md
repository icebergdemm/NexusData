# NexusData

## Scope

This repository contains the NexusData website and its data/API integrations. Read the `barotrauma-nexus-data` skill before changing player data, replay data, SFTP deployment, or production rollout. Keep changes focused; do not invent a second deployment path because the first one looked inconvenient.

## Git and deployment

- `main` is the canonical branch. Preserve unrelated work and never force-push, reset, or clean the working tree without explicit approval.
- Do not stage, commit, or upload anything under `docs/superpowers/**`.
- The release order is: validate locally → push `main` → wait for the `frontend-dist` workflow and verify its `.build-sha` matches the pushed commit → run `update restart` in the Pterodactyl console.
- SFTP uploads files only. SFTP does not restart the application and is not a shell for Pterodactyl.
- Do not claim a production rollout is complete until the post-restart web/API checks have passed.

## Secrets and data

- Never print, commit, or upload `.env` contents, API keys, bearer tokens, passwords, CA/private-key material, or raw credentials.
- Keep AI endpoint credentials and TLS files in the NexusData/Qwen host environment. Keep prompts and response schemas in Git.
- AI dossiers may persist normalized results and run metadata. For explicit superuser debugging, a bounded raw model response may be stored on the run and returned only to superusers in the AI-center profile view; never expose it to ordinary moderators or general API lists. Treat the dossier as moderator reference material; a human makes moderation decisions.

## Local checks

Use the repository's Python 3.12 environment for targeted tests, for example:

```text
python -m pytest -q tests/test_ai_dossier.py
python -m py_compile models.py services_ai_dossier.py routes/ai_dossier.py routes/logs.py workers.py
git diff --check
```

For frontend changes:

```text
cd frontend
npm test -- --run
npm run build
```

The full pytest suite can have Windows-only collection failures from unrelated `fcntl` and test-fixture assumptions. Report those separately; do not turn a selective green test into a fictional full-suite victory.

## Runtime validation

Prefer static checks, API tests, the Qwen health-check, and the web UI. Do not perform live game checks by launching Barotrauma, typing into a user's session, or taking screenshots of a running game. The game server is not the website deployment endpoint.
