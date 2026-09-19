"""Диспетчер слышит общий канал, но вмешивается только когда зовут его."""
import pytest

import routes.dispatch as dispatch_routes

CONTEXT = {
    "source": {"kind": "player", "name": "Снегирь-2", "vessel_name": "Снегирь-2"},
    "available_targets": [
        {"kind": "player", "name": "Снегирь-2", "vessel_name": "Снегирь-2"},
        {"kind": "vessel", "name": "Тайфун", "vessel_name": None},
        {"kind": "supply_shuttle", "name": "Снегирь", "vessel_name": None},
        {"kind": "beacon", "name": "Маяк", "vessel_name": None},
    ],
}


def overheard(text, sender="Снегирь-2"):
    return dispatch_routes._is_overheard_traffic(text, CONTEXT, sender)


@pytest.mark.parametrize("text", [
    "Тайфун, выйдите на связь, говорит Снегирь-2.",
    "Тайфун, остановите движение, займите безопасную позицию",
    "Маяк, мы подходим с юга",
])
def test_vessel_to_vessel_chatter_does_not_wake_the_dispatcher(text):
    assert overheard(text)


@pytest.mark.parametrize("text", [
    "Диспетчер, ответьте Снегирю-2",
    "Пост, нужна эвакуация",
    "Тайфун, доложите посту обстановку",
    "Центральная, принимаете?",
])
def test_a_call_for_the_post_is_always_forwarded(text):
    assert overheard(text) == []


@pytest.mark.parametrize("text", [
    "У нас течь в машинном, нужна помощь",
    "Принято",
    "",
])
def test_messages_addressed_to_nobody_keep_reaching_the_ai(text):
    # Ни к кому конкретно: решение вмешиваться остаётся за моделью.
    assert overheard(text) == []


def test_sender_naming_itself_is_not_someone_else():
    assert overheard("Говорит Снегирь-2, идём по курсу") == []


def test_callsign_number_is_ignored_when_matching():
    # «Снегирь-2» в тексте должен узнаваться по позывному челнока «Снегирь».
    context = {
        "source": {"kind": "player", "name": "Тайфун", "vessel_name": "Тайфун"},
        "available_targets": CONTEXT["available_targets"],
    }
    found = dispatch_routes._is_overheard_traffic("Снегирь-2, примите груз", context, "Тайфун")
    assert "снегирь" in found


@pytest.mark.parametrize("text", [
    "Постой, сейчас проверю",
    "Двигаемся постепенно к выходу",
])
def test_words_that_merely_contain_a_callsign_are_not_a_call(text):
    # «постой»/«постепенно» не должны выглядеть как обращение к посту, иначе
    # такой фильтр молча пропускал бы чужие переговоры.
    assert dispatch_routes._mentions_dispatcher(text) is False


def test_unknown_callsign_falls_back_to_forwarding():
    # Борта нет в контексте раунда — придумывать позывные нельзя.
    assert overheard("Мурена, подтвердите приём") == []
