# INCIDENT: почему Hermes «падает» на VPS — диагноз с доказательством

Дата: 01.11.2026. Хост `vm-a77683` (2 vCPU EPYC 7763, 3910 МБ RAM, 38 ГБ, Ubuntu), пользователь `hermes`.
Источники: `journalctl -u hermes-telegram.service` (3810 строк), `~/.hermes/logs/gateway-exit-diag.log` (107 JSON-записей),
`~/.hermes/logs/gateway-shutdown-diag.log` (15 снимков), `~/.hermes/logs/gateway.log`, `~/.hermes/logs/crashwatch.log`,
`~/.hermes/state.db` (read-only), cgroup-счётчики `/sys/fs/cgroup`.

## ВЕРДИКТ (первой строкой)

**Причина названа, и это НЕ «гейтвей сам падает».** Насчитано два независимых механизма:

1. **Kernel OOM-kill под лимитом memory-кэгруппы.** 19 «нечистых» смертей гейтвея за 4 дня: процесс исчезает без
   обработчика сигнала, без строки `Shutdown context`, без traceback. В кэгруппах стоят счётчики `oom_kill`:
   `hermes.slice` **6**, `user-1000.slice` **6** (из них 5 внутри `user@1000.service`), лимиты `MemoryMax`
   **1,1 ГБ** и **2,6 ГБ** при 3,9 ГБ машины и **swap = 0**. Сейчас user-1000.slice работает на 2,55 ГБ из 2,6 ГБ — 98 % лимита.
2. **Всё остальное — остановки по команде человека/агента**, а не отказ демона: **13 событий SIGTERM** (14 строк журнала —
   одна остановка дала две) плюс одно `signal=UNKNOWN`; у каждого известен отправитель из строки `Shutdown context`
   и снимка `ps` в `gateway-shutdown-diag.log` (`sshd-session → bash → systemctl` от root, `hermes gateway restart` из bash,
   либо `parent_pid=1157` — user systemd).

Плюс два усилителя симптома «бот молчит / сессия теряется»:
* `platforms.telegram.extra.drop_pending_on_cold_boot: true` — **37 раз** при старте выброшены сообщения, накопившиеся
  в Telegram за время, пока гейтвей лежал (`gateway.log`: `Cold boot: dropping Telegram updates queued while offline`);
* маршрут топика в `sessions.json` остаётся указывать на сессию, помеченную в `state.db` как `ended` → строка
  `routing key … is ended in state.db but still live in sessions.json; dropping stale entry and recovering/recreating the session (#54878)`,
  и диалог начинается с `history=0` (05:04:47 и 05:10:04 01.11).

## ВАЖНАЯ ПОПРАВКА К ИСКЛЮЧЕНИЯМ ПРОРАБА

Пункт «OOM нет» **не доказан**: он опирался на `journalctl -k`, а этот вывод у пользователя `hermes` пустой **из-за прав**,
а не из-за отсутствия OOM:

```
$ journalctl -k --no-pager | tail -5
Hint: You are currently not seeing messages from other users and the system.
      Users in groups 'adm', 'systemd-journal' can see all messages.
-- No entries --
$ dmesg
dmesg: read kernel buffer failed: Operation not permitted
```

Счётчики кэгрупп читаются без root и показывают обратное:

```
$ cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.events
low 0
high 0
max 695892          # 700 тыс. раз кэгруппа упиралась в свой лимит
oom 9
oom_kill 6          # 6 процессов убито OOM внутри user-1000.slice
$ cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.max
2726297600          # 2,6 ГБ
$ cat /sys/fs/cgroup/user.slice/user-1000.slice/memory.current
2670292992          # 2,55 ГБ — 98 % лимита прямо сейчас
$ cat /sys/fs/cgroup/hermes.slice/memory.events
... oom 6 oom_kill 6 ...
$ swapon --show     # (пусто) — swap отсутствует
```

---

## 1. Кто посылает SIGTERM

Журнал юнита покрывает только 28.10 12:16 → сейчас (запись старше отсутствует: `journalctl --list-boots` — две загрузки,
28.10 10:38–30.10 09:53 и 30.10 09:54–сейчас), поэтому «14 дней» физически нет.

