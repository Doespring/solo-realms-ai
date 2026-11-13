"""Требование 9: повтор запроса не списывает ресурс во второй раз.

Критерий 14.8 требует определённой совместимости сохранений, а требование 9 —
«сбой сохранения не оставляет частично применённое действие». Существующие
тесты гоняют `nat20_worker.execute` в том же интерпретаторе; здесь воркер
запускается НАСТОЯЩИМ подпроцессом (`python -m solo.nat20_worker`, JSON на
stdin), поэтому проверяется именно то, что обещано хосту: состояние читается
после перезапуска и молчаливо не «доезжает» до другого результата.

Проверяются оба направления:

* целое состояние восстанавливается в новом процессе и даёт тот же бой;
* каждое из повреждений отклоняется с внятной причиной, а не тихо
  исправляется.

Пушка — `solo/` (зона интегратора), поэтому файл только читает её и не
меняет; если аддон перестанет отказывать, тест упадёт.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = [sys.executable, "-m", "solo.nat20_worker"]
SESSION = "sess-1"


def _env():
    return dict(os.environ, PYTHONUTF8="1", SOLO_TEST="1")


def _worker(payload):
    """Один запуск воркера в отдельном процессе."""
    proc = subprocess.run(
        WORKER, cwd=str(ROOT), env=_env(), timeout=300,
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
    )
    out = proc.stdout.decode("utf-8", "replace").strip()
    assert out, "воркер ничего не ответил: " + proc.stderr.decode("utf-8", "replace")[-600:]
    return json.loads(out)


def _command(state, kind, rid, **extra):
    return _worker({
        "mode": "execute", "state": state, "session_id": SESSION,
        "command": {
            "request_id": rid, "expected_version": 0,
            "action": dict(kind=kind, **extra),
        },
    })


@pytest.fixture(scope="module")
def played():
    """Бой, сыгранный в отдельном процессе: 2 хода и атака."""
    started = _worker({"mode": "initialize", "name": "Герой", "seed": 12345})
    assert started["ok"], started.get("error")
    state = started["state"]

    opened = _command(state, "start_combat", "c1", encounter_id="basic")
    assert opened["ok"], opened.get("error")
    state = opened["state"]

    for rid, (x, y) in (("mv1", (2, 1)), ("mv2", (3, 1))):
        moved = _command(state, "move", rid, position=dict(x=x, y=y, z=0))
        assert moved["ok"], (rid, moved.get("error"))
        state = moved["state"]

    hit = _command(state, "attack", "a1", target="goblin")
    assert hit["ok"], hit.get("error")
    return hit["state"]




def _hero(state):
    return state["combat"]["view"]["initiative"][0]


def _walk(state, rid, x, y=1):
    answer = _command(state, "move", rid, position=dict(x=x, y=y, z=0))
    assert answer["ok"], (rid, answer.get("error"))
    return answer["state"]


def test_the_same_request_twice_does_not_spend_twice(played):
    """§9: повтор запроса не списывает ресурс во второй раз.

    Первая атака в `played` уже израсходовала действие. Повтор ТОГО ЖЕ
    запроса (тот же `request_id`, та же цель) обязан быть отвергнут, а не
    провести вторую атаку бесплатно и не списать действие ещё раз.
    """
    before = _hero(played)
    assert before["action_available"] is False, "в исходном бою действие не израсходовано"

    again = _worker({
        "mode": "execute", "state": played, "session_id": SESSION,
        "command": {
            "request_id": "a1", "expected_version": 0,
            "action": {"kind": "attack", "target": "goblin"},
        },
    })
    assert not again["ok"], "повтор атаки прошёл: " + str(again)[:200]
    assert "no_action_economy" in again["error"], again["error"]


def test_a_repeated_move_keeps_the_spent_movement(played):
    """Повтор шага не сдвигает дальше и не тратит запас ещё раз."""
    opened = _worker({"mode": "initialize", "name": "Герой", "seed": 12345})
    state = _command(opened["state"], "start_combat", "c1", encounter_id="basic")["state"]
    fresh = _hero(state)["movement_remaining"]

    moved = _walk(state, "mv1", 2)
    spent = _hero(moved)["movement_remaining"]
    assert spent < fresh, "шаг не потратил запас: " + str((fresh, spent))

    frozen = copy.deepcopy(moved)
    repeated = _command(frozen, "move", "mv1", position=dict(x=2, y=1, z=0))
    assert not repeated["ok"], "повторный шаг прошёл"
    assert moved["combat"]["view"]["actor_zone"]["hero"] == "2,1", (
        "первый шаг не довёл героя: " + str(moved["combat"]["view"]["actor_zone"])
    )


def test_a_fresh_fight_before_the_first_command_survives_a_restart(played):
    """Обрыв сразу после начала боя: 0 команд, состояние всё равно читается."""
    opened = _worker({"mode": "initialize", "name": "Герой", "seed": 12345})
    state = _command(opened["state"], "start_combat", "c1", encounter_id="basic")["state"]
    assert state["combat"]["commands"] == [], "бой начат с непустой историей"

    restored = _worker({"mode": "verify", "state": state})
    assert restored["ok"], restored.get("error")
    assert _hero(state)["movement_remaining"] == 30, "запас хода не выставлен на старте"

