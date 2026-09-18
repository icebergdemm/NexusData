"""Retrospective, developer-only analysis of one completed Barotrauma round.

This module deliberately has no scheduler, moderation persistence, or gameplay
side effects.  It turns the existing indexed server log into bounded prompts,
uses the existing LLM queue, and returns an inspectable dry-run report.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

logger = logging.getLogger(__name__)

CONTEXT_TOKENS = 16_384
MAX_OUTPUT_TOKENS = 2_048
MAX_PROMPT_TOKENS = 14_000
EVENT_TOKEN_TARGET = 10_500
OVERLAP_MS = 45_000
BURST_MAX_GAP_MS = 1_000
BURST_MAX_SPAN_MS = 10_000
MAX_STATE_TOKENS = 600
MIN_INCIDENT_CONFIDENCE = 60
MAX_BENIGN_ALTERNATIVE_LENGTH = 220
MAX_WHY_STILL_SUSPICIOUS_LENGTH = 260
PROMPT_VERSION = "log-analysis-dry-run.v3"
SCHEMA_VERSION = "log-analysis-output.v3"
PREPROCESS_VERSION = "log-analysis-preprocess.v3"
ALLOWED_CATEGORIES = (
    "possible_sabotage",
    "possible_griefing",
    "suspicious_sequence",
    "unclear_harm",
)
EVIDENCE_DIMENSIONS = ("action_evidence", "consequence_evidence", "intent_evidence")


class AnalysisError(RuntimeError):
    """Expected, reportable dry-run failure."""


class RoundLogMappingError(AnalysisError):
    """The requested round cannot be mapped to logs unambiguously."""


class TokenizerUnavailable(AnalysisError):
    """No exact tokenizer mechanism is available."""


class LLMResponseError(AnalysisError):
    """The queue or model returned an unusable result."""


@dataclass(frozen=True)
class RawObservation:
    timestamp_ms: int
    server: str
    player: str
    action: str
    details: str
    source_file: str
    source_row_id: int


@dataclass(frozen=True)
class Observation:
    timestamp_ms: int
    round_offset_ms: int
    server: str
    player: str
    action: str
    details: str
    source_files: tuple[str, ...]
    source_row_ids: tuple[int, ...]
    multiplicity: int
    dedup_reason: str
    fingerprint: str
    first_timestamp_ms: int
    last_timestamp_ms: int
    member_count: int
    member_fingerprints: tuple[str, ...]


@dataclass
class DedupDiagnostics:
    input_rows: int = 0
    output_observations: int = 0
    removed_by_reason: dict[str, int] = field(default_factory=dict)
    ambiguous_cross_source_groups: int = 0
    same_source_repetition_rows: int = 0


@dataclass(frozen=True)
class DedupResult:
    observations: tuple[Observation, ...]
    diagnostics: DedupDiagnostics


@dataclass(frozen=True)
class BurstCompactionResult:
    observations: tuple[Observation, ...]
    input_count: int
    output_count: int
    compacted_member_count: int
    member_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class CoarseRegion:
    """One deterministic bounded region produced from raw V3 hypotheses."""

    start_offset_ms: int
    end_offset_ms: int
    actors: tuple[str, ...]
    observation_fingerprints: tuple[str, ...]
    hypothesis_indexes: tuple[int, ...]


@dataclass(frozen=True)
class CommunicationRecord:
    """Existing persisted chat context, detached from the ORM row."""

    fingerprint: str
    timestamp_offset_ms: int
    actor: str
    source: str
    text: str
    raw_id: int
    channel: str = ""


@dataclass(frozen=True)
class ReplayContextRecord:
    """Reserved shape for an already parsed replay event, if one exists."""

    fingerprint: str
    timestamp_offset_ms: int
    actor: str
    action: str
    details: str
    raw_id: int


@dataclass(frozen=True)
class RoundLogMapping:
    round_id: int
    server: str
    start_utc: datetime
    end_utc: datetime
    timezone_name: str
    start_log_clock: datetime
    end_log_clock: datetime
    storage_start_ms: int
    storage_end_ms: int
    padding_ms: int
    method: str
    diagnostics: tuple[str, ...] = ()
    round_context: Mapping[str, Any] = field(default_factory=dict)
    actor_roles: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisWindow:
    index: int
    start_index: int
    end_index: int
    observations: tuple[Observation, ...]
    actors: Mapping[str, int]
    event_text: str
    event_tokens: int
    preflight_prompt_tokens: int
    overlap_observations: int
    state_tokens_before: int
    context_tokens: int = 0


@dataclass(frozen=True)
class WindowLLMResult:
    window: AnalysisWindow
    response: dict[str, Any]
    next_state: dict[str, list[str]]
    elapsed_ms: int
    server_prompt_tokens: int | None
    completion_tokens: int | None
    validation_warnings: tuple[str, ...] = ()


class Tokenizer(Protocol):
    mechanism: str

    def count(self, text: str) -> int:
        ...


def _normalise(value: str | None) -> str:
    return " ".join((value or "").split())


def _canonical_tuple(row: RawObservation) -> tuple[int, str, str, str, str]:
    return (
        int(row.timestamp_ms),
        _normalise(row.server),
        _normalise(row.player),
        _normalise(row.action),
        _normalise(row.details),
    )


def _ranges_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


def deduplicate_observations(
    rows: Iterable[RawObservation],
    *,
    source_ranges: Mapping[str, tuple[int, int]] | None = None,
    round_start_ms: int | None = None,
) -> DedupResult:
    """Normalize and conservatively collapse proven cross-file overlap.

    Repeated tuples inside one source file are retained.  For a tuple seen in
    multiple files, one copy per occurrence ordinal is collapsed only when the
    participating file ranges overlap.  If that cannot be demonstrated, all
    copies remain and the report records the ambiguity.
    """
    source_ranges = source_ranges or {}
    normalized = [
        RawObservation(
            timestamp_ms=int(row.timestamp_ms),
            server=_normalise(row.server),
            player=_normalise(row.player),
            action=_normalise(row.action),
            details=_normalise(row.details),
            source_file=_normalise(row.source_file),
            source_row_id=int(row.source_row_id),
        )
        for row in rows
    ]
    groups: dict[tuple[int, str, str, str, str], list[RawObservation]] = defaultdict(list)
    for row in normalized:
        groups[_canonical_tuple(row)].append(row)

    diagnostics = DedupDiagnostics(input_rows=len(normalized))
    representatives: list[tuple[RawObservation, tuple[str, ...], tuple[int, ...], int, str]] = []

    for key, group in groups.items():
        by_source: dict[str, list[RawObservation]] = defaultdict(list)
        for row in sorted(group, key=lambda item: (item.source_file, item.source_row_id)):
            by_source[row.source_file].append(row)
        files = sorted(by_source)
        repeated_in_source = any(len(items) > 1 for items in by_source.values())
        if repeated_in_source:
            diagnostics.same_source_repetition_rows += sum(
                len(items) for items in by_source.values() if len(items) > 1
            )

        proven_overlap = False
        if len(files) > 1:
            file_pairs = [(files[i], files[j]) for i in range(len(files)) for j in range(i + 1, len(files))]
            proven_overlap = all(
                left in source_ranges and right in source_ranges and _ranges_overlap(
                    source_ranges[left], source_ranges[right]
                )
                for left, right in file_pairs
            )

        if len(files) > 1 and proven_overlap:
            occurrence_count = max(len(items) for items in by_source.values())
            for ordinal in range(occurrence_count):
                candidates = [items[ordinal] for items in by_source.values() if ordinal < len(items)]
                representative = min(candidates, key=lambda item: (item.source_file, item.source_row_id))
                source_files = tuple(sorted(item.source_file for item in candidates))
                source_ids = tuple(sorted(item.source_row_id for item in candidates))
                removed = len(candidates) - 1
                if removed:
                    diagnostics.removed_by_reason["overlapping_source_files"] = (
                        diagnostics.removed_by_reason.get("overlapping_source_files", 0) + removed
                    )
                    reason = "overlapping_source_files"
                elif len(files) > 1:
                    reason = "same_source_repetition"
                else:
                    reason = "none"
                representatives.append((representative, source_files, source_ids, len(candidates), reason))
        else:
            if len(files) > 1:
                diagnostics.ambiguous_cross_source_groups += 1
                reason = "ambiguous_cross_source"
            elif repeated_in_source:
                reason = "same_source_repetition"
            else:
                reason = "none"
            for row in sorted(group, key=lambda item: (item.source_file, item.source_row_id)):
                representatives.append((row, (row.source_file,), (row.source_row_id,), 1, reason))

    representatives.sort(key=lambda item: (
        item[0].timestamp_ms,
        item[0].source_file,
        item[0].source_row_id,
        item[0].action,
        item[0].details,
    ))
    occurrence_numbers: Counter[tuple[int, str, str, str, str]] = Counter()
    observations: list[Observation] = []
    start_ms = round_start_ms or (representatives[0][0].timestamp_ms if representatives else 0)
    for row, source_files, source_ids, multiplicity, reason in representatives:
        key = _canonical_tuple(row)
        ordinal = occurrence_numbers[key]
        occurrence_numbers[key] += 1
        fingerprint_payload = {
            "version": PREPROCESS_VERSION,
            "timestamp_ms": row.timestamp_ms,
            "server": row.server,
            "player": row.player,
            "action": row.action,
            "details": row.details,
            "occurrence": ordinal,
            "source_files": source_files,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        observations.append(
            Observation(
                timestamp_ms=row.timestamp_ms,
                round_offset_ms=row.timestamp_ms - start_ms,
                server=row.server,
                player=row.player,
                action=row.action,
                details=row.details,
                source_files=source_files,
                source_row_ids=source_ids,
                multiplicity=multiplicity,
                dedup_reason=reason,
                fingerprint=fingerprint,
                first_timestamp_ms=row.timestamp_ms,
                last_timestamp_ms=row.timestamp_ms,
                member_count=1,
                member_fingerprints=(fingerprint,),
            )
        )
    diagnostics.output_observations = len(observations)
    return DedupResult(tuple(observations), diagnostics)


def compact_bursts(
    observations: Sequence[Observation],
    *,
    max_gap_ms: int = BURST_MAX_GAP_MS,
    max_span_ms: int = BURST_MAX_SPAN_MS,
) -> BurstCompactionResult:
    """Compact adjacent exact repeats without filtering any event category."""
    if max_gap_ms < 0 or max_span_ms < 0:
        raise ValueError("burst time limits must be non-negative")
    raw = tuple(observations)
    if not raw:
        return BurstCompactionResult((), 0, 0, 0, ())
    if any(
        item.timestamp_ms < previous.timestamp_ms
        for previous, item in zip(raw, raw[1:])
    ):
        raise ValueError("observations must be in canonical timestamp order")

    def compatible(previous: Observation, first: Observation, current: Observation) -> bool:
        return (
            current.player == first.player
            and current.action == first.action
            and current.details == first.details
            and current.timestamp_ms - previous.timestamp_ms <= max_gap_ms
            and current.timestamp_ms - first.timestamp_ms <= max_span_ms
        )

    def build_group(group: Sequence[Observation]) -> Observation:
        first = group[0]
        if len(group) == 1:
            return first
        member_fingerprints = tuple(
            fingerprint
            for item in group
            for fingerprint in item.member_fingerprints
        )
        if len(member_fingerprints) != sum(item.member_count for item in group):
            raise AnalysisError("burst compaction member metadata is inconsistent")
        compact_payload = {
            "version": f"{PREPROCESS_VERSION}:burst",
            "members": member_fingerprints,
            "first_timestamp_ms": first.first_timestamp_ms,
            "last_timestamp_ms": group[-1].last_timestamp_ms,
        }
        fingerprint = hashlib.sha256(
            json.dumps(compact_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        source_files = tuple(dict.fromkeys(
            source_file for item in group for source_file in item.source_files
        ))
        source_row_ids = tuple(
            source_row_id for item in group for source_row_id in item.source_row_ids
        )
        return Observation(
            timestamp_ms=first.timestamp_ms,
            round_offset_ms=first.round_offset_ms,
            server=first.server,
            player=first.player,
            action=first.action,
            details=first.details,
            source_files=source_files,
            source_row_ids=source_row_ids,
            multiplicity=sum(item.multiplicity for item in group),
            dedup_reason="burst_compacted",
            fingerprint=fingerprint,
            first_timestamp_ms=first.first_timestamp_ms,
            last_timestamp_ms=group[-1].last_timestamp_ms,
            member_count=sum(item.member_count for item in group),
            member_fingerprints=member_fingerprints,
        )

    compacted: list[Observation] = []
    group: list[Observation] = [raw[0]]
    for current in raw[1:]:
        if compatible(group[-1], group[0], current):
            group.append(current)
            continue
        compacted.append(build_group(group))
        group = [current]
    compacted.append(build_group(group))
    members = tuple(
        fingerprint
        for item in compacted
        for fingerprint in item.member_fingerprints
    )
    expected = tuple(item.member_fingerprints for item in raw)
    flattened_expected = tuple(fingerprint for item in expected for fingerprint in item)
    if members != flattened_expected:
        raise AnalysisError("burst compaction changed member ordering or coverage")
    compacted_member_count = sum(
        item.member_count for item in compacted if item.member_count > 1
    )
    return BurstCompactionResult(
        observations=tuple(compacted),
        input_count=len(raw),
        output_count=len(compacted),
        compacted_member_count=compacted_member_count,
        member_fingerprints=members,
    )


def _incident_fingerprints(incident: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for field_name in ("evidence", *EVIDENCE_DIMENSIONS):
        field = incident.get(field_name)
        if not isinstance(field, list):
            continue
        values.extend(value for value in field if isinstance(value, str) and value)
    snapshots = incident.get("evidence_snapshots")
    if isinstance(snapshots, list):
        values.extend(
            item.get("fingerprint")
            for item in snapshots
            if isinstance(item, Mapping) and isinstance(item.get("fingerprint"), str)
        )
    return tuple(dict.fromkeys(values))


def build_coarse_regions(
    incidents: Sequence[Mapping[str, Any]],
    observations: Sequence[Observation],
    *,
    padding_ms: int = 120_000,
    actor_gap_ms: int = 120_000,
    max_component_span_ms: int = 180_000,
    round_end_offset_ms: int | None = None,
) -> tuple[CoarseRegion, ...]:
    """Group raw V3 hypotheses without assigning suspicious meaning.

    The grouping is deliberately boring: shared evidence, overlapping ranges,
    or a shared actor inside a bounded gap.  It is only a request-budgeting
    step; the enriched model still makes the candidate decision.
    """
    if padding_ms < 0 or actor_gap_ms < 0 or max_component_span_ms < 0:
        raise ValueError("coarse region limits must be non-negative")
    by_fingerprint = {item.fingerprint: item for item in observations}
    hypotheses: list[dict[str, Any]] = []
    for hypothesis_index, incident in enumerate(incidents):
        fingerprints = _incident_fingerprints(incident)
        members = [by_fingerprint[value] for value in fingerprints if value in by_fingerprint]
        if not members:
            continue
        start = min(item.round_offset_ms for item in members)
        end = max(
            item.round_offset_ms + item.last_timestamp_ms - item.first_timestamp_ms
            for item in members
        )
        actors = frozenset(item.player for item in members if item.player)
        hypotheses.append({
            "index": hypothesis_index,
            "start": max(0, start),
            "end": max(0, end),
            "actors": actors,
            "fingerprints": frozenset(fingerprints),
        })
    if not hypotheses:
        return ()

    parent = list(range(len(hypotheses)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(hypotheses):
        for right_index in range(left_index + 1, len(hypotheses)):
            right = hypotheses[right_index]
            ranges_overlap = left["start"] <= right["end"] and right["start"] <= left["end"]
            shared_evidence = bool(left["fingerprints"] & right["fingerprints"])
            gap = max(right["start"] - left["end"], left["start"] - right["end"], 0)
            shared_actor_nearby = bool(left["actors"] & right["actors"]) and gap <= actor_gap_ms
            left_root, right_root = find(left_index), find(right_index)
            left_component = [item for index, item in enumerate(hypotheses) if find(index) == left_root]
            right_component = [item for index, item in enumerate(hypotheses) if find(index) == right_root]
            combined_start = min(item["start"] for item in (*left_component, *right_component))
            combined_end = max(item["end"] for item in (*left_component, *right_component))
            within_span = combined_end - combined_start <= max_component_span_ms
            if within_span and (ranges_overlap or shared_evidence or shared_actor_nearby):
                union(left_index, right_index)

    components: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, hypothesis in enumerate(hypotheses):
        components[find(index)].append(hypothesis)
    regions: list[CoarseRegion] = []
    ordered_observations = tuple(observations)
    for component in sorted(components.values(), key=lambda items: (min(item["start"] for item in items), min(item["index"] for item in items))):
        start = max(0, min(item["start"] for item in component) - padding_ms)
        end = max(item["end"] for item in component) + padding_ms
        if round_end_offset_ms is not None:
            end = min(max(0, int(round_end_offset_ms)), end)
        fingerprints = {
            value for item in component for value in item["fingerprints"]
        }
        ordered_fingerprints = tuple(
            item.fingerprint for item in ordered_observations if item.fingerprint in fingerprints
        )
        actors = tuple(sorted(
            {actor for item in component for actor in item["actors"]},
            key=lambda value: (value.casefold(), value),
        ))
        regions.append(CoarseRegion(
            start_offset_ms=start,
            end_offset_ms=max(start, end),
            actors=actors,
            observation_fingerprints=ordered_fingerprints,
            hypothesis_indexes=tuple(sorted(item["index"] for item in component)),
        ))
    return tuple(regions)


def build_actor_index(observations: Sequence[Observation]) -> dict[str, int]:
    actors: dict[str, int] = {}
    for observation in observations:
        name = observation.player.strip()
        if name and name not in actors:
            actors[name] = len(actors)
    return actors


def _redact_known_names(value: str, actors: Mapping[str, int]) -> str:
    result = value
    for name, actor_index in sorted(actors.items(), key=lambda item: (-len(item[0]), item[0].casefold())):
        if name:
            result = re.sub(re.escape(name), f"actor[{actor_index}]", result, flags=re.IGNORECASE)
    return result


def _scrub_report_names(value: Any, actors: Mapping[str, int]) -> Any:
    """Remove any player names the model may have repeated in free text."""
    if isinstance(value, str):
        return _redact_known_names(value, actors)
    if isinstance(value, list):
        return [_scrub_report_names(item, actors) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_report_names(item, actors) for key, item in value.items()}
    return value


def build_event_text(observations: Sequence[Observation], actors: Mapping[str, int]) -> str:
    lines = []
    for local_index, observation in enumerate(observations):
        actor_index = actors.get(observation.player, -1)
        details = _redact_known_names(observation.details, actors).replace("\\", "\\\\").replace('"', '\\"')
        action = observation.action.replace('"', '\\"')
        lines.append(
            f'[{local_index},{observation.round_offset_ms / 1000:.3f},{actor_index},'
            f'"{action}","{details}",count={observation.member_count},'
            f'first={observation.round_offset_ms / 1000:.3f},'
            f'last={(observation.round_offset_ms + observation.last_timestamp_ms - observation.first_timestamp_ms) / 1000:.3f}]'
        )
    return "\n".join(lines)


def build_analysis_schema(event_count: int, actor_count: int) -> dict[str, Any]:
    if event_count < 1:
        raise ValueError("event_count must be positive")
    actor_items: dict[str, Any] = {"type": "integer", "minimum": 0, "maximum": max(actor_count - 1, 0)}
    actor_schema: dict[str, Any] = {"type": "array", "maxItems": 16, "items": actor_items}
    if actor_count == 0:
        actor_schema["maxItems"] = 0
    incident = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "category", "severity", "confidence", "actors", *EVIDENCE_DIMENSIONS,
            "summary", "benign_alternative", "why_still_suspicious", "uncertainty",
        ],
        "properties": {
            "category": {"type": "string", "enum": list(ALLOWED_CATEGORIES)},
            "severity": {"type": "integer", "minimum": 1, "maximum": 3},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "actors": actor_schema,
            **{
                field_name: {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "items": {"type": "integer", "minimum": 0, "maximum": event_count - 1},
                }
                for field_name in EVIDENCE_DIMENSIONS
            },
            "summary": {"type": "string", "minLength": 1, "maxLength": 320},
            "benign_alternative": {"type": "string", "minLength": 1, "maxLength": MAX_BENIGN_ALTERNATIVE_LENGTH},
            "why_still_suspicious": {"type": "string", "minLength": 1, "maxLength": MAX_WHY_STILL_SUSPICIOUS_LENGTH},
            "uncertainty": {"type": "string", "minLength": 1, "maxLength": 220},
        },
    }
    report = {
        "type": "object",
        "additionalProperties": False,
        "required": ["incidents", "state"],
        "properties": {
            "incidents": {"type": "array", "maxItems": 8, "items": incident},
            "state": {
                "type": "object",
                "additionalProperties": False,
                "required": ["facts", "open_sequences", "open_questions"],
                "properties": {
                    "facts": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 160}},
                    "open_sequences": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
                    "open_questions": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 160}},
                },
            },
        },
    }
    return report


def validate_state(state: Any) -> dict[str, list[str]]:
    if not isinstance(state, dict) or set(state) != {"facts", "open_sequences", "open_questions"}:
        raise ValueError("state must contain exactly facts, open_sequences, open_questions")
    limits = {"facts": (12, 160), "open_sequences": (8, 200), "open_questions": (8, 160)}
    clean: dict[str, list[str]] = {}
    for key, (max_items, max_length) in limits.items():
        value = state[key]
        if not isinstance(value, list) or len(value) > max_items:
            raise ValueError(f"state {key} exceeds item limit")
        if any(not isinstance(item, str) or not item.strip() or len(item) > max_length for item in value):
            raise ValueError(f"state {key} contains an invalid item")
        clean[key] = [item.strip() for item in value]
    return clean


def validate_model_result(value: Any, *, event_count: int, actor_count: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"incidents", "state"}:
        raise ValueError("model result must contain exactly incidents and state")
    incidents = value["incidents"]
    if not isinstance(incidents, list) or len(incidents) > 8:
        raise ValueError("incidents exceeds maxItems")
    validated: list[dict[str, Any]] = []
    for incident in incidents:
        if not isinstance(incident, dict):
            raise ValueError("incident must be an object")
        required = {
            "category", "severity", "confidence", "actors", *EVIDENCE_DIMENSIONS, "summary",
            "benign_alternative", "why_still_suspicious", "uncertainty",
        }
        if set(incident) != required:
            missing = sorted(required - set(incident))
            raise ValueError(f"incident has unexpected or missing fields: {missing}")
        if incident["category"] not in ALLOWED_CATEGORIES:
            raise ValueError("unknown incident category")
        if isinstance(incident["severity"], bool) or not isinstance(incident["severity"], int) or not 1 <= incident["severity"] <= 3:
            raise ValueError("invalid severity")
        if isinstance(incident["confidence"], bool) or not isinstance(incident["confidence"], int) or not 0 <= incident["confidence"] <= 100:
            raise ValueError("invalid confidence")
        actors = incident["actors"]
        if not isinstance(actors, list) or len(actors) > 16 or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0 or item >= actor_count for item in actors
        ):
            raise ValueError("invalid actor index")
        evidence_by_dimension: dict[str, list[int]] = {}
        for field_name in EVIDENCE_DIMENSIONS:
            evidence = incident[field_name]
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"incident needs at least one {field_name} index")
            if len(evidence) > 32 or len(set(evidence)) != len(evidence) or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0 or item >= event_count
                for item in evidence
            ):
                raise ValueError(f"invalid {field_name} index")
            evidence_by_dimension[field_name] = list(evidence)
        for field_name, max_length in (
            ("summary", 320),
            ("benign_alternative", MAX_BENIGN_ALTERNATIVE_LENGTH),
            ("why_still_suspicious", MAX_WHY_STILL_SUSPICIOUS_LENGTH),
            ("uncertainty", 220),
        ):
            if not isinstance(incident[field_name], str) or not incident[field_name].strip() or len(incident[field_name]) > max_length:
                raise ValueError(f"invalid incident {field_name}")
        validated.append({
            "category": incident["category"],
            "severity": incident["severity"],
            "confidence": incident["confidence"],
            "actors": list(actors),
            **evidence_by_dimension,
            "summary": incident["summary"].strip(),
            "benign_alternative": incident["benign_alternative"].strip(),
            "why_still_suspicious": incident["why_still_suspicious"].strip(),
            "uncertainty": incident["uncertainty"].strip(),
        })
    return {"incidents": validated, "state": validate_state(value["state"])}


def _enrichment_ref_schema(allowed_sources: Sequence[str], max_count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "index"],
        "properties": {
            "source": {"type": "string", "enum": list(allowed_sources)},
            "index": {"type": "integer", "minimum": 0, "maximum": max(0, max_count - 1)},
        },
    }


def build_enrichment_schema(
    *,
    log_count: int,
    communication_count: int,
    replay_count: int,
    actor_count: int,
) -> dict[str, Any]:
    """Build the small source-aware schema for one bounded region."""
    if log_count < 1:
        raise ValueError("enriched region must contain a log observation")
    actor_schema: dict[str, Any] = {
        "type": "array",
        "maxItems": 16,
        "items": {"type": "integer", "minimum": 0, "maximum": max(0, actor_count - 1)},
    }
    if actor_count == 0:
        actor_schema["maxItems"] = 0
    available = {
        "log": log_count,
        "communication": communication_count,
        "replay": replay_count,
    }
    consequence_sources = ["log"] + (["replay"] if replay_count else [])
    intent_sources = ["log"] + (["communication"] if communication_count else []) + (["replay"] if replay_count else [])
    max_count = max(available.values())

    def evidence_field(sources: Sequence[str]) -> dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": _enrichment_ref_schema(sources, max_count),
        }

    incident = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "category", "severity", "confidence", "actors", *EVIDENCE_DIMENSIONS,
            "summary", "benign_alternative", "why_still_suspicious", "uncertainty",
        ],
        "properties": {
            "category": {"type": "string", "enum": list(ALLOWED_CATEGORIES)},
            "severity": {"type": "integer", "minimum": 1, "maximum": 3},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "actors": actor_schema,
            "action_evidence": evidence_field(["log"]),
            "consequence_evidence": evidence_field(consequence_sources),
            "intent_evidence": evidence_field(intent_sources),
            "summary": {"type": "string", "minLength": 1, "maxLength": 320},
            "benign_alternative": {"type": "string", "minLength": 1, "maxLength": MAX_BENIGN_ALTERNATIVE_LENGTH},
            "why_still_suspicious": {"type": "string", "minLength": 1, "maxLength": MAX_WHY_STILL_SUSPICIOUS_LENGTH},
            "uncertainty": {"type": "string", "minLength": 1, "maxLength": 220},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["incidents", "state"],
        "properties": {
            "incidents": {"type": "array", "maxItems": 8, "items": incident},
            "state": {
                "type": "object",
                "additionalProperties": False,
                "required": ["facts", "open_sequences", "open_questions"],
                "properties": {
                    "facts": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 160}},
                    "open_sequences": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
                    "open_questions": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 160}},
                },
            },
        },
    }


def _validate_enrichment_refs(
    values: Any,
    *,
    field_name: str,
    allowed_sources: set[str],
    counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values or len(values) > 32:
        raise ValueError(f"{field_name} needs at least one source index")
    clean: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"source", "index"}:
            raise ValueError(f"invalid {field_name} reference")
        source = value["source"]
        index = value["index"]
        if source not in allowed_sources:
            raise ValueError(f"{field_name} source is not allowed")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < counts.get(source, 0):
            raise ValueError(f"invalid {field_name} index")
        key = (source, index)
        if key in seen:
            raise ValueError(f"duplicate {field_name} index")
        seen.add(key)
        clean.append({"source": source, "index": index})
    return clean


def validate_enriched_model_result(
    value: Any,
    *,
    log_count: int,
    communication_count: int,
    replay_count: int,
    actor_count: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"incidents", "state"}:
        raise ValueError("enriched model result must contain exactly incidents and state")
    incidents = value["incidents"]
    if not isinstance(incidents, list) or len(incidents) > 8:
        raise ValueError("enriched incidents exceeds maxItems")
    counts = {"log": log_count, "communication": communication_count, "replay": replay_count}
    allowed = {
        "action_evidence": {"log"},
        "consequence_evidence": {"log", "replay"},
        "intent_evidence": {"log", "communication", "replay"},
    }
    validated: list[dict[str, Any]] = []
    for incident in incidents:
        if not isinstance(incident, dict):
            raise ValueError("enriched incident must be an object")
        required = {
            "category", "severity", "confidence", "actors", *EVIDENCE_DIMENSIONS, "summary",
            "benign_alternative", "why_still_suspicious", "uncertainty",
        }
        if set(incident) != required:
            raise ValueError("enriched incident has unexpected or missing fields")
        if incident["category"] not in ALLOWED_CATEGORIES:
            raise ValueError("unknown enriched incident category")
        if isinstance(incident["severity"], bool) or not isinstance(incident["severity"], int) or not 1 <= incident["severity"] <= 3:
            raise ValueError("invalid enriched severity")
        if isinstance(incident["confidence"], bool) or not isinstance(incident["confidence"], int) or not 0 <= incident["confidence"] <= 100:
            raise ValueError("invalid enriched confidence")
        actors = incident["actors"]
        if not isinstance(actors, list) or len(actors) > 16 or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0 or item >= actor_count for item in actors
        ):
            raise ValueError("invalid enriched actor index")
        clean = {
            "category": incident["category"],
            "severity": incident["severity"],
            "confidence": incident["confidence"],
            "actors": list(actors),
        }
        for field_name in EVIDENCE_DIMENSIONS:
            clean[field_name] = _validate_enrichment_refs(
                incident[field_name], field_name=field_name,
                allowed_sources={source for source in allowed[field_name] if counts[source] > 0},
                counts=counts,
            )
        for field_name, max_length in (
            ("summary", 320),
            ("benign_alternative", MAX_BENIGN_ALTERNATIVE_LENGTH),
            ("why_still_suspicious", MAX_WHY_STILL_SUSPICIOUS_LENGTH),
            ("uncertainty", 220),
        ):
            if not isinstance(incident[field_name], str) or not incident[field_name].strip() or len(incident[field_name]) > max_length:
                raise ValueError(f"invalid enriched incident {field_name}")
            clean[field_name] = incident[field_name].strip()
        validated.append(clean)
    return {"incidents": validated, "state": validate_state(value["state"])}


def load_round_communications(
    db: Any,
    mapping: RoundLogMapping,
    region: CoarseRegion,
) -> tuple[CommunicationRecord, ...]:
    """Read existing round-bound chat rows; no ingestion or schema changes."""
    if db is None:
        return ()
    try:
        from models import PlayerCommunication

        start = mapping.start_log_clock + timedelta(milliseconds=region.start_offset_ms)
        end = mapping.start_log_clock + timedelta(milliseconds=region.end_offset_ms)
        rows = (
            db.query(PlayerCommunication)
            .filter(
                PlayerCommunication.round_event_id == mapping.round_id,
                PlayerCommunication.source == "chat",
                PlayerCommunication.occurred_at >= start,
                PlayerCommunication.occurred_at <= end,
            )
            .order_by(PlayerCommunication.occurred_at.asc(), PlayerCommunication.id.asc())
            .all()
        )
    except Exception as exc:
        logger.warning("log analysis enrichment: communications unavailable: %s", exc)
        return ()
    result: list[CommunicationRecord] = []
    for row in rows:
        text = _normalise(getattr(row, "original_text", None) or getattr(row, "transcript_text", None))
        if not text or not getattr(row, "occurred_at", None):
            continue
        occurred_at = row.occurred_at
        if occurred_at.tzinfo is not None:
            occurred_at = occurred_at.replace(tzinfo=None)
        offset = int((occurred_at - mapping.start_log_clock).total_seconds() * 1000)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"version": "log-analysis-enrichment.v4", "id": int(row.id), "uid": row.event_uid, "offset": offset},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        result.append(CommunicationRecord(
            fingerprint=fingerprint,
            timestamp_offset_ms=offset,
            actor=_normalise(getattr(row, "player_name", None)),
            source="chat",
            text=text,
            raw_id=int(row.id),
            channel=_normalise(getattr(row, "channel", None)),
        ))
    return tuple(result)


def build_enriched_messages(
    *,
    round_id: int,
    region_index: int,
    region: CoarseRegion,
    log_observations: Sequence[Observation],
    communications: Sequence[CommunicationRecord],
    replay_records: Sequence[ReplayContextRecord],
    actors: Mapping[str, int],
    schema: Mapping[str, Any],
    context_text: str = "",
) -> list[dict[str, str]]:
    def log_lines() -> str:
        if not log_observations:
            return "(none)"
        lines = []
        for index, item in enumerate(log_observations):
            details = _redact_known_names(item.details, actors)
            lines.append(
                f"[log:{index},{item.round_offset_ms / 1000:.3f},{actors.get(item.player, -1)},"
                f"{item.action!r},{details!r},count={item.member_count},"
                f"first={item.round_offset_ms / 1000:.3f},"
                f"last={(item.round_offset_ms + item.last_timestamp_ms - item.first_timestamp_ms) / 1000:.3f}]"
            )
        return "\n".join(lines)

    def communication_lines() -> str:
        if not communications:
            return "(none; no existing chat records in this bounded region)"
        return "\n".join(
            f"[communication:{index},{item.timestamp_offset_ms / 1000:.3f},"
            f"{actors.get(item.actor, -1)},{item.source},{_redact_known_names(item.text, actors)!r}]"
            for index, item in enumerate(communications)
        )

    def replay_lines() -> str:
        if not replay_records:
            return "(none; no existing parsed replay consequence source is available)"
        return "\n".join(
            f"[replay:{index},{item.timestamp_offset_ms / 1000:.3f},"
            f"{actors.get(item.actor, -1)},{item.action},{_redact_known_names(item.details, actors)!r}]"
            for index, item in enumerate(replay_records)
        )

    contract = json.dumps(
        {"categories": list(ALLOWED_CATEGORIES), "max_incidents": 8, "region": [region.start_offset_ms, region.end_offset_ms]},
        ensure_ascii=False, separators=(",", ":"),
    )
    actor_indexes = sorted(set(actors.values()))
    user = (
        f"Round {round_id}; coarse region {region_index}; relative range "
        f"{region.start_offset_ms / 1000:.3f}..{region.end_offset_ms / 1000:.3f} seconds.\n"
        f"Opaque actor indexes present: {actor_indexes}.\n"
        f"Persisted round/role context (not evidence): {context_text or '{}'}\n"
        f"Schema limits and candidate policy: {contract}; severity is 1..3.\n"
        "Server log observations (use source=log and the shown index):\n"
        f"{log_lines()}\n"
        "Replay consequences (use source=replay and the shown index):\n"
        f"{replay_lines()}\n"
        "Player communications (use source=communication and the shown index):\n"
        f"{communication_lines()}\n"
        "Create a candidate only when the region visibly supports action, consequence, and intent. "
        "Use only local source/index references. Returning incidents=[] is correct."
    )
    return [
        {"role": "system", "content": ENRICHED_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _expanded_enrichment_ref(
    ref: Mapping[str, Any],
    compact_logs: Sequence[Observation],
    raw_logs: Sequence[Observation],
    communications: Sequence[CommunicationRecord],
    replay_records: Sequence[ReplayContextRecord],
) -> list[dict[str, Any]]:
    source, index = ref["source"], ref["index"]
    if source == "log":
        compact = compact_logs[index]
        raw_by_fp = {item.fingerprint: item for item in raw_logs}
        result = []
        for fingerprint in compact.member_fingerprints:
            item = raw_by_fp.get(fingerprint)
            if item is None:
                raise LLMResponseError("enriched evidence references an unavailable raw log observation")
            result.append({
                "source": "log", "fingerprint": item.fingerprint,
                "timestamp_offset_ms": item.round_offset_ms, "actor": item.player,
                "action": item.action, "details": item.details,
            })
        return result
    if source == "communication":
        item = communications[index]
        return [{
            "source": "communication", "fingerprint": item.fingerprint,
            "timestamp_offset_ms": item.timestamp_offset_ms, "actor": item.actor,
            "action": item.source, "details": item.text, "raw_id": item.raw_id,
        }]
    item = replay_records[index]
    return [{
        "source": "replay", "fingerprint": item.fingerprint,
        "timestamp_offset_ms": item.timestamp_offset_ms, "actor": item.actor,
        "action": item.action, "details": item.details, "raw_id": item.raw_id,
    }]


def expand_enriched_incident_evidence(
    incident: Mapping[str, Any],
    compact_logs: Sequence[Observation],
    raw_logs: Sequence[Observation],
    communications: Sequence[CommunicationRecord],
    replay_records: Sequence[ReplayContextRecord],
) -> dict[str, Any]:
    expanded = dict(incident)
    evidence_records: list[dict[str, Any]] = []
    for field_name in EVIDENCE_DIMENSIONS:
        records = [
            record
            for ref in incident[field_name]
            for record in _expanded_enrichment_ref(ref, compact_logs, raw_logs, communications, replay_records)
        ]
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for record in records:
            key = (str(record["source"]), str(record["fingerprint"]))
            if key not in seen:
                seen.add(key)
                unique.append(record)
        expanded[f"model_{field_name}"] = list(incident[field_name])
        expanded[field_name] = [record["fingerprint"] for record in unique]
        evidence_records.extend(record for record in unique if record not in evidence_records)
    expanded["evidence"] = [record["fingerprint"] for record in evidence_records]
    expanded["evidence_records"] = evidence_records
    return expanded


def run_enrichment_regions(
    *,
    round_id: int,
    mapping: RoundLogMapping,
    regions: Sequence[CoarseRegion],
    compact_observations: Sequence[Observation],
    raw_observations: Sequence[Observation],
    tokenizer: Tokenizer,
    executor: Callable[..., tuple[dict[str, Any], int]],
    db: Any = None,
    settings: Any | None = None,
    replay_records_by_region: Mapping[int, Sequence[ReplayContextRecord]] | None = None,
    region_index_offset: int = 0,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    region_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one bounded enriched request per coarse region."""
    started = time.monotonic()
    replay_records_by_region = replay_records_by_region or {}
    region_reports: list[dict[str, Any]] = []
    merge_inputs: list[dict[str, Any]] = []
    raw_incident_count = 0
    rejected_count = 0
    prompt_tokens_total = 0
    completion_tokens_total = 0
    max_context_tokens = 0
    prompt_eval_total = 0.0
    generation_total = 0.0
    successful_calls = 0
    failed_calls = 0

    pending_regions = list(regions)
    pending_index = 0
    while pending_index < len(pending_regions):
        region_started = time.monotonic()
        region_index = pending_index
        reported_region_index = region_index_offset + pending_index
        region = pending_regions[pending_index]
        stage_started = time.monotonic()
        log_rows = tuple(
            item for item in compact_observations
            if item.round_offset_ms <= region.end_offset_ms
            and item.round_offset_ms + item.last_timestamp_ms - item.first_timestamp_ms >= region.start_offset_ms
        )
        if progress_callback:
            progress_callback({
                "stage": "region_load",
                "region_index": reported_region_index,
                "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                "log_observations": len(log_rows),
            })
        if not log_rows:
            region_reports.append({
                "index": reported_region_index, "start_offset_ms": region.start_offset_ms,
                "end_offset_ms": region.end_offset_ms, "skipped": "no_log_observations",
            })
            pending_index += 1
            continue
        stage_started = time.monotonic()
        communications = load_round_communications(db, mapping, region)
        if progress_callback:
            progress_callback({
                "stage": "communication_query",
                "region_index": reported_region_index,
                "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                "communications": len(communications),
            })
        replay_records = tuple(replay_records_by_region.get(region_index, ()))
        actors = build_actor_index(log_rows)
        context_text = build_context_text(
            round_context=mapping.round_context,
            actor_roles=mapping.actor_roles,
            actors=actors,
        )
        stage_started = time.monotonic()
        schema = build_enrichment_schema(
            log_count=len(log_rows),
            communication_count=len(communications),
            replay_count=len(replay_records),
            actor_count=len(actors),
        )
        if progress_callback:
            progress_callback({
                "stage": "schema_construction",
                "region_index": reported_region_index,
                "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                "elapsed_ms": int((time.monotonic() - region_started) * 1000),
            })
        stage_started = time.monotonic()
        messages = build_enriched_messages(
            round_id=round_id,
            region_index=region_index,
            region=region,
            log_observations=log_rows,
            communications=communications,
            replay_records=replay_records,
            actors=actors,
            schema=schema,
            context_text=context_text,
        )
        if progress_callback:
            progress_callback({
                "stage": "prompt_construction",
                "region_index": reported_region_index,
                "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                "elapsed_ms": int((time.monotonic() - region_started) * 1000),
            })
        stage_started = time.monotonic()
        preflight_tokens = tokenizer.count(build_prompt_text(messages))
        if progress_callback:
            progress_callback({
                "stage": "tokenize",
                "region_index": reported_region_index,
                "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                "prompt_tokens": preflight_tokens,
            })
        if preflight_tokens > MAX_PROMPT_TOKENS:
            if len(log_rows) < 2:
                raise AnalysisError(f"enriched region {region_index} exceeds prompt budget with one observation")
            middle = len(log_rows) // 2
            split_offset = (
                log_rows[middle - 1].round_offset_ms + log_rows[middle].round_offset_ms
            ) // 2
            left = CoarseRegion(
                start_offset_ms=region.start_offset_ms,
                end_offset_ms=max(region.start_offset_ms, split_offset),
                actors=tuple(sorted({item.player for item in log_rows[:middle] if item.player}, key=lambda value: (value.casefold(), value))),
                observation_fingerprints=tuple(item.fingerprint for item in log_rows[:middle]),
                hypothesis_indexes=region.hypothesis_indexes,
            )
            right = CoarseRegion(
                start_offset_ms=min(region.end_offset_ms, split_offset + 1),
                end_offset_ms=region.end_offset_ms,
                actors=tuple(sorted({item.player for item in log_rows[middle:] if item.player}, key=lambda value: (value.casefold(), value))),
                observation_fingerprints=tuple(item.fingerprint for item in log_rows[middle:]),
                hypothesis_indexes=region.hypothesis_indexes,
            )
            pending_regions[pending_index:pending_index + 1] = [left, right]
            continue
        request_key = hashlib.sha256(
            json.dumps(
                {
                    "round": round_id, "region": region_index,
                    "start": region.start_offset_ms, "end": region.end_offset_ms,
                    "prompt": "log-analysis-enrichment.v4", "schema": "log-analysis-enrichment.v4",
                }, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            stage_started = time.monotonic()
            payload, elapsed_ms = executor(
                messages=messages,
                schema=schema,
                model=getattr(settings, "ai_dossier_model", "") if settings else "",
                idempotency_key=f"log-analysis-enrichment:{request_key}",
            )
            if progress_callback:
                progress_callback({
                    "stage": "post_chat_completion",
                    "region_index": reported_region_index,
                    "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                    "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                    "request_elapsed_ms": elapsed_ms,
                })
                progress_callback({
                    "stage": "response_received",
                    "region_index": reported_region_index,
                    "stage_elapsed_ms": 0,
                    "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                })
        except Exception as exc:
            failed_calls += 1
            region_reports.append({
                "index": reported_region_index,
                "start_offset_ms": region.start_offset_ms,
                "end_offset_ms": region.end_offset_ms,
                "log_observations": len(log_rows),
                "communications": len(communications),
                "replay_records": len(replay_records),
                "status": "failed_closed",
                "error": str(exc),
            })
            if region_callback:
                region_callback({
                    "index": reported_region_index,
                    "status": "failed_closed",
                    "error": str(exc),
                    "report": region_reports[-1],
                })
            pending_index += 1
            continue
        decoded = None
        try:
            stage_started = time.monotonic()
            decoded, usage = extract_model_result(payload)
            if progress_callback:
                progress_callback({
                    "stage": "parsing",
                "region_index": reported_region_index,
                    "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                    "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                })
            stage_started = time.monotonic()
            validated = validate_enriched_model_result(
                decoded,
                log_count=len(log_rows),
                communication_count=len(communications),
                replay_count=len(replay_records),
                actor_count=len(actors),
            )
            if progress_callback:
                progress_callback({
                    "stage": "validation",
                "region_index": reported_region_index,
                    "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                    "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                })
        except (LLMResponseError, ValueError) as exc:
            if progress_callback:
                progress_callback({
                    "stage": "parsing" if decoded is None else "validation",
                    "region_index": reported_region_index,
                    "stage_elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                    "elapsed_ms": int((time.monotonic() - region_started) * 1000),
                    "error": str(exc),
                })
            failed_calls += 1
            region_reports.append({
                "index": reported_region_index,
                "start_offset_ms": region.start_offset_ms,
                "end_offset_ms": region.end_offset_ms,
                "log_observations": len(log_rows),
                "communications": len(communications),
                "replay_records": len(replay_records),
                "status": "failed_closed",
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
            })
            if region_callback:
                region_callback({
                    "index": reported_region_index,
                    "status": "failed_closed",
                    "error": str(exc),
                    "response": payload,
                    "report": region_reports[-1],
                })
            pending_index += 1
            continue
        raw_incident_count += len(validated["incidents"])
        accepted, rejected = filter_low_confidence_incidents(validated["incidents"])
        rejected_count += rejected
        expanded_incidents = [
            expand_enriched_incident_evidence(
                incident, log_rows, raw_observations, communications, replay_records,
            )
            for incident in accepted
        ]
        merge_inputs.extend(expanded_incidents)
        response_metrics = extract_response_metrics(payload, usage)
        server_prompt_tokens = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None
        if server_prompt_tokens is not None:
            prompt_tokens_total += server_prompt_tokens
        if completion_tokens is not None:
            completion_tokens_total += completion_tokens
        measured_context = server_prompt_tokens or preflight_tokens
        max_context_tokens = max(max_context_tokens, measured_context)
        if isinstance(response_metrics["prompt_eval_time_ms"], (int, float)):
            prompt_eval_total += response_metrics["prompt_eval_time_ms"]
        if isinstance(response_metrics["generation_time_ms"], (int, float)):
            generation_total += response_metrics["generation_time_ms"]
        successful_calls += 1
        region_report = {
            "index": reported_region_index,
            "start_offset_ms": region.start_offset_ms,
            "end_offset_ms": region.end_offset_ms,
            "actors": list(region.actors),
            "log_observations": len(log_rows),
            "communications": len(communications),
            "replay_records": len(replay_records),
            "preflight_prompt_tokens": preflight_tokens,
            "server_prompt_tokens": server_prompt_tokens,
            "completion_tokens": completion_tokens,
            "context_headroom_tokens": CONTEXT_TOKENS - measured_context,
            "safe_headroom_tokens": CONTEXT_TOKENS - measured_context - MAX_OUTPUT_TOKENS,
            "elapsed_ms": elapsed_ms,
            "raw_model_incident_count": len(validated["incidents"]),
            "rejected_low_confidence": rejected,
            **response_metrics,
            "incidents": expanded_incidents,
        }
        region_reports.append(region_report)
        if region_callback:
            region_callback({
                "index": reported_region_index,
                "status": "succeeded",
                "response": payload,
                "validated": validated,
                "report": region_report,
            })
        pending_index += 1

    merged, merge_count = merge_incidents(merge_inputs)
    actor_aliases = build_actor_index(raw_observations)
    report = {
        "round_id": int(round_id),
        "regions": region_reports,
        "incidents": _scrub_report_names(merged, actor_aliases),
        "metrics": {
            "coarse_regions": len(regions),
            "enriched_calls": successful_calls,
            "failed_closed_calls": failed_calls,
            "raw_model_incidents": raw_incident_count,
            "rejected_low_confidence": rejected_count,
            "final_candidate_count": len(merged),
            "merge_duplicate_incidents": merge_count,
            "prompt_tokens": prompt_tokens_total,
            "completion_tokens": completion_tokens_total,
            "prompt_eval_time_ms": prompt_eval_total,
            "generation_time_ms": generation_total,
            "max_context_tokens": max_context_tokens,
            "total_elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    }
    return report


def filter_low_confidence_incidents(
    incidents: Sequence[dict[str, Any]],
    *,
    threshold: int = MIN_INCIDENT_CONFIDENCE,
) -> tuple[list[dict[str, Any]], int]:
    """Apply the abstention policy after schema validation, for diagnostics."""
    accepted = [incident for incident in incidents if incident["confidence"] >= threshold]
    return accepted, len(incidents) - len(accepted)


def merge_incidents(incidents: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: list[dict[str, Any]] = []
    duplicate_count = 0
    for incident in incidents:
        current = dict(incident)
        current["evidence"] = list(dict.fromkeys(current.get("evidence", [])))
        for field_name in EVIDENCE_DIMENSIONS:
            current[field_name] = list(dict.fromkeys(current.get(field_name, [])))
        current["evidence_snapshots"] = list(current.get("evidence_snapshots", []))
        current["actors"] = list(current.get("actors", []))
        for field_name in EVIDENCE_DIMENSIONS:
            compact_field = f"compact_{field_name}"
            current[compact_field] = list(dict.fromkeys(current.get(compact_field, [])))
        current.setdefault("merged_windows", [current.get("window_index", 0)])
        match = None
        for existing in merged:
            if (
                existing.get("category") == current.get("category")
                and set(existing.get("evidence", [])) & set(current["evidence"])
                and set(existing.get("actors", [])) == set(current["actors"])
            ):
                match = existing
                break
        if match is None:
            merged.append(current)
            continue
        duplicate_count += 1
        match["evidence"] = list(dict.fromkeys(match["evidence"] + current["evidence"]))
        for field_name in EVIDENCE_DIMENSIONS:
            match[field_name] = list(dict.fromkeys(match[field_name] + current[field_name]))
            compact_field = f"compact_{field_name}"
            match[compact_field] = list(dict.fromkeys(match[compact_field] + current[compact_field]))
        snapshots_by_fingerprint = {
            snapshot.get("fingerprint"): snapshot
            for snapshot in match.get("evidence_snapshots", [])
            if isinstance(snapshot, dict) and snapshot.get("fingerprint")
        }
        for snapshot in current.get("evidence_snapshots", []):
            if isinstance(snapshot, dict) and snapshot.get("fingerprint"):
                snapshots_by_fingerprint.setdefault(snapshot["fingerprint"], snapshot)
        match["evidence_snapshots"] = [
            snapshots_by_fingerprint[fingerprint]
            for fingerprint in match["evidence"]
            if fingerprint in snapshots_by_fingerprint
        ]
        match["severity"] = max(match["severity"], current["severity"])
        match["confidence"] = max(match["confidence"], current["confidence"])
        match["merged_windows"] = list(dict.fromkeys(match.get("merged_windows", []) + current.get("merged_windows", [])))
    return merged, duplicate_count


def derive_tokenize_url(completions_url: str) -> str:
    parsed = urlsplit(completions_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("LLM URL must be an absolute URL")
    path = parsed.path.rstrip("/")
    if path.endswith("/tokenize"):
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    elif path.endswith("/v1"):
        path = path[:-3]
    elif path.endswith("/v1/chat/completions"):
        path = path[: -len("/v1/chat/completions")]
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    else:
        path = path.rsplit("/", 1)[0] if "/" in path else ""
    return urlunsplit((parsed.scheme, parsed.netloc, (path or "") + "/tokenize", "", ""))


def extract_command_token_count(output: str) -> int:
    try:
        decoded = json.loads(output.strip())
        if isinstance(decoded, dict) and isinstance(decoded.get("count"), int) and decoded["count"] >= 0:
            return decoded["count"]
    except json.JSONDecodeError:
        pass
    match = re.search(r"token\s*count\s*[:=]\s*(\d+)", output, re.IGNORECASE)
    if match:
        return int(match.group(1))
    numeric_lines = [line.strip() for line in output.splitlines() if line.strip().isdigit()]
    if numeric_lines:
        return int(numeric_lines[-1])
    raise ValueError("token count is absent from tokenizer command output")


class LlamaServerTokenizer:
    mechanism = "llama-server:/tokenize"

    def __init__(self, *, completions_url: str, api_key: str, model: str, verify: str | bool = True, timeout: float = 30.0):
        if not api_key or not model:
            raise TokenizerUnavailable("Qwen tokenizer requires API key and configured model")
        self.url = derive_tokenize_url(completions_url)
        self.models_url = self.url.rsplit("/tokenize", 1)[0] + "/v1/models"
        self.api_key = api_key
        self.model = model
        self.client = httpx.Client(verify=verify or True, timeout=timeout)

    def verify_runtime(self) -> None:
        response = self.client.get(self.models_url, headers={"Authorization": f"Bearer {self.api_key}"})
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models") or payload.get("data")
        if not isinstance(models, list):
            raise TokenizerUnavailable("Qwen /v1/models response has no model list")
        available = set()
        for item in models:
            if isinstance(item, dict):
                available.add(str(item.get("id") or item.get("name") or ""))
                available.update(str(alias) for alias in (item.get("aliases") or []) if alias)
        if self.model not in available:
            raise TokenizerUnavailable(f"configured model {self.model!r} is not loaded by tokenizer endpoint")

    def count(self, text: str) -> int:
        response = self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"content": text},
        )
        response.raise_for_status()
        payload = response.json()
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, list) or any(not isinstance(item, int) for item in tokens):
            raise TokenizerUnavailable("Qwen /tokenize response has no integer tokens array")
        return len(tokens)

    def close(self) -> None:
        self.client.close()


class CommandTokenizer:
    def __init__(self, command: Sequence[str], *, timeout: float = 30.0):
        if not command:
            raise TokenizerUnavailable("tokenizer command is empty")
        self.command = tuple(command)
        self.timeout = timeout
        self.mechanism = "pinned-tokenizer-command:" + " ".join(command)

    def count(self, text: str) -> int:
        try:
            completed = subprocess.run(
                list(self.command),
                input=text,
                text=True,
                capture_output=True,
                check=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TokenizerUnavailable(f"tokenizer command failed: {exc}") from exc
        return extract_command_token_count(completed.stdout + "\n" + completed.stderr)

    def close(self) -> None:
        return None


class CachingTokenizer:
    def __init__(self, delegate: Tokenizer):
        self.delegate = delegate
        self.mechanism = delegate.mechanism
        self._cache: dict[str, int] = {}

    def count(self, text: str) -> int:
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key not in self._cache:
            self._cache[key] = self.delegate.count(text)
        return self._cache[key]

    def close(self) -> None:
        close = getattr(self.delegate, "close", None)
        if close:
            close()


def build_exact_tokenizer(settings: Any, *, tokenizer_command: str | None = None, tokenizer_url: str | None = None) -> CachingTokenizer:
    command_error: Exception | None = None
    if tokenizer_url or getattr(settings, "ai_dossier_url", ""):
        try:
            tokenizer = LlamaServerTokenizer(
                completions_url=tokenizer_url or settings.ai_dossier_url,
                api_key=settings.ai_dossier_api_key,
                model=settings.ai_dossier_model,
                verify=getattr(settings, "ai_dossier_tls_ca_file", "") or True,
                timeout=min(float(getattr(settings, "ai_dossier_timeout_seconds", 30.0)), 60.0),
            )
            tokenizer.verify_runtime()
            return CachingTokenizer(tokenizer)
        except (httpx.HTTPError, ValueError, TokenizerUnavailable) as exc:
            command_error = exc
    if tokenizer_command:
        return CachingTokenizer(CommandTokenizer(shlex.split(tokenizer_command)))
    detail = f" ({command_error})" if command_error else ""
    raise TokenizerUnavailable(f"exact Qwen tokenizer unavailable; provide --tokenizer-command{detail}")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _configured_timezone(server: str, settings: Any) -> str:
    default = (getattr(settings, "logs_default_timezone", "") or "").strip()
    for index in (1, 2):
        configured = (getattr(settings, f"sftp_server_{index}_name", "") or "").strip()
        override = (getattr(settings, f"sftp_server_{index}_logs_timezone", "") or "").strip()
        if configured and configured == server and override:
            return override
    if not default:
        raise RoundLogMappingError("log timezone is not configured")
    return default


def _storage_ms(value: datetime) -> int:
    # Match the existing log index's _ts_to_ms convention.  Both mapping and
    # query run in the same NexusData process, so the historical naive-clock
    # conversion remains explicit in the report instead of being guessed.
    from services_logs import _ts_to_ms

    return _ts_to_ms(value)


def _round_context_from_event(end_event: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Extract only already-persisted, compact context; never copy identities."""
    context: dict[str, Any] = {}
    for field_name in ("mission", "submarine_name", "location"):
        value = _normalise(getattr(end_event, field_name, None))
        if value:
            context[field_name] = value[:160]
    for field_name in ("mission_completed", "reached_station"):
        value = getattr(end_event, field_name, None)
        if value is not None:
            context[field_name] = bool(value)

    actor_roles: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    for player in getattr(end_event, "players", None) or []:
        if not isinstance(player, dict):
            continue
        player_name = _normalise(player.get("player_name"))
        role = _normalise(player.get("role"))
        if player_name and role and len(role) <= 80:
            if player_name in actor_roles:
                ambiguous_names.add(player_name)
                actor_roles.pop(player_name, None)
                continue
            if player_name in ambiguous_names:
                continue
            actor_roles[player_name] = role
    return context, actor_roles


def resolve_round_log_mapping(db: Any, round_id: int, *, settings: Any | None = None, padding_ms: int = 1_000) -> RoundLogMapping:
    from models import RoundEvent
    from config import get_settings

    settings = settings or get_settings()
    end_event = db.query(RoundEvent).filter(RoundEvent.id == int(round_id)).first()
    if end_event is None:
        raise RoundLogMappingError(f"round event {round_id} does not exist")
    if end_event.event_type != "end":
        raise RoundLogMappingError(f"round event {round_id} is not a completed round end")
    start_event = end_event.paired_start
    if start_event is None or start_event.event_type != "start":
        raise RoundLogMappingError("completed round has no unambiguous paired start event")
    if not end_event.server_name or end_event.server_name != start_event.server_name:
        raise RoundLogMappingError("round start/end server identity is missing or inconsistent")
    if not start_event.created_at or not end_event.created_at:
        raise RoundLogMappingError("round boundaries have no timestamps")
    for field_name in ("server_session_id", "round_token"):
        start_value = getattr(start_event, field_name, None)
        end_value = getattr(end_event, field_name, None)
        if start_value and end_value and start_value != end_value:
            raise RoundLogMappingError(f"round {field_name} conflicts between start and end")

    start_utc = _as_utc(start_event.created_at)
    end_utc = _as_utc(end_event.created_at)
    if end_utc <= start_utc:
        raise RoundLogMappingError("round end is not after round start")
    duration = (end_utc - start_utc).total_seconds()
    if duration > 12 * 60 * 60:
        raise RoundLogMappingError("round duration is implausibly long")

    timezone_name = _configured_timezone(end_event.server_name, settings)
    try:
        log_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RoundLogMappingError(f"configured log timezone is invalid: {timezone_name}") from exc
    start_log = start_utc.astimezone(log_timezone).replace(tzinfo=None)
    end_log = end_utc.astimezone(log_timezone).replace(tzinfo=None)
    if end_log <= start_log:
        raise RoundLogMappingError("timezone conversion produced invalid log boundaries")

    round_context, actor_roles = _round_context_from_event(end_event)

    return RoundLogMapping(
        round_id=int(round_id),
        server=end_event.server_name,
        start_utc=start_utc,
        end_utc=end_utc,
        timezone_name=timezone_name,
        start_log_clock=start_log,
        end_log_clock=end_log,
        storage_start_ms=_storage_ms(start_log) - padding_ms,
        storage_end_ms=_storage_ms(end_log) + padding_ms,
        padding_ms=padding_ms,
        method=f"paired_round_events+configured_timezone:{timezone_name}+services_logs._ts_to_ms",
        round_context=round_context,
        actor_roles=actor_roles,
    )


def load_round_observations(
    db: Any,
    round_id: int,
    *,
    settings: Any | None = None,
    log_db_path: str | None = None,
) -> tuple[RoundLogMapping, tuple[RawObservation, ...], dict[str, tuple[int, int]]]:
    from services_logs import LOG_DB_PATH

    mapping = resolve_round_log_mapping(db, round_id, settings=settings)
    path = str(log_db_path or LOG_DB_PATH)
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3, check_same_thread=False)
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT ts, id, player, action, details, server, source_file "
            "FROM log_events WHERE server=? AND ts BETWEEN ? AND ? ORDER BY ts, id",
            (mapping.server, mapping.storage_start_ms, mapping.storage_end_ms),
        ).fetchall()
        if not rows:
            raise RoundLogMappingError(
                f"no indexed log events in mapped range {mapping.storage_start_ms}..{mapping.storage_end_ms}"
            )
        source_files = sorted({str(row[6] or "") for row in rows})
        placeholders = ",".join("?" for _ in source_files)
        source_ranges = {
            str(source): (int(min_ts), int(max_ts))
            for source, min_ts, max_ts in connection.execute(
                f"SELECT source_file, MIN(ts), MAX(ts) FROM log_events "
                f"WHERE server=? AND source_file IN ({placeholders}) GROUP BY source_file",
                (mapping.server, *source_files),
            ).fetchall()
        }
    except sqlite3.Error as exc:
        raise AnalysisError(f"log index read failed: {exc}") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass

    raw = tuple(
        RawObservation(
            timestamp_ms=int(row[0]),
            server=str(row[5] or mapping.server),
            player=str(row[2] or ""),
            action=str(row[3] or ""),
            details=str(row[4] or ""),
            source_file=str(row[6] or ""),
            source_row_id=int(row[1]),
        )
        for row in rows
    )
    return mapping, raw, source_ranges


SYSTEM_PROMPT = (
    "You analyze incomplete Barotrauma server telemetry for a developer dry-run. "
    "Normal or uncertain gameplay is the default, and you are allowed to return incidents=[] in every window. "
    "An anomaly is not proof of a rule violation and does not establish motive. Legitimate gameplay, roleplay, "
    "accidents, combat, medical work, repair, reactor or wiring work, item handling, doors, chat, and name changes "
    "are ordinary explanations unless a broader sequence and harmful consequence make them genuinely suspicious. "
    "A single unusual action is never enough. Missing context lowers confidence rather than raising it. "
    "Create an incident only when the window contains all of the following: a supported sequence of actions, "
    "an observed harmful or dangerous consequence, and separate observations that make deliberate intent more "
    "plausible than error or ordinary gameplay. If any of these three evidence dimensions is not visible, "
    "return incidents=[]. Confidence is an independent estimate of how strongly the complete evidence supports "
    "the candidate; do not use confidence to compensate for missing evidence. "
    "For every candidate, state the most plausible benign_alternative and why_still_suspicious; "
    "if you cannot distinguish the evidence from the benign explanation, return no incident. "
    "State is short working memory only and may be updated without an incident; state is never evidence. "
    "Actions are parser classifications of log lines, not proof that an action succeeded or was intentional. "
    "In particular, repair means a repair attempt/start as parsed, structure covers both breach and repair, "
    "attack covers combat/damage-related lines, and other is unclassified technical or textual telemetry. "
    "Do not turn action names into rules. Actors are local opaque indexes; an empty actor list is allowed when no "
    "actor is established. Keep output compact: at most two strongest candidates per window, and only the smallest "
    "evidence index set needed to support each candidate. Return only the requested JSON object and do not provide hidden reasoning."
)


ENRICHED_SYSTEM_PROMPT = (
    SYSTEM_PROMPT + " "
    "This is a bounded verification pass after a coarse log hypothesis. "
    "The source sections are separate: log references use source=log, communications use source=communication, "
    "and replay references use source=replay. Action evidence must use log observations. Consequence evidence "
    "must use log or replay observations. Intent evidence may use log, communication, or replay observations. "
    "Each candidate still needs all three dimensions. If the bounded region does not show all three, return no incident."
)


def build_context_text(
    *,
    round_context: Mapping[str, Any] | None,
    actor_roles: Mapping[str, str] | None,
    actors: Mapping[str, int],
) -> str:
    """Serialize cheap persisted metadata without copying player identities."""
    payload: dict[str, Any] = {}
    if round_context:
        payload["round"] = dict(round_context)
    role_payload = {
        str(actor_index): {"role": actor_roles[player]}
        for player, actor_index in actors.items()
        if actor_roles and player in actor_roles
    }
    if role_payload:
        payload["actors"] = dict(sorted(role_payload.items(), key=lambda item: int(item[0])))
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_user_prompt(
    *,
    round_id: int,
    window: AnalysisWindow,
    state: Mapping[str, Sequence[str]],
    schema: Mapping[str, Any],
    context_text: str = "",
) -> str:
    state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    schema_contract = json.dumps(
        {"categories": list(ALLOWED_CATEGORIES), "max_incidents": 8, "event_index_max": len(window.observations) - 1, "actor_index_max": max(len(window.actors) - 1, 0)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"Round {round_id}; window {window.index}; relative range "
        f"{window.observations[0].round_offset_ms / 1000:.3f}.."
        f"{window.observations[-1].round_offset_ms / 1000:.3f} seconds.\n"
        f"Opaque actor indexes present: {sorted(set(window.actors.values()))}.\n"
        f"Persisted round/role context (not evidence): {context_text or '{}'}\n"
        f"Previous state: {state_json}\n"
        f"Schema limits and candidate policy: {schema_contract}; severity is 1..3.\n"
        "Events are [local_index,relative_seconds,actor_index,action,details]:\n"
        f"{window.event_text}\n"
        "Use only local event/actor indexes from this window."
    )


def state_token_count(state: Mapping[str, Sequence[str]], tokenizer: Tokenizer) -> int:
    return tokenizer.count(json.dumps(state, ensure_ascii=False, separators=(",", ":")))


def _extract_response_content(payload: Mapping[str, Any]) -> Any:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("LLM response has no choices/message/content") from exc
    if not isinstance(content, str):
        raise LLMResponseError("LLM response content is not text")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMResponseError("LLM response content is not JSON") from exc


def extract_model_result(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    decoded = _extract_response_content(payload)
    usage = payload.get("usage") if isinstance(payload, dict) else {}
    if not isinstance(decoded, dict):
        raise LLMResponseError("LLM response JSON is not an object")
    return decoded, usage if isinstance(usage, dict) else {}


def _numeric_response_metric(
    payload: Mapping[str, Any],
    usage: Mapping[str, Any],
    *keys: str,
) -> int | float | None:
    """Read one numeric metric across the gateway/llama.cpp response shapes."""
    containers: list[Mapping[str, Any]] = []
    for container in (
        usage.get("timings"),
        payload.get("timings"),
        usage,
        payload,
    ):
        if isinstance(container, Mapping):
            containers.append(container)
    for container in containers:
        for key in keys:
            value = container.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
    return None


def extract_response_metrics(
    payload: Mapping[str, Any],
    usage: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize usage and completion state for the dry-run report."""
    prompt_eval_time_ms = _numeric_response_metric(
        payload,
        usage,
        "prompt_eval_time_ms",
        "prompt_eval_ms",
        "prompt_eval_time",
        "prompt_ms",
        "prompt_eval",
    )
    generation_time_ms = _numeric_response_metric(
        payload,
        usage,
        "generation_time_ms",
        "generation_ms",
        "generation_time",
        "predicted_ms",
    )
    finish_reason: str | None = None
    try:
        raw_finish_reason = payload["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raw_finish_reason = None
    if isinstance(raw_finish_reason, str):
        finish_reason = raw_finish_reason
    return {
        "prompt_eval_time_ms": prompt_eval_time_ms,
        "generation_time_ms": generation_time_ms,
        "finish_reason": finish_reason,
        "truncated": finish_reason in {"length", "max_tokens"},
    }


def _request_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "log_analysis_dry_run",
            "strict": True,
            "schema": schema,
        },
    }


def build_llm_messages(
    round_id: int,
    window: AnalysisWindow,
    state: Mapping[str, Sequence[str]],
    schema: dict[str, Any],
    *,
    context_text: str = "",
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(round_id=round_id, window=window, state=state, schema=schema, context_text=context_text)},
    ]


def build_prompt_text(messages: Sequence[Mapping[str, str]]) -> str:
    return "\n\n".join(str(message.get("content", "")) for message in messages)


def build_window(
    *,
    index: int,
    start_index: int,
    observations: Sequence[Observation],
    state: Mapping[str, Sequence[str]],
    tokenizer: Tokenizer,
    context_text: str = "",
) -> AnalysisWindow:
    selected = tuple(observations[start_index:])
    if not selected:
        raise ValueError("window cannot be empty")
    actors = build_actor_index(selected)
    event_text = build_event_text(selected, actors)
    event_tokens = tokenizer.count(event_text)
    context_tokens = tokenizer.count(context_text) if context_text else 0
    placeholder = AnalysisWindow(index, start_index, len(observations), selected, actors, event_text, event_tokens, 0, 0, state_token_count(state, tokenizer), context_tokens)
    schema = build_analysis_schema(len(selected), len(actors))
    messages = build_llm_messages(0, placeholder, state, schema, context_text=context_text)
    prompt_tokens = tokenizer.count(build_prompt_text(messages))
    return replace(placeholder, preflight_prompt_tokens=prompt_tokens)


def _measure_window_candidate(
    *,
    round_id: int,
    start: int,
    end: int,
    index: int,
    observations: Sequence[Observation],
    state: Mapping[str, Sequence[str]],
    tokenizer: Tokenizer,
    state_tokens: int,
    context_text: str = "",
) -> AnalysisWindow:
    selected = tuple(observations[start:end])
    actors = build_actor_index(selected)
    event_text = build_event_text(selected, actors)
    event_tokens = tokenizer.count(event_text)
    context_tokens = tokenizer.count(context_text) if context_text else 0
    provisional = AnalysisWindow(
        index,
        start,
        end,
        selected,
        actors,
        event_text,
        event_tokens,
        0,
        0,
        state_tokens,
        context_tokens,
    )
    schema = build_analysis_schema(len(selected), len(actors))
    prompt_tokens = tokenizer.count(
        build_prompt_text(build_llm_messages(round_id, provisional, state, schema, context_text=context_text))
    )
    return replace(provisional, preflight_prompt_tokens=prompt_tokens)


def _expanded_observation_members(
    observation: Observation,
    raw_by_fingerprint: Mapping[str, Observation],
) -> tuple[Observation, ...]:
    members = tuple(raw_by_fingerprint.get(fingerprint) for fingerprint in observation.member_fingerprints)
    if any(member is None for member in members):
        raise LLMResponseError("compact evidence references an unavailable raw observation")
    if len(members) != observation.member_count:
        raise LLMResponseError("compact evidence member count is inconsistent")
    return members  # type: ignore[return-value]


def make_incident_evidence(
    result: dict[str, Any],
    window: AnalysisWindow,
    *,
    raw_observations: Sequence[Observation] | None = None,
) -> dict[str, Any]:
    raw_by_fingerprint = {
        observation.fingerprint: observation
        for observation in (raw_observations or window.observations)
    }
    incidents = []
    for incident in result["incidents"]:
        observations_by_dimension = {
            field_name: [
                member
                for index in incident[field_name]
                for member in _expanded_observation_members(window.observations[index], raw_by_fingerprint)
            ]
            for field_name in EVIDENCE_DIMENSIONS
        }
        evidence = []
        for field_name in EVIDENCE_DIMENSIONS:
            for observation in observations_by_dimension[field_name]:
                if observation.fingerprint not in {item.fingerprint for item in evidence}:
                    evidence.append(observation)
        snapshots = [
            {
                "timestamp_offset_ms": observation.round_offset_ms,
                "actor": window.actors.get(observation.player),
                "action": observation.action,
                "details": _redact_known_names(observation.details, window.actors),
                "fingerprint": observation.fingerprint,
                "multiplicity": observation.multiplicity,
                "dedup_reason": observation.dedup_reason,
            }
            for observation in evidence
        ]
        incidents.append({
            **incident,
            "summary": _redact_known_names(incident["summary"], window.actors),
            "benign_alternative": _redact_known_names(incident["benign_alternative"], window.actors),
            "why_still_suspicious": _redact_known_names(incident["why_still_suspicious"], window.actors),
            "uncertainty": _redact_known_names(incident["uncertainty"], window.actors),
            "evidence": [observation.fingerprint for observation in evidence],
            **{
                field_name: [observation.fingerprint for observation in observations_by_dimension[field_name]]
                for field_name in EVIDENCE_DIMENSIONS
            },
            **{
                f"compact_{field_name}": list(incident[field_name])
                for field_name in EVIDENCE_DIMENSIONS
            },
            "evidence_snapshots": snapshots,
            "window_index": window.index,
        })
    return {"incidents": incidents, "state": result["state"]}


def build_windows(
    *,
    round_id: int,
    observations: Sequence[Observation],
    initial_state: Mapping[str, Sequence[str]],
    tokenizer: Tokenizer,
    overlap_ms: int = OVERLAP_MS,
    max_windows: int | None = None,
    context_text: str = "",
    context_builder: Callable[[Mapping[str, int]], str] | None = None,
) -> tuple[AnalysisWindow, ...]:
    """Build one bounded window at a time; execution may update state later."""
    state = validate_state(dict(initial_state))
    windows: list[AnalysisWindow] = []
    start = 0
    index = 0
    while start < len(observations):
        state_tokens = state_token_count(state, tokenizer)
        cache: dict[int, AnalysisWindow] = {}

        def measure(end: int) -> AnalysisWindow:
            if end not in cache:
                selected_actors = build_actor_index(observations[start:end])
                selected_context = context_builder(selected_actors) if context_builder else context_text
                cache[end] = _measure_window_candidate(
                    round_id=round_id,
                    start=start,
                    end=end,
                    index=index,
                    observations=observations,
                    state=state,
                    tokenizer=tokenizer,
                    state_tokens=state_tokens,
                    context_text=selected_context,
                )
            return cache[end]

        def fits(window: AnalysisWindow) -> bool:
            return window.event_tokens <= EVENT_TOKEN_TARGET and window.preflight_prompt_tokens <= MAX_PROMPT_TOKENS

        first = measure(start + 1)
        if not fits(first):
            raise AnalysisError(f"single observation at index {start} exceeds token budget")
        best = first
        probe = start + 2
        step = 1
        while probe <= len(observations):
            candidate = measure(probe)
            if not fits(candidate):
                break
            best = candidate
            step *= 2
            probe = start + 1 + step

        if probe <= len(observations) and not fits(measure(probe)):
            low, high = best.end_index + 1, probe - 1
            while low <= high:
                middle = (low + high) // 2
                candidate = measure(middle)
                if fits(candidate):
                    best = candidate
                    low = middle + 1
                else:
                    high = middle - 1
        if best is None:
            raise AnalysisError(f"single observation at index {start} exceeds token budget")
        windows.append(best)
        if max_windows is not None and len(windows) >= max_windows:
            break
        if best.end_index >= len(observations):
            break
        cutoff = observations[best.end_index - 1].timestamp_ms - overlap_ms
        next_start = best.end_index
        for candidate_index in range(start, best.end_index):
            if observations[candidate_index].timestamp_ms >= cutoff:
                next_start = candidate_index
                break
        start = max(start + 1, next_start)
        index += 1
    return tuple(windows)


class QueueExecutor:
    """Submit one request to the existing durable LLM queue and wait for it."""

    def __init__(self, *, poll_interval: float = 0.25, timeout_seconds: float = 240.0):
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds

    def __call__(self, *, messages: list[dict[str, str]], schema: dict[str, Any], model: str, idempotency_key: str) -> tuple[dict[str, Any], int]:
        from services_llm_gateway import poll, submit

        ticket = submit(
            "log_analysis_dry_run",
            messages,
            {
                "model": model,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "temperature": 0,
                "response_format": _request_schema(schema),
            },
            idempotency_key=idempotency_key,
            timeout=self.timeout_seconds,
        )
        started = time.monotonic()
        deadline = started + self.timeout_seconds + 30
        while time.monotonic() < deadline:
            current = poll(ticket)
            if current["status"] == "succeeded":
                if not isinstance(current.get("result"), dict):
                    raise LLMResponseError("LLM queue result is not an object")
                return current["result"], int((time.monotonic() - started) * 1000)
            if current["status"] in {"failed", "cancelled"}:
                raise LLMResponseError(current.get("error") or f"LLM ticket {ticket} ended as {current['status']}")
            time.sleep(self.poll_interval)
        raise LLMResponseError(f"LLM ticket {ticket} timed out")


def _empty_state() -> dict[str, list[str]]:
    return {"facts": [], "open_sequences": [], "open_questions": []}


def _observation_snapshot(observation: Observation, actors: Mapping[str, int]) -> dict[str, Any]:
    return {
        "timestamp_offset_ms": observation.round_offset_ms,
        "last_timestamp_offset_ms": observation.round_offset_ms + observation.last_timestamp_ms - observation.first_timestamp_ms,
        "actor": actors.get(observation.player),
        "action": observation.action,
        "details": _redact_known_names(observation.details, actors),
        "fingerprint": observation.fingerprint,
        "member_count": observation.member_count,
        "member_fingerprints": list(observation.member_fingerprints),
        "multiplicity": observation.multiplicity,
        "dedup_reason": observation.dedup_reason,
    }


def run_dry_run(
    *,
    db: Any,
    round_id: int,
    tokenizer: Tokenizer,
    executor: Callable[..., tuple[dict[str, Any], int]],
    settings: Any | None = None,
    log_db_path: str | None = None,
    region_start_offset_ms: int | None = None,
    region_end_offset_ms: int | None = None,
) -> dict[str, Any]:
    if region_start_offset_ms is not None and region_start_offset_ms < 0:
        raise ValueError("region_start_offset_ms must be non-negative")
    if region_end_offset_ms is not None and region_end_offset_ms < 0:
        raise ValueError("region_end_offset_ms must be non-negative")
    if (
        region_start_offset_ms is not None
        and region_end_offset_ms is not None
        and region_end_offset_ms < region_start_offset_ms
    ):
        raise ValueError("region_end_offset_ms must not precede region_start_offset_ms")
    started = time.monotonic()
    mapping, raw_rows, source_ranges = load_round_observations(
        db, round_id, settings=settings, log_db_path=log_db_path,
    )
    round_start_ms = mapping.storage_start_ms + mapping.padding_ms
    if region_start_offset_ms is not None or region_end_offset_ms is not None:
        raw_rows = tuple(
            row for row in raw_rows
            if (region_start_offset_ms is None or row.timestamp_ms - round_start_ms >= region_start_offset_ms)
            and (region_end_offset_ms is None or row.timestamp_ms - round_start_ms <= region_end_offset_ms)
        )
        if not raw_rows:
            raise AnalysisError("requested analysis region contains no indexed observations")
    dedup = deduplicate_observations(
        raw_rows,
        source_ranges=source_ranges,
        round_start_ms=round_start_ms,
    )
    raw_observations = dedup.observations
    compaction = compact_bursts(raw_observations)
    observations = compaction.observations
    if not observations:
        raise AnalysisError("mapped round contains no observations after preprocessing")
    state = _empty_state()
    window_reports: list[dict[str, Any]] = []
    merge_inputs: list[dict[str, Any]] = []
    state_token_counts: list[int] = []
    cursor = 0
    window_index = 0
    previous_window_end = 0
    total_llm_calls = 0
    context_builder = lambda actors: build_context_text(
        round_context=mapping.round_context,
        actor_roles=mapping.actor_roles,
        actors=actors,
    )
    raw_model_incidents = 0
    rejected_low_confidence = 0
    raw_confidence_values: list[int] = []
    raw_severity_values: list[int] = []
    while cursor < len(observations):
        suffix_windows = build_windows(
            round_id=round_id,
            observations=observations[cursor:],
            initial_state=state,
            tokenizer=tokenizer,
            overlap_ms=OVERLAP_MS,
            max_windows=1,
            context_builder=context_builder,
        )
        window = suffix_windows[0]
        window = replace(
            window,
            index=window_index,
            start_index=cursor + window.start_index,
            end_index=cursor + window.end_index,
            overlap_observations=max(0, previous_window_end - cursor),
        )
        # If the overlap consumes the whole candidate, do not spend another
        # model call re-reading the same tail.  Drop the overlap and rebuild
        # from the previous end so progress is measured in new observations.
        if previous_window_end > cursor and window.end_index <= previous_window_end:
            cursor = previous_window_end
            continue
        schema = build_analysis_schema(len(window.observations), len(window.actors))
        context_text = context_builder(window.actors)
        messages = build_llm_messages(round_id, window, state, schema, context_text=context_text)
        request_key = hashlib.sha256(
            json.dumps(
                {"round": round_id, "window": window_index, "start": window.start_index, "end": window.end_index, "prompt": PROMPT_VERSION, "schema": SCHEMA_VERSION, "preprocess": PREPROCESS_VERSION, "max_output_tokens": MAX_OUTPUT_TOKENS},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        response_payload, elapsed_ms = executor(
            messages=messages,
            schema=schema,
            model=getattr(settings, "ai_dossier_model", "") if settings else "",
            idempotency_key=f"log-analysis:{request_key}",
        )
        decoded, usage = extract_model_result(response_payload)
        response_metrics = extract_response_metrics(response_payload, usage)
        validated = validate_model_result(decoded, event_count=len(window.observations), actor_count=len(window.actors))
        raw_model_incidents += len(validated["incidents"])
        raw_confidence_values.extend(int(incident["confidence"]) for incident in validated["incidents"])
        raw_severity_values.extend(int(incident["severity"]) for incident in validated["incidents"])
        raw_evidence_result = make_incident_evidence(
            validated,
            window,
            raw_observations=raw_observations,
        )
        accepted_incidents, rejected = filter_low_confidence_incidents(validated["incidents"])
        rejected_low_confidence += rejected
        validated = {"incidents": accepted_incidents, "state": validated["state"]}
        next_state = validated["state"]
        next_state_tokens = state_token_count(next_state, tokenizer)
        if next_state_tokens > MAX_STATE_TOKENS:
            raise LLMResponseError(f"rolling state uses {next_state_tokens} tokens, limit is {MAX_STATE_TOKENS}")
        server_prompt_tokens = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None
        state_token_counts.append(next_state_tokens)
        evidence_result = make_incident_evidence(
            validated,
            window,
            raw_observations=raw_observations,
        )
        for incident in evidence_result["incidents"]:
            merge_inputs.append(incident)
        window_reports.append({
            "index": window_index,
            "start_offset_ms": window.observations[0].round_offset_ms,
            "end_offset_ms": window.observations[-1].round_offset_ms,
            "observation_count": len(window.observations),
            "event_input_tokens": window.event_tokens,
            "preflight_prompt_tokens": window.preflight_prompt_tokens,
            "server_prompt_tokens": server_prompt_tokens,
            "completion_tokens": completion_tokens,
            **response_metrics,
            "context_headroom_tokens": CONTEXT_TOKENS - (server_prompt_tokens or window.preflight_prompt_tokens),
            "safe_headroom_tokens": CONTEXT_TOKENS - (server_prompt_tokens or window.preflight_prompt_tokens) - MAX_OUTPUT_TOKENS,
            "overlap_observations": window.overlap_observations,
            "state_tokens_before": window.state_tokens_before,
            "state_tokens_after": next_state_tokens,
            "context_enrichment_tokens": window.context_tokens,
            "raw_model_incident_count": len(decoded.get("incidents", [])) if isinstance(decoded.get("incidents"), list) else 0,
            "raw_hypotheses": raw_evidence_result["incidents"],
            "rejected_low_confidence": rejected,
            "elapsed_ms": elapsed_ms,
            "incidents": evidence_result["incidents"],
        })
        state = next_state
        total_llm_calls += 1
        previous_window_end = window.end_index
        if window.end_index >= len(observations):
            break
        cutoff = observations[window.end_index - 1].timestamp_ms - OVERLAP_MS
        next_cursor = window.end_index
        for candidate_index in range(window.start_index, window.end_index):
            if observations[candidate_index].timestamp_ms >= cutoff:
                next_cursor = candidate_index
                break
        cursor = max(cursor + 1, next_cursor)
        window_index += 1

    merged, merge_count = merge_incidents(merge_inputs)
    unattributed = sum(1 for incident in merged if not incident.get("actors"))
    global_actor_aliases = build_actor_index(raw_observations)
    report = {
        "round_id": int(round_id),
        "server": mapping.server,
        "model": getattr(settings, "ai_dossier_model", None) if settings else None,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "preprocess_version": PREPROCESS_VERSION,
        "tokenizer": {"mechanism": tokenizer.mechanism},
        "mapping": {
            "unambiguous": True,
            "method": mapping.method,
            "timezone": mapping.timezone_name,
            "round_context": dict(mapping.round_context),
            "actor_roles_available": bool(mapping.actor_roles),
            "start_utc": mapping.start_utc.isoformat(),
            "end_utc": mapping.end_utc.isoformat(),
            "start_log_clock": mapping.start_log_clock.isoformat(),
            "end_log_clock": mapping.end_log_clock.isoformat(),
            "storage_start_ms": mapping.storage_start_ms,
            "storage_end_ms": mapping.storage_end_ms,
            "padding_ms": mapping.padding_ms,
            "diagnostics": list(mapping.diagnostics),
        },
        "analysis_region": {
            "start_offset_ms": region_start_offset_ms,
            "end_offset_ms": region_end_offset_ms,
            "selected_input_rows": len(raw_rows),
        },
        "preprocessing": {
            "input_rows": dedup.diagnostics.input_rows,
            "output_observations": dedup.diagnostics.output_observations,
            "raw_observation_count": len(raw_observations),
            "raw_observations": [
                _observation_snapshot(observation, global_actor_aliases)
                for observation in raw_observations
            ],
            "compact_observations": compaction.output_count,
            "compacted_member_count": compaction.compacted_member_count,
            "saved_observations": compaction.input_count - compaction.output_count,
            "raw_to_compact_ratio": (
                compaction.output_count / compaction.input_count
                if compaction.input_count else 1.0
            ),
            "removed_by_reason": dedup.diagnostics.removed_by_reason,
            "ambiguous_cross_source_groups": dedup.diagnostics.ambiguous_cross_source_groups,
            "same_source_repetition_rows": dedup.diagnostics.same_source_repetition_rows,
            "source_file_count": len(source_ranges),
        },
        "limits": {
            "context_tokens": CONTEXT_TOKENS,
            "max_prompt_tokens": MAX_PROMPT_TOKENS,
            "event_token_target": EVENT_TOKEN_TARGET,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_state_tokens": MAX_STATE_TOKENS,
            "min_incident_confidence": MIN_INCIDENT_CONFIDENCE,
            "overlap_ms": OVERLAP_MS,
        },
        "windows": window_reports,
        "validation_warnings": [],
        "rolling_state_token_counts": state_token_counts,
        "incidents": merged,
        "metrics": {
            "windows": len(window_reports),
            "llm_calls": total_llm_calls,
            "raw_model_incidents": raw_model_incidents,
            "raw_confidence_values": raw_confidence_values,
            "raw_severity_values": raw_severity_values,
            "rejected_low_confidence": rejected_low_confidence,
            "final_candidate_count": len(merged),
            "unattributed_incidents": unattributed,
            "merge_duplicate_incidents": merge_count,
            "total_elapsed_ms": int((time.monotonic() - started) * 1000),
        },
    }
    report["windows"] = _scrub_report_names(report["windows"], global_actor_aliases)
    report["incidents"] = _scrub_report_names(report["incidents"], global_actor_aliases)
    return report
