from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import sqlite3

import pytest

from services_log_analysis import (
    CommunicationRecord,
    CoarseRegion,
    RawObservation,
    build_analysis_schema,
    build_actor_index,
    build_context_text,
    build_event_text,
    build_llm_messages,
    build_windows,
    build_coarse_regions,
    compact_bursts,
    derive_tokenize_url,
    deduplicate_observations,
    extract_command_token_count,
    merge_incidents,
    filter_low_confidence_incidents,
    load_round_observations,
    make_incident_evidence,
    run_dry_run,
    RoundLogMappingError,
    LLMResponseError,
    extract_model_result,
    validate_model_result,
    validate_state,
    build_enrichment_schema,
    validate_enriched_model_result,
    expand_enriched_incident_evidence,
    run_enrichment_regions,
    RoundLogMapping,
)
from services_llm_gateway import LLM_CLIENTS
import services_log_analysis
from scripts.log_analysis_enrichment_probe import (
    _read_checkpoint,
    _region_checkpoint_key,
    _write_checkpoint,
)


def _raw(
    row_id,
    *,
    ts=1_000,
    player="Alice",
    action="wire",
    details="connected wire",
    source="a.txt",
):
    return RawObservation(
        timestamp_ms=ts,
        server="ASPER1",
        player=player,
        action=action,
        details=details,
        source_file=source,
        source_row_id=row_id,
    )


def test_dedup_collapses_only_proven_overlapping_source_copies():
    rows = [
        _raw(1, source="a.txt"),
        _raw(2, source="b.txt"),
        _raw(3, source="a.txt", ts=2_000, details="same source twice"),
        _raw(4, source="a.txt", ts=2_000, details="same source twice"),
        _raw(5, source="old.txt", ts=3_000, details="ambiguous"),
        _raw(6, source="new.txt", ts=3_000, details="ambiguous"),
    ]
    ranges = {
        "a.txt": (900, 2_500),
        "b.txt": (950, 2_600),
        "old.txt": (0, 1_000),
        "new.txt": (2_000, 4_000),
    }

    result = deduplicate_observations(rows, source_ranges=ranges)

    assert len(result.observations) == 5
    assert result.diagnostics.removed_by_reason == {"overlapping_source_files": 1}
    assert result.diagnostics.ambiguous_cross_source_groups == 1
    assert result.diagnostics.same_source_repetition_rows == 2


def test_fingerprints_are_deterministic_and_distinguish_same_source_repetitions():
    rows = [
        _raw(20, ts=2_000, source="a.txt", details="repeat"),
        _raw(10, ts=2_000, source="a.txt", details="repeat"),
    ]
    ranges = {"a.txt": (1_000, 3_000)}

    first = deduplicate_observations(rows, source_ranges=ranges).observations
    second = deduplicate_observations(list(reversed(rows)), source_ranges=ranges).observations

    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]
    assert len({item.fingerprint for item in first}) == 2


def test_burst_compaction_preserves_members_and_enforces_gap_and_span_limits():
    rows = [
        _raw(1, ts=1_000, player="Alice", action="attack", details="same"),
        _raw(2, ts=1_400, player="Alice", action="attack", details="same"),
        _raw(3, ts=1_900, player="Alice", action="attack", details="same"),
        _raw(4, ts=2_500, player="Alice", action="attack", details="same"),
        _raw(5, ts=3_201, player="Alice", action="attack", details="same"),
    ]
    observations = deduplicate_observations(
        rows, source_ranges={"a.txt": (0, 10_000)}
    ).observations

    result = compact_bursts(observations, max_gap_ms=700, max_span_ms=1_000)

    assert result.input_count == 5
    assert result.output_count == 3
    assert result.compacted_member_count == 3
    assert [item.member_count for item in result.observations] == [3, 1, 1]
    assert result.observations[0].first_timestamp_ms == 1_000
    assert result.observations[0].last_timestamp_ms == 1_900
    assert result.observations[0].member_fingerprints == tuple(
        item.fingerprint for item in observations[:3]
    )
    assert set(result.member_fingerprints) == {item.fingerprint for item in observations}


