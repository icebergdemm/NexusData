from discord_bot.address_detector.qwen_shadow import SYSTEM_PROMPT


def test_prompt_prioritizes_phrase_discussion_and_keeps_examples_distinct() -> None:
    prompt = SYSTEM_PROMPT.casefold()

    assert 'фраза «ты можешь посмотреть третий раздел?» считается фамильярной?' in prompt
    assert 'example_or_metadiscussion / metadiscussion' in prompt
    assert 'он написал: «ты можешь посмотреть третий раздел?»' in prompt
    assert 'quoted_or_reported / quoted_content' in prompt
    assert 'ты можешь посмотреть третий раздел? (адресату)' in prompt
    assert 'direct_familiar_address / informal_pronoun' in prompt
    assert 'reply_to=i, «а ты сам не видишь?» автору index i' in prompt


def test_prompt_uses_reply_target_only_to_resolve_current_addressee() -> None:
    prompt = SYSTEM_PROMPT.casefold()

    assert 'reply_to=i конкретный адресат — speaker сообщения с index i' in prompt
    assert 'сам reply не создаёт нарушение' in prompt
    assert 'reply_to=i, «а ты сам не видишь?» автору index i →' in prompt
    assert 'похожая фраза в соседнем сообщении не делает текущее метаобсуждением' in prompt


def test_prompt_separates_reported_speech_from_model_directed_injection() -> None:
    prompt = SYSTEM_PROMPT.casefold()

    assert 'он спросил меня: «а ты сам не видишь?» → quoted_or_reported / reported_speech' in prompt
    assert 'слова «ты» в чужой речи не обращение автора' in prompt
    assert 'prompt injection само по себе не делает сообщение метаобсуждением' in prompt
    assert '«ты понял?» после такой команды — direct_familiar_address / informal_pronoun' in prompt
