# NexusData external worker

This process connects out to NexusData. It has no database or shared-filesystem connection. NexusData leases tasks from its durable queue, streams an input archive to the worker, and accepts the result through the same authenticated API.

## Run

On a controlled build host with the .NET 8 SDK, publish the voice decoder as a self-contained Linux x64 executable, then create the worker bundle in a new empty directory:

```sh
dotnet publish tools/VoiceProfileDecoder/VoiceProfileDecoder.csproj -c Release -r linux-x64 --self-contained true
python external_worker/build_bundle.py /tmp/nexusdata-worker
```

Copy the bundle to the remote machine. It excludes NexusData's `config.py`, `database.py`, `.env`, DB settings, and application credentials; the worker does not import the DB engine. The remote worker needs Python 3.12, but does not need .NET installed when using the self-contained decoder. The pinned ONNX model is streamed from NexusData only to an assigned voiceprint job, so the worker makes no external download requests at runtime.

On the remote machine, from the bundle directory:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Register a worker in **Администрирование → Внешние воркеры**. The panel shows a bearer token once. Set these variables in the worker service environment; do not put the token in source control:

```text
NEXUSDATA_URL=https://your-nexusdata-host
NEXUSDATA_WORKER_TOKEN=<one-time token from the admin panel>
NEXUSDATA_WORKER_NAME=voice-worker-1
NEXUSDATA_WORKER_DATA=/var/lib/nexusdata-worker
VOICE_PROFILE_DECODER_PATH=/opt/nexusdata/tools/VoiceProfileDecoder/VoiceProfileDecoder
```

Start it from the repository root with `python -m external_worker.runner`. Keep it under a service manager such as systemd so a process exit or machine restart brings it back. The local SQLite spool retains completed results until NexusData acknowledges them. NexusData requeues leases after a worker stops heartbeating; handlers therefore need to be idempotent.

## Add a task module

Create `external_worker/plugins/<module>.py` and export a `HANDLERS` dictionary. Each handler receives `(job, input_path, output_dir, cancelled, progress)` and returns `(result_json, optional_zip_path)`. The worker discovers plugin modules at startup and advertises each handler key as a task capability. Keep external side effects out of handlers; commit results in NexusData after validating them.

The worker polls commands over the same API. `stop` pauses new work and interrupts the active handler cooperatively, `stop_task` interrupts the active job, `restart` re-executes the worker process, and `resume` enables polling again. A task that was interrupted is returned to the queue by NexusData's lease/retry handling.