Все **штатные** остановки — по SIGTERM с известным отправителем (`gateway.log`, строка `Shutdown context: signal=… under_systemd=… parent_pid=… parent_name=…`).
Итого 13 событий SIGTERM (14 строк: 29.10 07:54:56 и 07:54:57 — две строки одной остановки) и одно `signal=UNKNOWN`:

| # | Время (UTC) | Инициатор | Доказательство |
|---|---|---|---|
| 1 | 28.10 20:26:32 | root по SSH → `systemctl stop/restart` | `signal=SIGTERM under_systemd=yes parent_pid=1 parent_name=systemd`; в `gateway-shutdown-diag.log` снимок `ps`: `225476 225434 root … systemctl` + `225434 225433 root … bash` + `sshd-session` |
| 2 | 29.10 07:48:43 | systemd (PID 1) | `parent_pid=1`; перед этим в exit-diag `argv: ["-c","gateway","restart","--yes"]` (pid 315554) |
| 3 | 29.10 07:48:52 | неизвестный сигнал | `Shutdown context: signal=UNKNOWN` — обработчик не распознал сигнал |
| 4 | 29.10 07:51:35 | `hermes gateway restart` из bash | `under_systemd=no parent_pid=315552 parent_name=bash` |
| 5 | 29.10 07:54:56 и :57 | другой процесс `hermes` (агент из tool-вызова) | `under_systemd=no parent_pid=305628 parent_name=hermes` |
| 6 | 30.10 09:41:04 | systemd PID 301556 (перед перезагрузкой) | `parent_pid=301556` |
| 7 | 30.10 09:53:56 | systemd PID 301556 — **перезагрузка сервера** | boot ID меняется 30.10 09:54:21 |
| 8 | 30.10 11:13:58 | systemd PID 1 | `parent_pid=1` |
| 9 | 30.10 11:14:59 | **user systemd (1157)** → `systemctl --user restart` | `parent_pid=1157 parent_name=systemd` |
| 10 | 30.10 12:21:30 | root по SSH → `systemctl` | `parent_pid=1`; снимок ps: `106502 106432 root … systemctl`; drop-in `99-shared-budget.conf` записан 12:21:29 |
| 11 | 30.10 20:04:41 | user systemd (1157) | `parent_pid=1157` |
| 12 | 31.10 08:15:30 | root по SSH → `systemctl` | снимок ps: `409070 408865 root … systemctl`, `408865 408864 root … bash` |
| 13 | 31.10 16:17:13 | systemd PID 1 | `parent_pid=1` |
| 14 | 01.11 05:20:49 | root по SSH → `systemctl` (запрошено владельцем) | снимок ps: `649174 649170 root … systemctl` |

Отдельно 7 «плановых» выходов с `SystemExit: 75` (EX_TEMPFAIL = «перезапусти меня, супервизор»):
29.10 06:44:16, 07:28:30, 08:04:01, 09:22:54, 09:25:58, 09:44:42, 30.10 20:03:47 — это штатный путь
`gateway/restart.py: GATEWAY_SERVICE_RESTART_EXIT_CODE = 75`.

**Вывод по разделу:** спонтанных корректных остановок нет ни одной. Любой «тихий» уход гейтвея — это OOM-kill (раздел 4),
а все остальные — команды `systemctl restart` / `hermes gateway restart`, отданные руками или агентом.

## 2. Потеря строки сессии — протокол воспроизведения

**Ответ на вопрос карточки: строка `sessions` пишется ДО первого хода.** Проверено двумя способами.

(а) Код: `gateway/session.py: get_or_create_session()` → `INSERT INTO sessions …` (`hermes_state_local.py:67`) в момент
создания сессии; сообщения (`messages`) добавляются уже внутри хода.

(б) Живой прогон на **копии** боевой базы (`runtime/incident163/repro.db`, боевая не тронута):

```
$ python3 - <<'PY'
import sys, shutil, sqlite3; from pathlib import Path
sys.path.insert(0,'/home/hermes/.hermes/hermes-agent'); import hermes_state as hs
shutil.copy2("/home/hermes/.hermes/state.db","repro.db")
db=hs.SessionDB(Path("repro.db")); sid=db.create_session("repro-r163", source="telegram")
c=sqlite3.connect("repro.db")
print(c.execute("select id,source,ended_at,end_reason,message_count from sessions where id=?", (sid,)).fetchone())
print("messages:", c.execute("select count(*) from messages where session_id=?", (sid,)).fetchone()[0])
PY
('repro-r163', 'telegram', None, None, 0)
messages: 0
```

