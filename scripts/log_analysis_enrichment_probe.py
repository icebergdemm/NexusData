"""Local V4 probe harness.

This intentionally reads production data through SSH and calls the already
running llama-server through SSH.  It never opens the production queue and
never writes to NexusData's production database.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services_log_analysis import (
    CachingTokenizer,
    RoundLogMapping,
    build_coarse_regions,
    build_actor_index,
    load_round_observations,
    merge_incidents,
    run_dry_run,
    run_enrichment_regions,
    _scrub_report_names,
    _request_schema,
)


CONTAINER = "c0de7555-aeab-4039-a387-37fb229b69ed"
REMOTE_DB_HOST = "nexusdata-root"
LLM_HOST = "asper2-ai"
LLM_URL = "https://127.0.0.1:8000"
SSH_OPTIONS: list[str] = []


class PersistentSSH:
    def __init__(self, alias: str):
        self.alias = alias
        self.client: paramiko.SSHClient | None = None
        self.connect()

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        config = paramiko.SSHConfig()
        config_path = Path.home() / ".ssh" / "config"
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as handle:
                config.parse(handle)
        options = config.lookup(self.alias)
        kwargs = {
            "hostname": options.get("hostname", self.alias),
            "port": int(options.get("port", 22)),
            "username": options.get("user"),
            "allow_agent": True,
            "look_for_keys": True,
            "timeout": 20,
        }
        identities = options.get("identityfile") or []
        if identities:
            kwargs["key_filename"] = [os.path.expanduser(value) for value in identities]
        client.connect(**kwargs)
        self.client = client

    def run(self, command: str, payload: bytes = b"", timeout: int = 900) -> tuple[int, bytes, bytes]:
        last_error = None
        for _attempt in range(2):
            try:
                if self.client is None:
                    self.connect()
                stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
                if payload:
                    stdin.write(payload)
                stdin.channel.shutdown_write()
                exit_code = stdout.channel.recv_exit_status()
                return exit_code, stdout.read(), stderr.read()
            except Exception as exc:
                last_error = exc
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
        raise RuntimeError(f"persistent SSH request failed for {self.alias}: {last_error}")


_SSH_CONNECTIONS: dict[str, PersistentSSH] = {}


def _persistent_ssh(alias: str) -> PersistentSSH:
    connection = _SSH_CONNECTIONS.get(alias)
    if connection is None:
        connection = PersistentSSH(alias)
        _SSH_CONNECTIONS[alias] = connection
    return connection


def _remote_json(code: str) -> dict:
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    command = (
        f"docker exec {CONTAINER} python -c "
        f"\"import base64;exec(base64.b64decode('{encoded}'))\""
    )
    exit_code, stdout, stderr = _persistent_ssh(REMOTE_DB_HOST).run(command, timeout=120)
    if exit_code:
        raise RuntimeError(f"remote export failed: {stderr.decode('utf-8', errors='replace').strip()}")
    return json.loads(stdout)


def _round_export(round_id: int) -> dict:
    return _remote_json(
        """
import json, sqlite3
from datetime import timezone
from zoneinfo import ZoneInfo
from config import get_settings
from database import SessionLocal
from models import PlayerCommunication, RoundEvent
from services_logs import LOG_DB_PATH, _ts_to_ms

db = SessionLocal()
end = db.query(RoundEvent).filter(RoundEvent.id == ROUND_ID).first()
if end is None or end.paired_start is None:
    raise RuntimeError('round mapping unavailable')
