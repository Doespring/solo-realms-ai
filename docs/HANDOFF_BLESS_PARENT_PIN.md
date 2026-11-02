# Передача в поток движка: манифест Bless привязан к отсутствующему родителю

Дата: 27 октября 2026. Ветка: `feature/critical-player-ux`, HEAD `943eb0b`.

## Симптом

9 ошибок в `tests/test_nat20_bless.py`, все на setup:

```
ValueError: Bless content parent/edition mismatch; no implicit migration
  solo/nat20_bless.py:55, в extension():
  if (manifest['release_id'], manifest['rules_version'], manifest['parent_release_digest']) \
     != (RELEASE, 'dnd2024', base_fingerprint): raise ...
```

## Воспроизводимый пример

```powershell
$env:SOLO_TEST='1'; $env:SOLO_NAT20_BLESS_STAND='1'
.venv\Scripts\python.exe runtime\_bl3.py
```

Без `SOLO_NAT20_BLESS_STAND=1` отказ другой (и правильный):
`Bless profile activation pending: …` из `require_stand()`, `solo/nat20_bless.py:25-27`.

## Установленные факты

| факт | значение |
|---|---|
| `parent_release_digest` в Bless-манифесте | `374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75` |
| текущий отпечаток `release()[1]` | `33359fe3538b96bdb1bbde77b5d1d6aa8769d2fe2a2c65f66eb282de5a4bf394` |
| `parent_manifest_sha256` в Bless-манифесте | `37841f2920b7924a1b19e09dd5b75c1821b35ef311216677414c391f80f32e56` — **совпадает** с текущим `integration/nat20/imported/manifest.json` |
| где менялся манифест релиза | только в коммите `9723a04` |
| есть ли `vendor/nat20` в `9723a04` | **нет** — эталонного снимка корпуса в истории не существует |

Отпечаток считается как `digest({runtime: хеши .py, content: манифест, dice_versions})`
(`solo/nat20_content.py:162`). Родительский **манифест содержимого не менялся** — расхождение
дают байты `.py` движка (`activities/{context,damage,dice,formula}.py`, `dnd5e_srd_data/loader.py`).

## Что нужно решить потоку движка

1. Признать `33359fe3…` текущим родителем и выпустить Bless-манифест с новым
   `parent_release_digest`, **пересчитав** `digest({parent_release_digest, extension: manifest})`
   и обновив ожидания тестов — это новый релиз расширения, а не правка байтов на месте.
2. Либо снять с Bless-расширения блокирующий статус и оставить документированный отказ.

Подставлять новый digest в старый конверт нельзя: `digest` покрывает сам манифест расширения,
поэтому смена родителя обязана сменить и отпечаток расширения.

## Почему это не правка этого потока

Перепривязка расширения — решение о релизе корпуса и версиях движка, а не адаптерная правка.