Строка есть, `ended_at IS NULL`, `end_reason IS NULL`, `message_count=0` — то есть сессия фиксируется до всякого хода.

(в) Живое подтверждение на боевом рестарте: сессия `20261101_050447_1a71a9cf` (топик «Новости») стартовала 05:04:47,
сообщения 05:04 → 05:23, `ended_at IS NULL` — **пережила** рестарт гейтвея 01.11 05:20:49–05:20:57 и продолжилась после него.
Рестарт сам по себе сессию не убивает.

**Что реально теряется.** Не строка, а:
* незавершённый ход (ответ, который в этот момент генерировался) при OOM-kill — SIGKILL обработчиков не вызывает;
* диалог после рестарта начинается с нуля, если маршрут топика указывал на сессию, уже помеченную `ended`:

```
$ grep 'is ended in state.db' ~/.hermes/logs/gateway.log
2026-11-01 05:04:47 WARNING gateway.session: routing key 'agent:main:telegram:dm:885724535:259407'
  -> 20261031_203902_386db9c5 is ended in state.db but still live in sessions.json;
  dropping stale entry and recovering/recreating the session (#54878)
2026-11-01 05:10:04 … 'agent:main:telegram:dm:885724535:259411' -> 20261031_214320_d9d6667d … (#54878)
```

В 05:09:34 владелец в чате спросил ровно это: «Почему сессия новая? Я не понимаю почему сессии затираются».

* **старые сессии удалены**: в `state.db` осталось 15 сессий, самые ранние — 30.10 11:21; в `messages` id начинаются
  с 6827 при `sqlite_sequence.messages = 11109`, то есть ~4282 строки удалены, а не база создана заново
  (`state_meta.store_created_at_utc = 2026-10-28T12:16:09`). Все 30 сессий, на которые указывали резервные копии
  `~/.hermes/sessions/sessions.json.bak-2026103*`, в базе **отсутствуют** (`20261030_090349_a945b9`, `20261031_193029_ed496e47`, …).
  Удаление шло **штатным путём Hermes** — в `state_meta` лежат 54 надгробия `gateway.retired_session.v1.<id>` с `retired_at`
  (последнее 31.10 23:29), которые пишет `hermes_state_mutation_retirement.py`. Автоматический прунинг при этом **выключен**:
  в `config.yaml` нет секции `sessions`, а `gateway/run.py:4424` запускает авто-прунинг только при
  `sessions.auto_prune`/`auto_archive = true`. Значит удаляли вручную (`hermes sessions prune|delete`), скорее всего
  прежней версией самодельного чистильщика: `~/.hermes/scripts/session_gc_coder.py` (31.10 08:11) и
  `/usr/local/sbin/hermes-session-gc` (01.11 05:17, «fixed»).
  Текущая версия безопасна — за 14 запусков ноль кандидатов:
  `~/.hermes/logs/session-gc.log`: `[ПРИМЕНЯЮ] кандидатов: 0; защищено чатовых сессий: 6`.

## 3. Сеть до Telegram

```
$ python3 - <<'PY'   # дедуп по секунде: одно событие = одна строка 'Sticky Telegram path ... failed'
… события 'Sticky path failed' = 13, восстановлений 'transport recovered' = 13,
  по дням: {'2026-10-28': 3, '2026-10-29': 5, '2026-10-30': 2, '2026-10-31': 2, '2026-11-01': 1}
$ grep -c 'Sticky Telegram path .* failed'  ~/.hermes/logs/gateway.log   # 13
$ grep -c 'IPv4 Telegram API IP .* failed'   ~/.hermes/logs/gateway.log   # 12 (у одного события нет второй строки)
$ grep -c 'drop_pending_on_cold_boot'        ~/.hermes/logs/gateway.log   # 37
```

В журнале юнита тех же предупреждений 8 (журнал покрывает 4 дня и часть событий в него не попала) — цифра 91 у прораба
считалась по всем строкам с `ConnectError`, включая дубли от двух логгеров.

Каждое событие — пара строк `Sticky Telegram path 149.154.166.110 failed; re-walking IPv4 literals` +
`IPv4 Telegram API IP 149.154.166.110 failed: ConnectError('')`, затем `Telegram API transport recovered via api.telegram.org
after 149.154.166.110 failed` через 3–11 секунд (пример: 01.11 17:08:52 → 17:09:03).

