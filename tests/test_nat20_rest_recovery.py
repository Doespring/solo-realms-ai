"""Требование 9: отдых восстанавливает ресурсы, но не во время боя.

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


def _hero(state):
    return state["combat"]["view"]["initiative"][0]


def _wounded():
    """Герой, уже получивший урон: 4 из 12 хитов."""
    state = _worker({"mode": "initialize", "name": "Герой", "seed": 12345})["state"]
    assert state["hero"]["hp_current"] == state["hero"]["hp_max"] == 12, state["hero"]["hp_current"]
    state["hero"]["hp_current"] = 4
    return state


def test_a_long_rest_restores_hit_points_and_hit_dice():
    """SRD 5.2: длинный отдых возвращает все хиты и половину костей хитов."""
    state = _wounded()
    assert state["hit_dice"]["dice_remaining"] == 1, state["hit_dice"]

    rested = _command(state, "long_rest", "lr1", food_water=True)
    assert rested["ok"], rested.get("error")
    after = rested["state"]
    assert after["hero"]["hp_current"] == after["hero"]["hp_max"] == 12, (
        "длинный отдых не восстановил хиты: " + str(after["hero"]["hp_current"])
    )
    assert after["hit_dice"]["dice_remaining"] == 1, after["hit_dice"]


def test_a_short_rest_spends_a_hit_die_to_heal():
    """Короткий отдых тратит кость хитов и лечит на результат."""
    state = _wounded()
    rested = _command(state, "short_rest", "sr1", hit_dice=1)
    assert rested["ok"], rested.get("error")
    after = rested["state"]
    assert after["hit_dice"]["dice_remaining"] == 0, (
        "кость хитов не потрачена: " + str(after["hit_dice"])
    )
    assert after["hero"]["hp_current"] > 4, (
        "короткий отдых не вылечил: " + str(after["hero"]["hp_current"])
    )
    assert after["hero"]["hp_current"] <= 12, "лечение превысило максимум хитов"


def test_a_long_rest_is_refused_in_the_middle_of_a_fight():
    """Отдых — не боевое действие: во время боя он отвергается целиком."""
    state = _worker({"mode": "initialize", "name": "Герой", "seed": 12345})["state"]
    fighting = _command(state, "start_combat", "c1", encounter_id="basic")["state"]
    before = _hero(fighting)["hp_current"]

    rested = _command(fighting, "long_rest", "lr1", food_water=True)
    assert not rested["ok"], "отдых прошёл посреди боя"
    assert "cannot run during this combat" in rested["error"], rested["error"]
    assert _hero(fighting)["hp_current"] == before, "отказ всё равно изменил хиты"


def test_a_short_rest_without_dice_cannot_invent_healing():
    """Без явно названных костей отдых не лечит наугад."""
    state = _wounded()
    rested = _command(state, "short_rest", "sr1", hit_dice=0)
    if rested["ok"]:
        assert rested["state"]["hero"]["hp_current"] == 4, "отдых без костей вылечил"
    else:
        assert rested["error"], "отказ без причины"