def test_burst_compaction_does_not_cross_intervening_observation_and_is_deterministic():
    rows = [
        _raw(1, ts=1_000, player="Alice", action="attack", details="same"),
        _raw(2, ts=1_100, player="Bob", action="repair", details="other"),
        _raw(3, ts=1_200, player="Alice", action="attack", details="same"),
    ]
    observations = deduplicate_observations(
        rows, source_ranges={"a.txt": (0, 10_000)}
    ).observations

    first = compact_bursts(observations, max_gap_ms=1_000, max_span_ms=2_000)
    second = compact_bursts(observations, max_gap_ms=1_000, max_span_ms=2_000)

    assert [item.member_count for item in first.observations] == [1, 1, 1]
    assert [item.member_fingerprints for item in first.observations] == [
        item.member_fingerprints for item in second.observations
    ]


def test_compact_evidence_expands_to_raw_member_fingerprints():
    raw = deduplicate_observations(
        [
            _raw(1, ts=1_000, action="attack", details="same"),
            _raw(2, ts=1_200, action="attack", details="same"),
        ],
        source_ranges={"a.txt": (0, 10_000)},
    ).observations
    compact = compact_bursts(raw, max_gap_ms=1_000, max_span_ms=2_000)

    class FakeTokenizer:
        mechanism = "test"

        def count(self, text):
            return len(text.split())

    window = build_windows(
        round_id=42,
        observations=compact.observations,
        initial_state={"facts": [], "open_sequences": [], "open_questions": []},
        tokenizer=FakeTokenizer(),
        max_windows=1,
    )[0]
    result = make_incident_evidence(
        {
            "incidents": [{
                "category": "possible_sabotage",
                "severity": 2,
                "confidence": 80,
                "actors": [0],
                "action_evidence": [0],
                "consequence_evidence": [0],
                "intent_evidence": [0],
                "summary": "summary",
                "benign_alternative": "benign",
                "why_still_suspicious": "suspicious",
                "uncertainty": "uncertain",
            }],
            "state": {"facts": [], "open_sequences": [], "open_questions": []},
        },
        window,
        raw_observations=raw,
    )

    incident = result["incidents"][0]
    expected = [item.fingerprint for item in raw]
    assert incident["action_evidence"] == expected
    assert incident["consequence_evidence"] == expected
    assert incident["intent_evidence"] == expected
    assert incident["compact_action_evidence"] == [0]


def test_coarse_regions_merge_only_close_actor_or_overlapping_hypotheses_and_pad_deterministically():
    observations = deduplicate_observations(
        [
            _raw(1, ts=10_000, player="Alice", action="opened", details="hatch"),
            _raw(2, ts=20_000, player="Alice", action="used", details="crowbar"),
            _raw(3, ts=200_000, player="Bob", action="repair", details="hull"),
        ],
        source_ranges={"a.txt": (0, 300_000)},
    ).observations
    incidents = [
        {"evidence": [observations[0].fingerprint], "evidence_snapshots": []},
        {"evidence": [observations[1].fingerprint], "evidence_snapshots": []},
        {"evidence": [observations[2].fingerprint], "evidence_snapshots": []},
    ]

    regions = build_coarse_regions(
        incidents,
        observations,
        padding_ms=5_000,
        actor_gap_ms=30_000,
        max_component_span_ms=30_000,
        round_end_offset_ms=250_000,
    )

    assert [(item.start_offset_ms, item.end_offset_ms) for item in regions] == [
        (0, 15_000),
        (185_000, 195_000),
    ]
    assert regions[0].actors == ("Alice",)
    assert regions[0].observation_fingerprints == tuple(
        item.fingerprint for item in observations[:2]
    )