Путь и MTU в порядке, дело не во фрагментации:

```
$ ip route get 149.154.166.110        # via 144.31.34.1 dev eth0 src 144.31.34.190
$ ip link show eth0                   # mtu 1400
$ ping -c2 -M do -s 1372 -W2 149.154.166.110
2 packets transmitted, 2 received, 0% packet loss, rtt min/avg = 3.9/14.0 ms
```

**Вывод:** деградация — на конкретном «залипающем» IPv4 Telegram (149.154.166.110), типичная фильтрация/шейпинг у
провайдера; адаптер сам уходит на `api.telegram.org` и восстанавливается за секунды. На доставку сообщений это не влияет
(`Flushing text batch → Sending response` идут парами). **Но** при каждом рестарте очередь Telegram выбрасывается:
`drop_pending_on_cold_boot: true` → 37 событий `Cold boot: dropping Telegram updates queued while offline`
(последние 01.11 05:21:00). Всё, что владелец написал боту за время даунтайма, потеряно молча.

Рекомендация (не применена — конфиг продукта): `platforms.telegram.extra.drop_pending_on_cold_boot: false`.

## 4. Память под нагрузкой

Гейтвей (текущий процесс 649204, в `user-1000.slice`):

```
$ systemctl show hermes-telegram.service -p MemoryCurrent -p MemoryPeak
MemoryCurrent=2147033088     # 2,15 ГБ
MemoryPeak=2242945024        # 2,24 ГБ
$ free -m                    # Mem 3910, available 1619, Swap 0
```

Тренд по последним сердцам перед смертями (`gateway-exit-diag.log`, `last_heartbeat_mem`):

| Последний heartbeat | RSS гейтвея | mem_available машины | suspected_oom |
|---|---|---|---|
| 30.10 06:18:27 | 577 МБ | **153 МБ** | да |
| 30.10 09:32:03 | 403 МБ | **91 МБ** | да |
| 30.10 09:37:53 | 278 МБ | **101 МБ** | да |
| 30.10 11:30:36 | **914 МБ** | 1070 МБ | — |
| 31.10 19:33:31 | 437 МБ | 2328 МБ | — |

Пик гейтвея — 2,24 ГБ из 3,9 ГБ машины (запас 1,6 ГБ). Упирается не машина, а **кэгруппа**: `user-1000.slice`
`MemoryMax = 2 726 297 600` (2,6 ГБ), `memory.current` = 2,55 ГБ. При одновременном прогоне pytest и живом приложении
все процессы (гейтвей + `solo-app` + kanban-воркеры + pytest) сидят в одной этой кэгруппе, и ядро убивает самого
крупного — почти всегда гейтвей.

История лимитов (кто и когда ставил, `stat` + `systemctl cat`):

| Когда | Что | Лимит |
|---|---|---|
| 28.10 10:38 | создан `hermes.slice` | `MemoryHigh=1200M`, `MemoryMax=1536M` |
| 30.10 10:33 | `90-hermes-budget.conf.before-max-power` | `MemoryMax=900M`, `CPUQuota=40%` |
| 30.10 10:42 | `90-hermes-budget.conf` | `MemoryMax=1100M` |
| 30.10 12:21 | `hermes-telegram.service.d/99-shared-budget.conf` → `Slice=user-1000.slice` | 2,6 ГБ на всю сессию пользователя |

Итог: 6 OOM-kill в `hermes.slice` (окно 10:33–12:21 30.10) совпадают с 6 из 7 «нечистых» смертей гейтвея в этом окне
(10:55:52, 11:03:04, 11:31:28, 11:35:39, 12:01:22, 12:10:39); седьмая (12:11:08, жизнь 29 с) — короткий дубль-старт.
После переезда в `user-1000.slice` остался один «нечистый» уход — 31.10 19:34:11, и он приходится ровно на 6-й `oom_kill`
этой кэгруппы (5 из них внутри `user@1000.service` — воркеры/приложение, 1 — сам гейтвей на уровне слайса).
8-я «нечистая» смерть — перезагрузка 30.10 09:54:32.

## 5. Итог: причина названа или нет

**Названа.** «Hermes падает» = kernel OOM-kill процесса гейтвея под лимитом memory-кэгруппы при отсутствии swap,
плюс (реже, но заметнее для владельца) остановки по команде `systemctl restart` и потеря очереди Telegram при рестарте.

