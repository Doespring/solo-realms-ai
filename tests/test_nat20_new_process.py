"""Требования 3 и 9: сохранение целостно и релиз проверяется.

Требование 9: отдых восстанавливает ресурсы, но не во время боя.

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


def _fighting():
    """Состояние с настоящим боем и двумя командами в истории."""
    state = _worker({"mode": "initialize", "name": "Герой", "seed": 12345})["state"]
    out = _command(state, "start_combat", "sc1", encounter_id="basic")
    assert out["ok"], out.get("error")
    state = out["state"]
    out = _command(state, "move", "mv1", position=dict(x=2, y=1, z=0))
    assert out["ok"], out.get("error")
    return out["state"]


def test_a_sound_save_restores_in_a_fresh_process():
    """Целое состояние переигрывается в НОВОМ процессе и совпадает."""
    state = _fighting()
    # `start_combat` фиксируется отдельно; в `commands` только команды игрока.
    assert len(state["combat"]["commands"]) == 1, len(state["combat"]["commands"])
    assert _worker({"mode": "verify", "state": state})["ok"]


def test_a_tampered_history_is_refused_not_partially_applied():
    """Подмена действия в истории ловится реплеем, а не применяется молча."""
    state = _fighting()
    bad = copy.deepcopy(state)
    bad["combat"]["commands"][0]["action"]["position"]["x"] = 4
    out = _worker({"mode": "verify", "state": bad})
    assert not out["ok"], "подменённая история принята"
    assert "replay diverged" in out["error"], out["error"]


def test_a_corrupted_digest_is_refused():
    """Испорченный отпечаток исходного состояния не принимается."""
    state = _fighting()
    bad = copy.deepcopy(state)
    bad["combat"]["initial_digest"] = "0" * 64
    out = _worker({"mode": "verify", "state": bad})
    assert not out["ok"], "испорченный отпечаток принят"
    assert "initial replay diverged" in out["error"], out["error"]


def test_a_save_from_another_release_demands_migration():
    """Сохранение чужого релиза требует явной миграции (требование 3)."""
    state = _fighting()
    bad = copy.deepcopy(state)
    bad["release_digest"] = "0" * 64
    out = _worker({"mode": "verify", "state": bad})
    assert not out["ok"], "чужой релиз принят без миграции"
    assert "migration" in out["error"].lower(), out["error"]