def test_enrichment_schema_uses_source_local_indexes_and_rejects_wrong_source():
    schema = build_enrichment_schema(log_count=2, communication_count=1, replay_count=0, actor_count=1)
    evidence_item = schema["properties"]["incidents"]["items"]["properties"]["action_evidence"]["items"]
    assert evidence_item["properties"]["source"]["enum"] == ["log"]
    valid = {
        "incidents": [{
            "category": "possible_griefing", "severity": 2, "confidence": 80, "actors": [0],
            "action_evidence": [{"source": "log", "index": 0}],
            "consequence_evidence": [{"source": "log", "index": 1}],
            "intent_evidence": [{"source": "communication", "index": 0}],
            "summary": "summary", "benign_alternative": "benign",
            "why_still_suspicious": "suspicious", "uncertainty": "uncertain",
        }],
        "state": {"facts": [], "open_sequences": [], "open_questions": []},
    }
    validated = validate_enriched_model_result(valid, log_count=2, communication_count=1, replay_count=0, actor_count=1)
    assert validated["incidents"][0]["intent_evidence"] == [{"source": "communication", "index": 0}]
    invalid = {**valid, "incidents": [{**valid["incidents"][0], "action_evidence": [{"source": "communication", "index": 0}]}]}
    with pytest.raises(ValueError, match="action_evidence source"):
        validate_enriched_model_result(invalid, log_count=2, communication_count=1, replay_count=0, actor_count=1)


def test_enriched_evidence_expands_compact_logs_and_communications():
    raw = deduplicate_observations(
        [_raw(1, ts=1_000, action="attack", details="same"), _raw(2, ts=1_200, action="attack", details="same")],
        source_ranges={"a.txt": (0, 10_000)},
    ).observations
    compact = compact_bursts(raw, max_gap_ms=1_000, max_span_ms=2_000).observations
    communication = CommunicationRecord(
        fingerprint="comm-1", timestamp_offset_ms=1_500, actor="Alice", source="chat", text="stop",
        raw_id=91,
    )
    incident = {
        "category": "possible_griefing", "severity": 2, "confidence": 80, "actors": [0],
        "action_evidence": [{"source": "log", "index": 0}],
        "consequence_evidence": [{"source": "log", "index": 0}],
        "intent_evidence": [{"source": "communication", "index": 0}],
        "summary": "summary", "benign_alternative": "benign",
        "why_still_suspicious": "suspicious", "uncertainty": "uncertain",
    }
    expanded = expand_enriched_incident_evidence(
        incident, compact, raw, [communication], [],
    )
    assert expanded["action_evidence"] == [item.fingerprint for item in raw]
    assert expanded["intent_evidence"] == ["comm-1"]
    assert expanded["evidence_records"][-1]["source"] == "communication"


def test_enrichment_emits_stage_progress_and_completed_region_payload():
    observations = deduplicate_observations(
        [_raw(1, ts=1_000, details="wire connected")],
        source_ranges={"a.txt": (0, 10_000)},
        round_start_ms=0,
    ).observations
    region = CoarseRegion(
        start_offset_ms=0,
        end_offset_ms=2_000,
        actors=("Alice",),
        observation_fingerprints=(observations[0].fingerprint,),
        hypothesis_indexes=(3,),
    )
    mapping = RoundLogMapping(
        round_id=42,
        server="ASPER1",
        start_utc=datetime(2026, 9, 18, tzinfo=timezone.utc),
        end_utc=datetime(2026, 9, 18, 0, 1, tzinfo=timezone.utc),
        timezone_name="UTC",
        start_log_clock=datetime(2026, 9, 18),
        end_log_clock=datetime(2026, 9, 18, 0, 1),
        storage_start_ms=0,
        storage_end_ms=60_000,
        padding_ms=0,
        method="test",
    )

    class Tokenizer:
        mechanism = "test"

        def count(self, text):
            return len(text.split())

    def executor(**kwargs):
        del kwargs
        result = {"incidents": [], "state": {"facts": [], "open_sequences": [], "open_questions": []}}
        return {
            "choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 2},
        }, 17

    progress = []
    completed = []
    report = run_enrichment_regions(
        round_id=42,
        mapping=mapping,
        regions=[region],
        compact_observations=observations,
        raw_observations=observations,
        tokenizer=Tokenizer(),
        executor=executor,
        region_index_offset=7,
        progress_callback=progress.append,
        region_callback=completed.append,
    )

    assert [item["stage"] for item in progress] == [
        "region_load", "communication_query", "schema_construction",
        "prompt_construction", "tokenize", "post_chat_completion",
        "response_received", "parsing", "validation",
    ]
    assert completed[0]["index"] == 7
    assert completed[0]["validated"] == {"incidents": [], "state": {"facts": [], "open_sequences": [], "open_questions": []}}
    assert completed[0]["response"]["usage"]["prompt_tokens"] == 11
    assert report["metrics"]["enriched_calls"] == 1


