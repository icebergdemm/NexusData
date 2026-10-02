import json

import pytest

import discord_bot.address_detector.qwen_classifier as qwen
from services_llm_gateway import LLM_CLIENTS


@pytest.mark.parametrize(("message_count", "expected_tokens"), [(7, 260), (8, 288)])
def test_batch_token_budget_stays_within_gateway_limit(message_count, expected_tokens):
    assert qwen.max_tokens_for(message_count) == expected_tokens
    assert qwen.max_tokens_for(message_count) <= LLM_CLIENTS[qwen.CLIENT_ID]["max_tokens"]


def test_production_message_action_vector_survives_evidence_validation():
    messages = [
        "Ты можешь посмотреть третий раздел?",
        "Ты терпила",
        "Определитесь уже",
        "Спасибо, я придержу вам место",
        "по моему",
        "Он написал: «ты можешь посмотреть третий раздел?»",
        "Фраза «ты можешь посмотреть третий раздел?» считается фамильярной?",
        "Глянь третий раздел",
    ]
    raw = json.dumps({"r": [
        {"i": 0, "c": "D", "r": "ip", "e": "Ты"},
        {"i": 1, "c": "D", "r": "ip", "e": "Ты"},
        {"i": 2, "c": "D", "r": "ii", "e": "Определитесь"},
        {"i": 3, "c": "D", "r": "ip", "e": "вам"},
        {"i": 4, "c": "C", "r": "o", "e": None},
        {"i": 5, "c": "Q", "r": "qc", "e": None},
        {"i": 6, "c": "M", "r": "md", "e": None},
        {"i": 7, "c": "D", "r": "ii", "e": "Глянь"},
    ]}, ensure_ascii=False)
    parsed = qwen.parse_results(raw, list(range(len(messages))))
    guarded = qwen.validate_results_evidence(parsed, {"messages": [
        {"index": index, "text": message} for index, message in enumerate(messages)
    ]})

    assert [row["action_eligible"] for row in guarded] == [True, True, False, False, False, False, False, True]


def test_parser_accepts_compact_evidence_records_and_normalizes_classes():
    content = json.dumps({"r": [
        {"i": 4, "c": "D", "r": "ip", "e": "Ты"},
        {"i": 5, "c": "C", "r": "o", "e": None},
        {"i": 6, "c": "Q", "r": "qc", "e": None},
        {"i": 7, "c": "M", "r": "md", "e": None},
        {"i": 8, "c": "A", "r": "ic", "e": None},
    ]}, ensure_ascii=False)

    assert qwen.parse_results(content, [4, 5, 6, 7, 8]) == [
        {"index": 4, "classification": "direct_familiar_address", "reason_code": "informal_pronoun", "evidence": "Ты"},
        {"index": 5, "classification": "no_violation", "reason_code": "other", "evidence": None},
        {"index": 6, "classification": "quoted_or_reported", "reason_code": "quoted_content", "evidence": None},
        {"index": 7, "classification": "example_or_metadiscussion", "reason_code": "metadiscussion", "evidence": None},
        {"index": 8, "classification": "ambiguous", "reason_code": "insufficient_context", "evidence": None},
    ]


@pytest.mark.parametrize("content", [
    '{"r":[]}',
    '{"r":[{"i":1,"c":"D","r":"ip","e":"ты"},{"i":1,"c":"D","r":"ip","e":"ты"}]}',
    '{"r":[{"i":2,"c":"D","r":"ip","e":"ты"}]}',
    '{"r":[{"i":1,"c":"D","r":"qc","e":"ты"}]}',
    '{"r":[{"i":1,"c":"C","r":"o","e":"unexpected"}]}',
    '{"r":[{"i":1,"c":"D","r":"ip","e":"' + "x" * 49 + '"}]}',
])
def test_parser_rejects_structurally_invalid_or_incompatible_evidence(content):
    with pytest.raises(ValueError):
        qwen.parse_results(content, [1])