start = end.paired_start
settings = get_settings()
tz_name = ((settings.sftp_server_1_logs_timezone or settings.logs_default_timezone) if settings.sftp_server_1_name == end.server_name else settings.logs_default_timezone)
tz = ZoneInfo(tz_name)
start_utc = start.created_at.replace(tzinfo=timezone.utc)
end_utc = end.created_at.replace(tzinfo=timezone.utc)
start_log = start_utc.astimezone(tz).replace(tzinfo=None)
end_log = end_utc.astimezone(tz).replace(tzinfo=None)
lower = _ts_to_ms(start_log) - 1000
upper = _ts_to_ms(end_log) + 1000
conn = sqlite3.connect(LOG_DB_PATH)
rows = conn.execute(
    'SELECT ts,id,player,action,details,server,source_file FROM log_events '
    'WHERE server=? AND ts BETWEEN ? AND ? ORDER BY ts,id',
    (end.server_name, lower, upper),
).fetchall()
conn.close()
communications = []
for row in db.query(PlayerCommunication).filter(
    PlayerCommunication.round_event_id == ROUND_ID,
    PlayerCommunication.source == 'chat',
).order_by(PlayerCommunication.occurred_at.asc(), PlayerCommunication.id.asc()).all():
    communications.append({
        'id': row.id, 'uid': row.event_uid, 'source': row.source,
        'channel': row.channel, 'player_name': row.player_name,
        'original_text': row.original_text, 'transcript_text': row.transcript_text,
        'occurred_at': row.occurred_at.isoformat() if row.occurred_at else None,
    })