def test_enrichment_probe_checkpoint_round_trips_atomically(tmp_path):
    region = CoarseRegion(
        start_offset_ms=10,
        end_offset_ms=20,
        actors=("Alice",),
        observation_fingerprints=("fingerprint",),
        hypothesis_indexes=(4,),
    )
    path = tmp_path / "v4-enriched-42.json"
    payload = {"version": 1, "round_id": 42, "regions": {}}

    key = _region_checkpoint_key(region)
    payload["regions"][key] = {"index": 0, "status": "succeeded"}
    _write_checkpoint(path, payload)

    assert _read_checkpoint(path) == payload


def test_event_text_uses_local_actor_indexes_and_does_not_expose_names():
    observations = deduplicate_observations(
        [
            _raw(1, player="Alice", details="Alice connected to Bob"),
            _raw(2, ts=1_100, player="Bob", details="Bob opened door"),
        ],
        source_ranges={"a.txt": (0, 2_000)},
    ).observations
    actors = build_actor_index(observations)

    text = build_event_text(observations, actors)

    assert actors == {"Alice": 0, "Bob": 1}
    assert "Alice" not in text
    assert "Bob" not in text
    assert "[0," in text


def test_schema_uses_window_bounds_and_requires_evidence():
    schema = build_analysis_schema(event_count=23, actor_count=4)
    incident = schema["properties"]["incidents"]["items"]
    evidence_fields = [
        incident["properties"][name]
        for name in ("action_evidence", "consequence_evidence", "intent_evidence")
    ]
    actors = incident["properties"]["actors"]

    assert all(field["minItems"] == 1 for field in evidence_fields)
    assert all(field["items"]["minimum"] == 0 for field in evidence_fields)
    assert all(field["items"]["maximum"] == 22 for field in evidence_fields)
    assert actors["items"]["minimum"] == 0
    assert actors["items"]["maximum"] == 3
    assert incident["properties"]["confidence"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 100,
    }
    assert incident["properties"]["severity"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 3,
    }
    assert incident["required"] == [
        "category", "severity", "confidence", "actors",
        "action_evidence", "consequence_evidence", "intent_evidence",
        "summary", "benign_alternative", "why_still_suspicious", "uncertainty",
    ]


