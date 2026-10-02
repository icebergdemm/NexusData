from services_llm_monster import _round_brief, _round_history_brief


def wave(**changes):
    return {"wave_name": "crawler", "outcome": "repelled", "fate": "destroyed",
            "unresolved": False, "adaptation_eligible": True, "performance": 1.2,
            "effective_difficulty": 2, "players_alive_at_start": 4,
            "players_alive_at_end": 3, "expected_repel_seconds": 20,
            "monsters_dead": 2, "monsters_alive": 0, "monsters_unknown": 0, **changes}


def test_censored_and_legacy_waves_cannot_establish_crew_strength():
    legacy = wave()
    del legacy["adaptation_eligible"]
    brief = _round_brief({"initial_player_count": 0, "waves": [legacy,
        wave(outcome="timeout_engaged", fate="tracking_stopped", performance=0.2),
        wave(unresolved=True), wave(effective_difficulty=0),
        wave(players_alive_at_start=0), wave(performance=float("nan")),
        wave(monsters_unknown=1), wave(monsters_alive=1), wave(monsters_dead=0)]})
    assert brief["assessment"]["performance"] is None
    assert brief["assessment"]["samples"] == 0
    assert brief["quality"]["legacy_assessment"] is True
    assert brief["quality"]["tracking_stopped"] == 2
    assert brief["crew"]["initial_players"] is None


def test_crew_numbers_and_unknown_fates_survive_compression():
    brief = _round_brief({"initial_player_count": 4, "initial_player_count_sample_seconds": 10,
        "waves": [wave(), wave(players_alive_at_start=8, outcome="unresolved",
                              fate="tracking_stopped", unresolved=True, adaptation_eligible=False)],
        "summary": {"crew_deaths_are_observed": True},
        "submarine": {"name": "Vector", "width": 4000, "height": 1200, "extra": "ignored"}})
    assert brief["crew"] == {"initial_players": 4, "initial_sample_seconds": 10,
                            "observed_players_min": 4, "observed_players_max": 8, "wave_samples": 2}
    assert brief["assessment"]["performance"] == 1.2
    assert brief["assessment"]["sample_support"] == 0.2
    assert brief["waves"][0][12:] == [4, 3, 20, False, True, 0, 2, 0]
    assert brief["quality"]["crew_deaths_are_observed"] is True
    assert brief["submarine"] == {"name": "Vector", "width": 4000, "height": 1200}
    history = _round_history_brief(brief)
    for key in ("submarine", "crew", "quality", "assessment"):
        assert history[key] == brief[key]


def test_quality_uses_entire_round_not_wave_sample_or_llm_text():
    waves = [wave(adaptation_eligible=False) for _ in range(30)]
    waves[15] = wave(fate="tracking_stopped", unresolved=True, adaptation_eligible=False)
    brief = _round_brief({"waves": waves, "analysis_text": "Very strong crew"}, sample_limit=3)
    assert len(brief["waves"]) == 3
    assert brief["wave_count"] == 30
    assert brief["quality"]["tracking_stopped"] == 1
    assert brief["assessment"]["performance"] is None


def test_empty_report_keeps_neutral_unknown_assessment():
    brief = _round_brief({})
    assert brief["assessment"]["performance"] is None
    assert brief["crew"]["observed_players_min"] is None
    assert brief["assessment"]["sample_support"] == 0
