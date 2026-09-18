"""Detached V4 probe runner for the production NexusData host.

This is a local-only harness. It runs beside the production source base, reads
round/log/communication data without committing anything, and calls the
configured production OpenAI-compatible HTTPS endpoint directly. It never uses
the durable LLM queue, so a probe cannot leave production tickets behind.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from config import get_settings
from database import SessionLocal
from services_ai_client import close_ai_client, post_chat_completions
from services_log_analysis import (
    CachingTokenizer,
    LlamaServerTokenizer,
    _request_schema,
    build_actor_index,
    build_coarse_regions,
    compact_bursts,
    deduplicate_observations,
    load_round_communications,
    load_round_observations,
    run_dry_run,
    run_enrichment_regions,
)


def _executor(*, messages, schema, model, idempotency_key):
    del idempotency_key
    started = time.monotonic()
    payload, _transport_ms = post_chat_completions(
        {
            "model": model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0,
            "response_format": _request_schema(schema),
        },
        timeout=float(get_settings().ai_dossier_timeout_seconds),
    )
    return payload, int((time.monotonic() - started) * 1000)


def _communication_summary(db, mapping, regions, log_observations):
    aliases = build_actor_index(log_observations)
    result = []
    for region_index, region in enumerate(regions):
        records = load_round_communications(db, mapping, region)
        result.append({
            "region_index": region_index,
            "count": len(records),
            "records": [
                {
                    "index": index,
                    "timestamp_offset_ms": record.timestamp_offset_ms,
                    "actor": aliases.get(record.actor),
                    "source": record.source,
                    "channel": record.channel,
                    "text": record.text,
                }
                for index, record in enumerate(records)
            ],
        })
    return result


def _run_round(round_id: int, tokenizer, executor, checkpoint_dir: Path | None):
    settings = get_settings()
    db = SessionLocal()
    started = time.monotonic()
    try:
        mapping, raw_rows, source_ranges = load_round_observations(
            db, round_id, settings=settings,
        )
        checkpoint = (checkpoint_dir / f"v4-coarse-{round_id}.json") if checkpoint_dir else None
        if round_id == 4357 and checkpoint and checkpoint.exists():
            checkpoint_payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            v3 = checkpoint_payload["v3"]
            if (
                v3.get("prompt_version") != "log-analysis-dry-run.v3"
                or v3.get("schema_version") != "log-analysis-output.v3"
                or v3.get("preprocess_version") != "log-analysis-preprocess.v3"
            ):
                raise RuntimeError("4357 checkpoint versions are not the current V3 contract")
            coarse_source = "checkpoint"
        else:
            v3 = run_dry_run(
                db=db, round_id=round_id, tokenizer=tokenizer, executor=executor,
                settings=settings,
            )
            coarse_source = "fresh"

        dedup = deduplicate_observations(
            raw_rows,
            source_ranges=source_ranges,
            round_start_ms=mapping.storage_start_ms + mapping.padding_ms,
        )
        compact = compact_bursts(dedup.observations).observations
        raw_hypotheses = [
            hypothesis
            for window in v3["windows"]
            for hypothesis in window.get("raw_hypotheses", [])
        ]
        duration_ms = int((mapping.end_utc - mapping.start_utc).total_seconds() * 1000)
        regions = build_coarse_regions(
            raw_hypotheses,
            dedup.observations,
            padding_ms=120_000,
            actor_gap_ms=120_000,
            max_component_span_ms=180_000,
            round_end_offset_ms=duration_ms,
        )
        aliases = build_actor_index(dedup.observations)
        coarse_summary = [
            {
                "start_offset_ms": region.start_offset_ms,
                "end_offset_ms": region.end_offset_ms,
                "actors": sorted({aliases.get(actor) for actor in region.actors if aliases.get(actor) is not None}),
                "hypothesis_indexes": list(region.hypothesis_indexes),
                "fingerprint_count": len(region.observation_fingerprints),
            }
            for region in regions
        ]
        v4 = run_enrichment_regions(
            round_id=round_id,
            mapping=mapping,
            regions=regions,
            compact_observations=compact,
            raw_observations=dedup.observations,
            tokenizer=tokenizer,
            executor=executor,
            db=db,
            settings=settings,
        )
        return {
            "round_id": round_id,
            "coarse_source": coarse_source,
            "source_rows": len(raw_rows),
            "preprocessing": {
                "raw_observations": len(dedup.observations),
                "compact_observations": len(compact),
                "raw_to_compact_ratio": len(compact) / len(dedup.observations) if dedup.observations else 1.0,
            },
            "v3_metrics": v3["metrics"],
            "raw_hypotheses": len(raw_hypotheses),
            "coarse_regions": coarse_summary,
            "communications": _communication_summary(db, mapping, regions, compact),
            "v4": v4,
            "probe_elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    settings = get_settings()
    tokenizer = CachingTokenizer(
        LlamaServerTokenizer(
            completions_url=settings.ai_dossier_url,
            api_key=settings.ai_dossier_api_key,
            model=settings.ai_dossier_model,
            verify=settings.ai_dossier_tls_ca_file or True,
            timeout=min(float(settings.ai_dossier_timeout_seconds), 60.0),
        )
    )
    started = time.monotonic()
    try:
        tokenizer.delegate.verify_runtime()
        results = [
            _run_round(round_id, tokenizer, _executor, args.checkpoint_dir)
            for round_id in (4357, 3951)
        ]
        payload = {
            "execution": "temporary server-side environment via production HTTPS",
            "model": settings.ai_dossier_model,
            "rounds": results,
            "total_elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        tokenizer.close()
        close_ai_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