def test_model_result_requires_three_evidence_dimensions_and_rejects_bad_indexes():
    valid = {
        "incidents": [
            {
                "category": "unclear_harm",
                "severity": 1,
                "confidence": 12,
                "actors": [],
                "action_evidence": [2],
                "consequence_evidence": [2],
                "intent_evidence": [2],
                "summary": "Potentially harmful sequence.",
                "benign_alternative": "A legitimate role task could explain it.",
                "why_still_suspicious": "The sequence has a harmful consequence.",
                "uncertainty": "Actor is not established.",
            }
        ],
        "state": {"facts": [], "open_sequences": [], "open_questions": []},
    }

    validate_model_result(valid, event_count=3, actor_count=0)

    with pytest.raises(ValueError, match="action_evidence index"):
        invalid = {**valid, "incidents": [{**valid["incidents"][0], "action_evidence": [3]}]}
        validate_model_result(invalid, event_count=3, actor_count=0)

    with pytest.raises(ValueError, match="consequence_evidence"):
        invalid = {**valid, "incidents": [{**valid["incidents"][0], "consequence_evidence": []}]}
        validate_model_result(invalid, event_count=3, actor_count=0)

    with pytest.raises(ValueError, match="intent_evidence"):
        invalid = {**valid, "incidents": [{k: v for k, v in valid["incidents"][0].items() if k != "intent_evidence"}]}
        validate_model_result(invalid, event_count=3, actor_count=0)

    with pytest.raises(ValueError, match="invalid severity"):
        invalid = {**valid, "incidents": [{**valid["incidents"][0], "severity": 0}]}
        validate_model_result(invalid, event_count=3, actor_count=0)

    with pytest.raises(ValueError, match="unexpected or missing fields"):
        invalid = {**valid, "incidents": [{k: v for k, v in valid["incidents"][0].items() if k != "benign_alternative"}]}
        validate_model_result(invalid, event_count=3, actor_count=0)


def test_prompt_does_not_reveal_backend_confidence_threshold():
    window = build_windows(
        round_id=42,
        observations=deduplicate_observations(
            [_raw(1)], source_ranges={"a.txt": (0, 2_000)}
        ).observations,
        initial_state={"facts": [], "open_sequences": [], "open_questions": []},
        tokenizer=type("Tokenizer", (), {"count": lambda self, text: len(text.split())})(),
        max_windows=1,
    )[0]
    messages = build_llm_messages(
        42,
        window,
        {"facts": [], "open_sequences": [], "open_questions": []},
        build_analysis_schema(len(window.observations), len(window.actors)),
    )
    prompt = "\n".join(message["content"] for message in messages)

    assert "confidence below" not in prompt
    assert "confidence >=" not in prompt
    assert "60" not in prompt


def test_low_confidence_incidents_are_filtered_but_counted():
    accepted, rejected = filter_low_confidence_incidents([
        {"confidence": 59, "evidence": [0]},
        {"confidence": 60, "evidence": [1]},
        {"confidence": 100, "evidence": [2]},
    ])

    assert rejected == 1
    assert [item["confidence"] for item in accepted] == [60, 100]


def test_role_context_uses_local_actor_indexes_without_identity_fields():
    text = build_context_text(
        round_context={"mission": "cargo", "submarine_name": "test-sub"},
        actor_roles={"Alice": "engineer", "Bob": "medic"},
        actors={"Alice": 0, "Bob": 1},
    )

    assert '"0":{"role":"engineer"}' in text
    assert "Alice" not in text
    assert "Bob" not in text
    assert "mission" in text


def test_state_has_hard_item_and_length_bounds():
    state = {"facts": [], "open_sequences": [], "open_questions": []}
    validate_state(state)

    too_many = {"facts": ["x"] * 13, "open_sequences": [], "open_questions": []}
    with pytest.raises(ValueError, match="facts"):
        validate_state(too_many)

    too_long = {"facts": ["x" * 181], "open_sequences": [], "open_questions": []}
    with pytest.raises(ValueError, match="facts"):
        validate_state(too_long)


