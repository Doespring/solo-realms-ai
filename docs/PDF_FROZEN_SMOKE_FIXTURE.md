# Export для frozen PDF smoke .5

Контракт Сборке/root, 18 октября 2026. Меняется только smoke_fixture.py и свои
тесты/отчёт; desktop_main/desktop_observations/hooks/package остаются Сборке.

```python
from scripts.smoke_fixture import make_pdf_world_smoke_llm, smoke_pdf_bytes
llm = make_pdf_world_smoke_llm(data, smoke_test=True, phase='prepare')
# До create_app, после host-проверки нового изолированного data-dir.
app = create_app(data / 'solo.sqlite3', llm, experimental=True, offline_mock=True)
pdf = smoke_pdf_bytes()  # bytes настоящего одностраничного текстового PDF
```

Для второго процесса: phase='continue'. Host проверяет собственный marker
успешной первой фазы/cid/hash; фабрика дополнительно проверяет свой owner-marker
в подпапке data/pdf-world-smoke. prepare требует новую подпапку и отсутствие
существующей solo.sqlite3; continue требует прежнюю исключительно smoke-конфигурацию.
Без smoke_test=True фабрика отказывает до файловых операций.

Возвращается обычный CloudLLM с httpx.MockTransport, trust_env=False, без redirects,
memory-only synthetic vault (без DPAPI). Синтетические тарифы/бюджеты существуют
только в собственной подпапке. Consent/profile/budget/JSON/world/native guards
остаются стандартными; источник не превращается в готовый world напрямую.
Транспорт замкнутый: неизвестные запросы/источники отказывают, внешнего fallback нет.

Поддержанные контролируемые utterances:
- `Осматриваюсь` → observe;
- `Спрашиваю Dorn про bridge` → conversation с видимым Dorn;
- `Проверяю ловкость` → зарегистрированный check save-dex.
- `Проверяю wis` → зарегистрированный check save-wis для continue.

PDF: source2014, Forge/Tower, Dorn и знание `The bridge is closed after the rain.`.
Preparation: campaign-prepare-v1 с world_structure=true, реальные source_span IDs
из переданного пакета; компактный source-extract-v1 не принимается как полный мир.
Остальной бой/предметы/root UI выполняются обычными typed commands, не fixture prose.

Доказательство wire без раскрытия содержимого:
`llm.smoke_packet_log` — безопасные записи текущего процесса;
`llm.smoke_packet_log_path` — Path на append-only packets.jsonl обеих фаз.
В записи phase, kind, model, message_roles, page/span counts, transport, synthetic
usage; нет prompts, responses, quotes, ключей. CloudLLM.metrics остаётся штатным.
Для hook также `llm.pdf_world_smoke is True`, `llm.pdf_smoke_packets` — alias
безопасного текущего списка, НЕ raw packets. `.kind=='preparation_world'` или
`.task=='campaign-prepare-v1'` позволяют считать preparation; у continue их 0.
Factory вызов без `smoke_test=True` намеренно отклоняется, phase передаётся явно.

## Source freeze / результат

**Готово, source freeze.** Fixture перенесён из solo/smoke_fixture.py в scripts/smoke_fixture.py в этапе 3; собственный
tests/test_frozen_pdf_smoke_fixture.py и этот документ. Прежний DesktopFixture
не менялся. Импортов tests/scripts в bundled коде нет. Подключение и две отдельные
OS/frozen фазы выполняет Сборка; этот исходный тест не выдаётся за frozen-приёмку.

Точный интерфейс:

```python
llm = make_pdf_world_smoke_llm(data, smoke_test=True, phase='prepare')
assert llm.pdf_world_smoke is True
pdf_bytes = smoke_pdf_bytes()
# Continue в отдельном host-процессе, после его проверки marker:
llm = make_pdf_world_smoke_llm(data, smoke_test=True, phase='continue')
prepared_calls = sum(p['task'] == 'campaign-prepare-v1' for p in llm.pdf_smoke_packets)
```

Собственный marker фабрики `data/pdf-world-smoke/fixture.json` не конфликтует с
marker host `data/.pdf-world-smoke.json` format=solo-pdf-world-smoke-v1.
Обычный prepare_profile marker может уже существовать; в prepare требуется лишь
новая собственная подпапка и отсутствие solo.sqlite3 до create_app. Continue читает
только свою synthetic-конфигурацию. Синтетический provider явно называется
`Synthetic PDF smoke`, base URL — `https://pdf-smoke.invalid/v1`; профиль проходит
обычную CloudProfile validation, включая повторное открытие. Тарифы 1/2 RUB за
миллион, пределы 100 RUB — исключительно тестовые значения собственного профиля.
SSE usage искусственный, ledger calls/cost возможны; **внешних/оплаченных вызовов 0**.

Транспорт принимает только свой POST /v1/chat/completions на pdf-smoke.invalid;
остальное — отказ без fallback. Trust-env/proxy и redirects отключены. Нет реального
ключа, чтения пользовательского vault, DPAPI writes или загрузки пользовательского
профиля. Полные packets не пишутся: persisted JSONL содержит только безопасные
поля task/kind/phase/model/roles/counts с метками MockTransport/synthetic.

Один конечный целевой тест: **1 passed, 12,51 с**, существующие Starlette/lark
deprecation. Через зарегистрированные API без world/state injection:

- настоящий synthetic PDF → import → consent guard (отказ без отправки) → полная
  подготовка по действительным spans → отдельное confirm-world;
- source2014 + явно подтверждённый Native Rogue/human 2024, без обязательного боя;
- осмотр, закрытое NPC-знание о мосте, save-dex, сохранение;
- повторное открытие app и фабрики в continue: тот же public snapshot/version,
  затем save-wis; в этой фазе **0 preparation calls**;
- packet JSONL содержит обе фазы и ровно **1 preparation call** суммарно,
  не содержит quotes/private canary/key; DPAPI-файлов нет;
- без explicit smoke флага, без owner-marker и при prepare поверх существующей
  кампании фабрика отказывает. В тесте запрещены внешние socket.connect; локальная
  socketpair Windows asyncio разрешена. Платных/живых AI-запросов не было.

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_frozen_pdf_smoke_fixture.py --basetemp "$env:TEMP/solo-frozen-pdf-fixture-tests"
```

SOLO_TEST=1 задаётся до app, PYTHONDONTWRITEBYTECODE=1. Ранние исправления касались
тестовой socketpair-проверки и корректной synthetic provider validation при reopen;
не ослаблялись штатные consent/profile/money/source/canon/Native guards.
Проверка реального EXE/второго процесса, renderer и frozen dependency inclusion
остаётся Сборке. Backend10/UI/regression/full здесь не повторялись.

.4 dist и Drive .3 snapshot не открывались/не менялись; desktop_main,
desktop_observations, hooks/package, движок, память и UI не редактировались.

Очистка: свои TestClient закрыты, активных тестов/серверов нет. Собственный стенд
`C:\Users\iwa41\AppData\Local\Temp\solo-frozen-pdf-fixture-tests` сохранён до
приёмки frozen .5 как воспроизводимое доказательство/packet log. Удаление по новой
схеме не предпринималось; прежний policy-отказ не обходился. Чужие данные не тронуты.
