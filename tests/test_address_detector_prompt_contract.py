from discord_bot.address_detector.qwen_shadow import SYSTEM_PROMPT


def test_prompt_prioritizes_phrase_discussion_and_keeps_examples_distinct() -> None:
    prompt = SYSTEM_PROMPT.casefold()

    assert 'фраза «ты можешь посмотреть третий раздел?» считается фамильярной?' in prompt
    assert 'example_or_metadiscussion / metadiscussion' in prompt
    assert 'он написал: «ты можешь посмотреть третий раздел?»' in prompt
    assert 'quoted_or_reported / quoted_content' in prompt
    assert 'ты можешь посмотреть третий раздел? (адресату)' in prompt
    assert 'direct_familiar_address / informal_pronoun' in prompt
    assert 'считается ли «глянь сюда» фамильярным обращением?' in prompt