def test_merge_only_merges_same_category_and_shared_evidence():
    first = {
        "category": "possible_sabotage",
        "severity": 1,
        "confidence": 40,
        "actors": [0],
        "evidence": ["fp-a", "fp-b"],
        "summary": "first",
        "benign_alternative": "first benign",
        "why_still_suspicious": "first concern",
        "uncertainty": "one",
        "window_index": 0,
        "evidence_snapshots": [{"fingerprint": "fp-a"}, {"fingerprint": "fp-b"}],
    }
    second = {
        **first,
        "evidence": ["fp-b", "fp-c"],
        "evidence_snapshots": [{"fingerprint": "fp-b"}, {"fingerprint": "fp-c"}],
        "window_index": 1,
    }
    unrelated = {**first, "evidence": ["fp-z"], "window_index": 2}

    merged, count = merge_incidents([first, second, unrelated])

    assert count == 1
    assert len(merged) == 2
    assert merged[0]["evidence"] == ["fp-a", "fp-b", "fp-c"]
    assert [item["fingerprint"] for item in merged[0]["evidence_snapshots"]] == ["fp-a", "fp-b", "fp-c"]


def test_tokenizer_helpers_are_explicit_and_not_character_heuristics():
    assert derive_tokenize_url("https://qwen.example/v1/chat/completions") == "https://qwen.example/tokenize"
    assert extract_command_token_count("token count: 123\n") == 123
    assert extract_command_token_count('{"count": 17}\n') == 17
    with pytest.raises(ValueError, match="token count"):
        extract_command_token_count("no count here")


def test_window_builder_respects_prompt_budget_and_reports_overlap(monkeypatch):
    class FakeTokenizer:
        mechanism = "test"

        def count(self, text):
            return len(text.split())

    observations = deduplicate_observations(
        [_raw(i, ts=i * 1_000, player=f"P{i % 2}", details="event payload") for i in range(1, 12)],
        source_ranges={"a.txt": (0, 20_000)},
    ).observations
    monkeypatch.setattr(services_log_analysis, "EVENT_TOKEN_TARGET", 20)
    monkeypatch.setattr(services_log_analysis, "MAX_PROMPT_TOKENS", 350)

    windows = build_windows(
        round_id=42,
        observations=observations,
        initial_state={"facts": [], "open_sequences": [], "open_questions": []},
        tokenizer=FakeTokenizer(),
        overlap_ms=3_000,
    )

    assert len(windows) > 1
    assert all(window.event_tokens <= 20 for window in windows)
    assert windows[1].observations[0].timestamp_ms < windows[0].observations[-1].timestamp_ms


def test_log_analysis_queue_has_its_own_policy():
    policy = LLM_CLIENTS["log_analysis_dry_run"]

    assert policy["backend"] == "dossier"
    assert policy["max_tokens"] == 2048
    assert policy["max_queue_depth"] >= 8


class _FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class _FakeDB:
    def __init__(self, value):
        self.value = value

    def query(self, _model):
        return _FakeQuery(self.value)


def _round_end(*, paired=True):
    start = SimpleNamespace(
        event_type="start",
        server_name="ASPER1",
        created_at=datetime(2026, 9, 18, 0, 0, 0),
    )
    return SimpleNamespace(
        id=42,
        event_type="end",
        server_name="ASPER1",
        created_at=datetime(2026, 9, 18, 0, 0, 10),
        paired_start=start if paired else None,
    )


def _settings():
    return SimpleNamespace(
        logs_default_timezone="UTC",
        sftp_server_1_name="ASPER1",
        sftp_server_1_logs_timezone="",
        sftp_server_2_name="ASPER2",
        sftp_server_2_logs_timezone="",
        ai_dossier_model="test-qwen",
    )


def test_round_mapping_fails_closed_without_paired_start():
    with pytest.raises(RoundLogMappingError, match="paired start"):
        load_round_observations(
            _FakeDB(_round_end(paired=False)),
            42,
            settings=_settings(),
            log_db_path="does-not-matter.db",
        )