@pytest.mark.parametrize(("target", "reason", "evidence", "expected"), [
    ("Ты можешь посмотреть третий раздел?", "informal_pronoun", "Ты", (True, None)),
    ("Ты терпила", "informal_pronoun", "Ты", (True, None)),
    ("мечты сбываются", "informal_pronoun", "ты", (False, "reason_evidence_mismatch")),
    ("Определитесь уже", "informal_pronoun", "Определитесь", (False, "reason_evidence_mismatch")),
    ("Определитесь уже", "informal_imperative", "Определитесь", (False, "plural_or_polite_imperative")),
    ("Спасибо, я придержу вам место", "informal_pronoun", "вам", (False, "reason_evidence_mismatch")),
    ("Фраза «ты можешь посмотреть третий раздел?» считается фамильярной?", "informal_pronoun", "ты", (False, "quoted_or_code_evidence")),
    ("Глянь третий раздел", "informal_imperative", "Глянь", (True, None)),
    ("Посмотри", "informal_imperative", "Посмотри", (True, None)),
    ("Посмотрите журнал", "informal_imperative", "Посмотрите", (False, "plural_or_polite_imperative")),
    ("Сделайте отчёт", "informal_imperative", "Сделайте", (False, "plural_or_polite_imperative")),
    ("Подойдите сюда", "informal_imperative", "Подойдите", (False, "plural_or_polite_imperative")),
    ("> ты понял?", "informal_pronoun", "ты", (False, "quoted_or_code_evidence")),
    ("`ты` понял?", "informal_pronoun", "ты", (False, "quoted_or_code_evidence")),
    ("```text\nты понял?\n```", "informal_pronoun", "ты", (False, "quoted_or_code_evidence")),
    ("Проверка завершена", "informal_imperative", "глянь", (False, "evidence_not_in_target")),
    ("!", "other", "!", (False, "invalid_evidence_span")),
])
def test_direct_evidence_must_be_exact_target_evidence_outside_quotes(target, reason, evidence, expected):
    validator = getattr(qwen, "validate_direct_evidence", None)

    assert callable(validator), "direct evidence validator is missing"
    assert validator(target, reason, evidence) == expected


def test_context_words_cannot_validate_evidence_absent_from_target():
    rows = [{"index": 1, "classification": "direct_familiar_address",
             "reason_code": "informal_pronoun", "evidence": "ты"}]
    guarded = qwen.validate_results_evidence(rows, {"messages": [
        {"index": 0, "text": "Ты понял?"},
        {"index": 1, "text": "Определитесь уже"},
    ]})

    assert guarded[0]["classification"] == "ambiguous"
    assert guarded[0]["evidence_rejection_reason"] == "evidence_not_in_target"


@pytest.mark.parametrize("form", [
    "ты", "тебя", "тебе", "тобой", "тобою", "твой", "твоя", "твоё", "твое", "твои",
    "твоего", "твоей", "твоему", "твоим", "твоими", "твоих", "твою", "твоём", "твоем",
])
def test_closed_informal_pronoun_family_is_verified(form):
    assert qwen.validate_direct_evidence(f"Привет, {form}.", "informal_pronoun", form) == (True, None)


@pytest.mark.parametrize("form", ["вы", "вас", "вам", "вами", "ваш", "ваша", "ваше", "ваши", "вашего", "вашим"])
def test_formal_pronouns_never_verify_informal_evidence(form):
    assert qwen.validate_direct_evidence(f"Привет, {form}.", "informal_pronoun", form) == (
        False, "reason_evidence_mismatch",
    )


def test_missing_evidence_downgrades_only_its_direct_result():
    rows = qwen.parse_results(json.dumps({"r": [
        {"i": 1, "c": "D", "r": "ip", "e": None},
        {"i": 2, "c": "D", "r": "ip", "e": "Ты"},
    ]}, ensure_ascii=False), [1, 2])

    guarded = qwen.validate_results_evidence(rows, {"messages": [
        {"index": 1, "text": "Ты терпила"},
        {"index": 2, "text": "Ты терпила"},
    ]})

    assert guarded[0]["classification"] == "ambiguous"
    assert guarded[0]["model_classification"] == "direct_familiar_address"
    assert guarded[0]["evidence_status"] == "rejected"
    assert guarded[1]["classification"] == "direct_familiar_address"
    assert guarded[1]["action_eligible"] is True
