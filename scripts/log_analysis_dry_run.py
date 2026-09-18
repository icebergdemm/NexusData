"""Run retrospective log analysis for exactly one completed round.

Developer-only command.  It does not create moderation records and does not
register a scheduler task.
"""

from __future__ import annotations

import argparse
import json
import sys

from config import get_settings
from database import SessionLocal
from services_log_analysis import (
    AnalysisError,
    QueueExecutor,
    build_exact_tokenizer,
    run_dry_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run Qwen log analysis for one completed round")
    parser.add_argument("--round-id", type=int, required=True, help="completed RoundEvent end id")
    parser.add_argument(
        "--tokenizer-command",
        default=None,
        help="explicit pinned tokenizer command reading prompt text from stdin",
    )
    parser.add_argument(
        "--tokenizer-url",
        default=None,
        help="optional llama-server URL override; /tokenize is derived from it",
    )
    parser.add_argument("--log-db-path", default=None, help="developer override for the indexed SQLite log database")
    parser.add_argument(
        "--region-start-offset-ms",
        type=int,
        default=None,
        help="optional inclusive round-relative analysis region start",
    )
    parser.add_argument(
        "--region-end-offset-ms",
        type=int,
        default=None,
        help="optional inclusive round-relative analysis region end",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    tokenizer = None
    db = None
    worker_started = False
    try:
        tokenizer = build_exact_tokenizer(
            settings,
            tokenizer_command=args.tokenizer_command,
            tokenizer_url=args.tokenizer_url,
        )
        db = SessionLocal()
        # A standalone developer invocation has no application worker thread.
        # Start only the existing single-flight queue worker for this command;
        # this is not a scheduler or an automatic round hook.
        from services_llm_worker import start_ai_llm_worker, stop_ai_llm_worker

        start_ai_llm_worker()
        worker_started = True
        report = run_dry_run(
            db=db,
            round_id=args.round_id,
            tokenizer=tokenizer,
            executor=QueueExecutor(),
            settings=settings,
            log_db_path=args.log_db_path,
            region_start_offset_ms=args.region_start_offset_ms,
            region_end_offset_ms=args.region_end_offset_ms,
        )
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (AnalysisError, ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if db is not None:
            db.close()
        if worker_started:
            stop_ai_llm_worker()
        if tokenizer is not None:
            tokenizer.close()


if __name__ == "__main__":
    raise SystemExit(main())