print(json.dumps({
    'round_id': ROUND_ID, 'server': end.server_name, 'timezone': tz_name,
    'start_utc': start.created_at.isoformat(), 'end_utc': end.created_at.isoformat(),
    'start_log': start_log.isoformat(), 'end_log': end_log.isoformat(),
    'start_session': start.server_session_id, 'end_session': end.server_session_id,
    'start_token': start.round_token, 'end_token': end.round_token,
    'players': end.players or [], 'model': settings.ai_dossier_model,
    'rows': rows, 'communications': communications,
}, ensure_ascii=False))
db.close()
""".replace("ROUND_ID", str(int(round_id)))
    )


def _ssh_curl(path: str, payload: dict, *, timeout: int = 900) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    command = (
        f"curl -ksS --max-time {timeout} "
        f"-H \"Authorization: Bearer $(head -n 1 /run/credentials/qwen-llama.service/api-keys)\" "
        f"-H 'Content-Type: application/json' --data-binary @- {LLM_URL}{path}"
    )
    connection = _persistent_ssh(LLM_HOST)
    last_error = ""
    for _attempt in range(2):
        exit_code, stdout, stderr = connection.run(command, body, timeout=timeout)
        if exit_code == 0:
            return json.loads(stdout)
        last_error = stderr.decode("utf-8", errors="replace").strip()
        try:
            connection.client.close()
        except Exception:
            pass
        connection.client = None
        time.sleep(1)
    raise RuntimeError(f"remote llama request failed: {last_error}")


class RemoteTokenizer:
    mechanism = "llama-server-over-ssh:/tokenize"

    def __init__(self, *, progress_path: Path | None = None):
        self._cache: dict[str, int] = {}
        self.progress_path = progress_path
        self.round_id: int | None = None
        self.remote_calls = 0

    def count(self, text: str) -> int:
        if text not in self._cache:
            started = time.monotonic()
            result = _ssh_curl("/tokenize", {"content": text}, timeout=120)
            self._cache[text] = len(result["tokens"])
            self.remote_calls += 1
            _emit_progress(self.progress_path, {
                "kind": "tokenizer_call",
                "round_id": self.round_id,
                "call_count": self.remote_calls,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "tokens": self._cache[text],
            })
        return self._cache[text]

    def close(self) -> None:
        return None


def _executor_factory():
    def execute(*, messages, schema, model, idempotency_key):
        started = time.monotonic()
        response = _ssh_curl(
            "/v1/chat/completions",
            {
                "model": model or "Qwen3-14B-IQ3_M",
                "messages": messages,
                "max_tokens": 2048,
                "temperature": 0,
                "response_format": _request_schema(schema),
            },
        )
        return response, int((time.monotonic() - started) * 1000)
    return execute


class _FakeRoundQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class _FakeRoundDB:
    def __init__(self, value):
        self.value = value

    def query(self, model):
        return _FakeRoundQuery(self.value)


def _local_round_db(export: dict):
    start = SimpleNamespace(
        event_type="start", server_name=export["server"],
        created_at=datetime.fromisoformat(export["start_utc"]),
        server_session_id=export["start_session"], round_token=export["start_token"],
    )
    end = SimpleNamespace(
        id=export["round_id"], event_type="end", server_name=export["server"],
        created_at=datetime.fromisoformat(export["end_utc"]), paired_start=start,
        server_session_id=export["end_session"], round_token=export["end_token"],
        players=export["players"], mission=None, submarine_name=None, location=None,
        mission_completed=None, reached_station=None,
    )
    return _FakeRoundDB(end), end


def _comm_session(export: dict):
    from database import Base
    from models import PlayerCommunication, RoundEvent
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RoundEvent.__table__, PlayerCommunication.__table__])
    session = sessionmaker(bind=engine)()
    session.add(RoundEvent(id=export["round_id"], event_type="end", server_name=export["server"]))
    for row in export["communications"]:
        session.add(PlayerCommunication(
            id=row["id"], event_uid=row["uid"], source=row["source"], channel=row["channel"] or "default",
            server_name=export["server"], server_session_id=export["end_session"],
            round_event_id=export["round_id"], player_name=row["player_name"],
            original_text=row["original_text"], transcript_text=row["transcript_text"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
        ))
    session.commit()
    return session, engine


def _settings(export: dict):
    return SimpleNamespace(
        logs_default_timezone=export["timezone"],
        sftp_server_1_name=export["server"], sftp_server_1_logs_timezone=export["timezone"],
        sftp_server_2_name="ASPER2", sftp_server_2_logs_timezone="",
        ai_dossier_model=export["model"],
    )


def _region_checkpoint_key(region) -> str:
    payload = {
        "start_offset_ms": region.start_offset_ms,
        "end_offset_ms": region.end_offset_ms,
        "observation_fingerprints": list(region.observation_fingerprints),
        "hypothesis_indexes": list(region.hypothesis_indexes),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "regions": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("regions"), dict):
        raise RuntimeError(f"unsupported enrichment checkpoint: {path}")
    return payload


def _emit_progress(progress_path: Path | None, payload: dict) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    if progress_path is not None:
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()


def _region_summary(region) -> dict:
    return {
        "start_offset_ms": region.start_offset_ms,
        "end_offset_ms": region.end_offset_ms,
        "hypothesis_indexes": list(region.hypothesis_indexes),
        "fingerprint_count": len(region.observation_fingerprints),
    }


def _combine_enrichment_reports(round_id: int, regions: list, reports: list[dict], elapsed_ms: int) -> dict:
    region_reports = []
    incidents = []
    sums = {
        "enriched_calls": 0,
        "failed_closed_calls": 0,
        "raw_model_incidents": 0,
        "rejected_low_confidence": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "prompt_eval_time_ms": 0.0,
        "generation_time_ms": 0.0,
    }
    max_context_tokens = 0
    for report in reports:
        region_reports.extend(report.get("regions", []))
        incidents.extend(report.get("incidents", []))
        metrics = report.get("metrics", {})
        for key in sums:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                sums[key] += value
        max_context_tokens = max(max_context_tokens, int(metrics.get("max_context_tokens") or 0))
    merged, merge_count = merge_incidents(incidents)
    return {
        "round_id": int(round_id),
        "regions": region_reports,
        "incidents": merged,
        "metrics": {
            "coarse_regions": len(regions),
            **sums,
            "max_context_tokens": max_context_tokens,
            "final_candidate_count": len(merged),
            "merge_duplicate_incidents": merge_count,
            "total_elapsed_ms": elapsed_ms,
        },
    }


def _run_round(
    round_id: int,
    tokenizer,
    executor,
    *,
    checkpoint_dir: Path,
    region_index: int | None = None,
    progress_path: Path | None = None,
) -> dict:
    round_started = time.monotonic()
    delegate = getattr(tokenizer, "delegate", None)
    if delegate is not None:
        delegate.round_id = round_id
    _emit_progress(progress_path, {"kind": "round_start", "round_id": round_id})
    export = _round_export(round_id)
    round_db, _end = _local_round_db(export)
    settings = _settings(export)
    import services_log_analysis as analysis
    original_storage_ms = analysis._storage_ms
    analysis._storage_ms = lambda value: int(
        value.replace(tzinfo=ZoneInfo("Europe/Berlin")).timestamp() * 1000
    )
    with tempfile.TemporaryDirectory(prefix=f"nexus-v4-{round_id}-") as temp:
        try:
            log_path = Path(temp) / "logs.db"
            connection = sqlite3.connect(log_path)
            connection.execute(
                "CREATE TABLE log_events (id INTEGER, ts INTEGER, server TEXT, player TEXT, action TEXT, details TEXT, source_file TEXT)"
            )
            connection.executemany(
                "INSERT INTO log_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(row[1], row[0], row[5], row[2], row[3], row[4], row[6]) for row in export["rows"]],
            )
            connection.commit()
            connection.close()
            mapping, raw_rows, source_ranges = load_round_observations(
                round_db, round_id, settings=settings, log_db_path=str(log_path)
            )
            checkpoint = checkpoint_dir / f"v4-coarse-{round_id}.json"
            if checkpoint.exists():
                v3 = json.loads(checkpoint.read_text(encoding="utf-8"))["v3"]
            else:
                coarse_calls_path = checkpoint_dir / f"v4-coarse-calls-{round_id}.json"
                coarse_calls_payload = (
                    json.loads(coarse_calls_path.read_text(encoding="utf-8"))
                    if coarse_calls_path.exists()
                    else {"version": 1, "calls": []}
                )
                coarse_calls_payload.update({"version": 1, "round_id": round_id})

                def checkpointing_coarse_executor(**kwargs):
                    response, elapsed_ms = executor(**kwargs)
                    coarse_calls_payload.setdefault("calls", []).append({
                        "idempotency_key": kwargs.get("idempotency_key"),
                        "elapsed_ms": elapsed_ms,
                        "response": response,
                    })
                    _write_checkpoint(coarse_calls_path, coarse_calls_payload)
                    _emit_progress(progress_path, {
                        "kind": "coarse_model_call_checkpointed",
                        "round_id": round_id,
                        "call_count": len(coarse_calls_payload["calls"]),
                        "elapsed_ms": elapsed_ms,
                    })
                    return response, elapsed_ms

                v3 = run_dry_run(
                    db=round_db, round_id=round_id, tokenizer=tokenizer, executor=checkpointing_coarse_executor,
                    settings=settings, log_db_path=str(log_path),
                )
        finally:
            analysis._storage_ms = original_storage_ms
        raw_hypotheses = [
            hypothesis
            for window in v3["windows"]
            for hypothesis in window.get("raw_hypotheses", [])
        ]
        from services_log_analysis import compact_bursts, deduplicate_observations
        dedup = deduplicate_observations(raw_rows, source_ranges=source_ranges, round_start_ms=mapping.storage_start_ms + mapping.padding_ms)
        compact = compact_bursts(dedup.observations).observations
        duration_ms = int((mapping.end_utc - mapping.start_utc).total_seconds() * 1000)
        regions = build_coarse_regions(
            raw_hypotheses, dedup.observations,
            padding_ms=120_000, actor_gap_ms=120_000, max_component_span_ms=180_000,
            round_end_offset_ms=duration_ms,
        )
        if not checkpoint.exists():
            checkpoint.write_text(json.dumps({"v3": v3}, ensure_ascii=False), encoding="utf-8")
        aliases = build_actor_index(dedup.observations)
        enriched_checkpoint_path = checkpoint_dir / f"v4-enriched-{round_id}.json"
        enriched_checkpoint = _read_checkpoint(enriched_checkpoint_path)
        enriched_checkpoint.update({"version": 1, "round_id": round_id})
        selected_indexes = [region_index] if region_index is not None else list(range(len(regions)))
        if any(index < 0 or index >= len(regions) for index in selected_indexes):
            raise ValueError(f"region index outside 0..{len(regions) - 1}")
        batch_reports = []
        resumed_indexes = []
        comm_db, comm_engine = _comm_session(export)
        try:
            for current_index in selected_indexes:
                region = regions[current_index]
                key = _region_checkpoint_key(region)
                saved = enriched_checkpoint["regions"].get(key)
                if saved is not None:
                    batch_reports.append(saved["report"])
                    resumed_indexes.append(current_index)
                    _emit_progress(progress_path, {
                        "kind": "region_resume_skip",
                        "round_id": round_id,
                        "region_index": current_index,
                    })
                    continue
                completed_calls = []

                def on_progress(event, *, current_index=current_index):
                    _emit_progress(progress_path, {"kind": "stage", "round_id": round_id, **event})

                def on_region(event):
                    completed_calls.append(_scrub_report_names(event, aliases))

                report = run_enrichment_regions(
                    round_id=round_id, mapping=mapping, regions=[region],
                    compact_observations=compact, raw_observations=dedup.observations,
                    tokenizer=tokenizer, executor=executor, db=comm_db, settings=settings,
                    region_index_offset=current_index,
                    progress_callback=on_progress,
                    region_callback=on_region,
                )
                entry = {
                    "index": current_index,
                    "region": _region_summary(region),
                    "calls": completed_calls,
                    "report": _scrub_report_names(report, aliases),
                }
                enriched_checkpoint["regions"][key] = entry
                _write_checkpoint(enriched_checkpoint_path, enriched_checkpoint)
                batch_reports.append(entry["report"])
                _emit_progress(progress_path, {
                    "kind": "region_checkpointed",
                    "round_id": round_id,
                    "region_index": current_index,
                    "checkpoint": str(enriched_checkpoint_path),
                })
        finally:
            comm_db.close()
            comm_engine.dispose()
        v4 = _combine_enrichment_reports(
            round_id, regions, batch_reports,
            int((time.monotonic() - round_started) * 1000),
        )
        return {
            "round_id": round_id,
            "source_rows": len(export["rows"]),
            "communications_available": len(export["communications"]),
            "v3_metrics": v3["metrics"],
            "raw_hypotheses": len(raw_hypotheses),
            "coarse_regions": [_region_summary(r) for r in regions],
            "checkpoint": {
                "path": str(enriched_checkpoint_path),
                "completed_region_count": len(enriched_checkpoint["regions"]),
                "resumed_region_indexes": resumed_indexes,
            },
            "v4": v4,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", dest="round_ids", action="append", type=int, choices=(4357, 3951))
    parser.add_argument("--region-index", type=int)
    parser.add_argument("--checkpoint-dir", type=Path, default=Path("."))
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    round_ids = args.round_ids or [4357, 3951]
    if args.region_index is not None and len(round_ids) != 1:
        parser.error("--region-index requires exactly one --round")
    tokenizer = CachingTokenizer(RemoteTokenizer(progress_path=args.progress))
    executor = _executor_factory()
    try:
        results = [
            _run_round(
                round_id,
                tokenizer,
                executor,
                checkpoint_dir=args.checkpoint_dir,
                region_index=args.region_index,
                progress_path=args.progress,
            )
            for round_id in round_ids
        ]
    finally:
        tokenizer.close()
    payload = {"rounds": results}
    if args.output:
        _write_checkpoint(args.output, payload)
    print(json.dumps({
        "rounds": [
            {
                "round_id": result["round_id"],
                "coarse_regions": len(result["coarse_regions"]),
                "checkpoint": result["checkpoint"],
                "v4_metrics": result["v4"]["metrics"],
            }
            for result in results
        ],
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