def test_round_mapping_fails_closed_on_conflicting_stable_identifiers():
    start = SimpleNamespace(
        event_type="start",
        server_name="ASPER1",
        created_at=datetime(2026, 9, 18, 0, 0, 0),
        server_session_id="session-a",
        round_token="round-a",
    )
    end = SimpleNamespace(
        id=42,
        event_type="end",
        server_name="ASPER1",
        created_at=datetime(2026, 9, 18, 0, 0, 10),
        paired_start=start,
        server_session_id="session-b",
        round_token="round-a",
    )
    with pytest.raises(RoundLogMappingError, match="server_session_id"):
        load_round_observations(_FakeDB(end), 42, settings=_settings(), log_db_path="missing.db")


def test_one_dry_run_round_returns_anonymized_report(tmp_path):
    from services_logs import _ts_to_ms

    log_path = tmp_path / "logs.db"
    connection = sqlite3.connect(log_path)
    connection.execute(
        "CREATE TABLE log_events (id INTEGER PRIMARY KEY, ts INTEGER, server TEXT, player TEXT, action TEXT, details TEXT, source_file TEXT)"
    )
    base = datetime(2026, 9, 18, 0, 0, 0)
    connection.executemany(
        "INSERT INTO log_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, _ts_to_ms(base + timedelta(seconds=1)), "ASPER1", "Alice", "wire", "Alice connected wire", "a.txt"),
            (2, _ts_to_ms(base + timedelta(seconds=2)), "ASPER1", "Bob", "opened", "Bob opened door", "a.txt"),
        ],
    )
    connection.commit()
    connection.close()

    class FakeTokenizer:
        mechanism = "test-exact-tokenizer"

        def count(self, text):
            return len(text.split())

    def executor(**kwargs):
        result = {
            "incidents": [{
                "category": "possible_sabotage",
                "severity": 1,
                "confidence": 20,
                "actors": [0],
                "action_evidence": [0],
                "consequence_evidence": [0],
                "intent_evidence": [0],
                "summary": "A sequence needs review.",
                "benign_alternative": "A legitimate task may explain it.",
                "why_still_suspicious": "The sequence still has an unexplained consequence.",
                "uncertainty": "The telemetry is incomplete.",
            }],
            "state": {"facts": [], "open_sequences": [], "open_questions": []},
        }
        return {
            "choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "timings": {"prompt_eval_time_ms": 7, "generation_time_ms": 11},
            },
        }, 12

    report = run_dry_run(
        db=_FakeDB(_round_end()),
        round_id=42,
        tokenizer=FakeTokenizer(),
        executor=executor,
        settings=_settings(),
        log_db_path=str(log_path),
    )

    assert report["metrics"]["llm_calls"] == 1
    assert report["metrics"]["raw_confidence_values"] == [20]
    assert report["metrics"]["raw_severity_values"] == [1]
    assert report["windows"][0]["server_prompt_tokens"] == 100
    assert report["windows"][0]["prompt_eval_time_ms"] == 7
    assert report["windows"][0]["generation_time_ms"] == 11
    assert report["preprocessing"]["raw_observation_count"] == 2
    assert report["preprocessing"]["compact_observations"] == 2
    assert report["preprocessing"]["raw_to_compact_ratio"] == 1.0
    assert report["metrics"]["rejected_low_confidence"] == 1
    assert report["incidents"] == []
    assert "Alice" not in json.dumps(report, ensure_ascii=False)
    assert "Bob" not in json.dumps(report, ensure_ascii=False)

    bounded_report = run_dry_run(
        db=_FakeDB(_round_end()),
        round_id=42,
        tokenizer=FakeTokenizer(),
        executor=executor,
        settings=_settings(),
        log_db_path=str(log_path),
        region_start_offset_ms=1_500,
        region_end_offset_ms=2_500,
    )
    assert bounded_report["analysis_region"] == {
        "start_offset_ms": 1_500,
        "end_offset_ms": 2_500,
        "selected_input_rows": 1,
    }
    assert bounded_report["preprocessing"]["raw_observation_count"] == 1


def test_malformed_llm_response_fails_closed():
    with pytest.raises(LLMResponseError, match="not JSON"):
        extract_model_result({"choices": [{"message": {"content": "not json"}}]})
