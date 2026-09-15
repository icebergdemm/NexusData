# NexusData

## Scope

This repository contains the NexusData website and its data/API integrations.

Before changing player data, replay data, SFTP deployment, production rollout, or related Barotrauma data flows, read the `barotrauma-nexus-data` skill.

Keep changes focused. Do not invent a second implementation, deployment path, or workflow because the existing one is inconvenient.

## Core operating rules

- Prefer the smallest correct change that fully satisfies the request.
- Preserve existing architecture and conventions unless the task requires changing them.
- Do not refactor, rename, reformat, or clean up unrelated code.
- Inspect only files relevant to the task.
- Prefer targeted searches and file reads over broad repository scans.
- Reuse information already obtained; do not repeat equivalent inspections or tool calls.
- If a safe interpretation of an ambiguous requirement is possible, use it and proceed.
- Ask a question only when missing information materially blocks a correct implementation.
- Never claim an action, test, deployment, or verification that was not actually performed.

## Git and deployment

- `main` is the canonical branch.
- Preserve unrelated work.
- Never force-push, reset, or clean the working tree without explicit approval.
- Do not stage, commit, or upload anything under `docs/superpowers/**`.

Release order:

1. Validate locally.
2. Push `main`.
3. Wait for the `frontend-dist` workflow.
4. Verify that its `.build-sha` matches the pushed commit.
5. Run `update restart` in the Pterodactyl console.
6. Perform post-restart web/API checks.

Important:

- SFTP uploads files only.
- SFTP does not restart the application.
- SFTP is not a shell for Pterodactyl.
- The game server is not the website deployment endpoint.
- Do not claim production rollout is complete until post-restart web/API checks have passed.

Do not create an alternative deployment procedure unless the existing procedure is demonstrably unavailable and the change is explicitly justified.

## Secrets and data

Never print, commit, expose, or upload:

- `.env` contents
- API keys
- bearer tokens
- passwords
- CA/private-key material
- raw credentials
- other authentication secrets

Keep AI endpoint credentials and TLS files in the NexusData/Qwen host environment.

Keep prompts and response schemas in Git.

AI dossiers may persist normalized results and run metadata.

For explicit superuser debugging:

- a bounded raw model response may be stored on the run;
- it may be returned only to superusers in the AI-center profile view;
- never expose raw model responses to ordinary moderators;
- never expose raw model responses through general API lists.

Treat the dossier as moderator reference material. A human makes moderation decisions.

## Local checks

Use the repository's Python 3.12 environment.

Prefer targeted validation first.

Examples:

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

Validation rules:

- Start with the cheapest relevant check.
- Run tests related to the changed code before broader suites.
- Expand validation only when the change warrants it.
- Do not run expensive or unrelated checks merely for completeness.
- If a targeted check is sufficient, stop there.
- Always run `git diff --check` when making source changes.

The full pytest suite can have Windows-only collection failures from unrelated `fcntl` and test-fixture assumptions.

Report such failures separately.

Never turn a selective green test into a fictional full-suite victory.

## Runtime validation

Prefer:

- static checks;
- targeted API tests;
- Qwen health-check;
- web UI validation.

Do not perform live game checks by:

- launching Barotrauma;
- typing into a user's game session;
- interacting with a running game;
- taking screenshots of a running game.

The game server is not the website deployment endpoint.

## Tool and inspection discipline

Before using a tool or performing an inspection, ask:

1. Do I actually need this information?
2. Is the information already available?
3. Can one targeted operation replace several broad operations?
4. Will the result change the implementation or validation decision?

Rules:

- Do not inspect the entire repository unless necessary.
- Do not repeatedly search for information already found.
- Do not make duplicate tool calls.
- Batch independent reads/checks when practical.
- Prefer exact file paths and targeted searches.
- After finding the relevant implementation, inspect its immediate dependencies rather than searching unrelated areas.

## Change discipline

For every implementation:

1. Identify the requested outcome.
2. Locate the smallest relevant part of the codebase.
3. Understand the existing behavior.
4. Make the smallest correct change.
5. Run the narrowest relevant validation.
6. Fix failures caused by the change.
7. Stop when the acceptance criteria are satisfied.

Do not:

- redesign unrelated code;
- introduce abstractions without need;
- migrate technologies without request;
- add dependencies without need;
- modify unrelated formatting;
- create duplicate implementations;
- add documentation solely to describe obvious code.

## Completion criteria

A task is complete when:

- the requested behavior is implemented;
- relevant validation has passed;
- no known change-related error remains;
- unrelated work is preserved;
- the final result can be accurately reported.

Do not continue optimizing after the task is sufficiently correct and verified.

## Final response

Keep the final response concise.

Report only:

- what changed;
- relevant files;
- validation performed;
- important unresolved issues or limitations.

Do not provide a transcript of the implementation process.

Do not repeat the user's request.

Do not describe internal reasoning.