Что закрывает повтор (все три пункта — root/конфиг, поэтому **не применял**: по карточке системные лимиты — решение владельца):

```bash
# 1. Дать гейтвею запас: либо поднять лимит слайса пользователя, либо вернуть юнит в свой слайс побольше.
mkdir -p /etc/systemd/system/user-1000.slice.d
printf '[Slice]\nMemoryHigh=3400M\nMemoryMax=3600M\n' > /etc/systemd/system/user-1000.slice.d/90-ceiling.conf
systemctl daemon-reload            # лимит применится к новым процессам; безопаснее после systemctl restart user@1000
# 2. Swap 2 ГБ, чтобы всплеск pytest+приложение не убивал гейтвей.
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
# 3. Не выбрасывать сообщения, написанные боту во время даунтайма.
hermes config set platforms.telegram.extra.drop_pending_on_cold_boot false
```

### Исключённые версии (каждая с командой-проверкой)

| Версия | Проверка | Результат |
|---|---|---|
| OOM нет (утверждение прораба) | `journalctl -k` / `dmesg` | **опровергнуто**: команда не имеет прав, вывод пуст не потому что OOM нет; `memory.events: oom_kill 6+6` |
| Лимит `hermes-telegram.service` | `systemctl show … -p MemoryMax` | `infinity` — но лимит стоит на **слайсе**, а не на юните |
| Автоудаление сессий Hermes | `grep -n '^sessions' ~/.hermes/config.yaml`; `gateway/run.py:4424` | секции нет → `auto_prune`/`auto_archive` = false, авто-прунинг не запускается |
| Самодельный session-gc удаляет чаты | `~/.hermes/logs/session-gc.log` | 14 запусков, `кандидатов: 0`, чатовые сессии защищены (код: `CHAT_SOURCES`, `KEEP_KEY_PREFIX`) |
| Кто-то удаляет сессии «на ходу» | `state_meta` надгробия `gateway.retired_session.v1.*` | 54 удаления штатным путём, последнее 31.10 23:29; текущий gc чист |
| Сеть/MTU/фрагментация | `ip link show eth0` (1400), `ping -M do -s 1372` | 0 % потерь, rtt 4–24 мс → путь цел |
| Watchdog systemd / KillMode | `systemctl show -p WatchdogSec -p KillMode` | watchdog не задан, `KillMode=control-group`; смертей по watchdog в журнале нет |
| Рестарт убивает сессию | живой прогон выше | строка сессии существует до первого хода и переживает рестарт |

### Чего данных не хватило

1. **Ядровой журнал недоступен** (`hermes` не в группе `adm`/`systemd-journal`): привязка каждой из 19 «нечистых» смертей
   к OOM сделана по счётчикам кэгрупп + памяти последнего heartbeat, а не по строке `Out of memory: Killed process`.
   Закрывается одной строкой в `journalctl -k` под root или `usermod -aG adm hermes`.
2. **Журнал юнита покрывает 4 дня, не 14** (`journalctl --list-boots` — 2 загрузки): более ранние события утеряны,
   `SystemMaxUse` журнала не увеличивался.
3. **Кто именно вызвал 54 удаления сессий** — надгробия доказывают путь (`hermes sessions prune|delete`), но не автора:
   вызовы CLI из cron/агента в `agent.log` не логируются. Закрывается включением журналирования
   `emit_session_deleted` (`hermes_state_maintenance.py:403`) в `gateway.log` и записью автора в надгробие.

## Следующая операция (для головы/владельца)

1. Владелец применяет три команды из раздела 5 (лимит слайса, swap, `drop_pending_on_cold_boot: false`) — это root/конфиг,
   исполнитель их не трогал по условию карточки.
2. Добавить в `~/.hermes/scripts/crashwatch.py` упреждающий замер памяти (`memory.current / memory.max` кэгруппы и
   `memory.events: oom_kill`): текущий сторож видит только факт смерти через 2 минуты, а слайс живёт на 98 % лимита.
3. Открыть пользователю `hermes` доступ к ядровому журналу (`usermod -aG adm hermes`), иначе следующий разбор снова упрётся
   в пустой `journalctl -k` и привязка смертей к OOM будет только по счётчикам кэгрупп.
