# DATA PRESERVATION INCIDENT — Preview10

**BLOCKED: сохранность всего проектного состояния относительно начала задачи не доказана.**
Функциональные тесты и native EXE прошли отдельно; они не закрывают этот блокер.

## Причина и границы

18.10.2026 около 06:51:48 и 06:52:08 (Europe/Samara) два моих source-замера
задержки импортировали `solo.app` без `SOLO_TEST=1`. Нижняя строка модуля
`app = create_app() if os.environ.get('SOLO_TEST')!='1' else None` создала
дополнительный default app и выполнила штатную инициализацию `ROOT/data`.
Это ошибка QA-harness. Сам EXE запускался только с явными QA data-dir.
Диагностический default app не запускал lifespan/HTTP-сервер и не вызывал AI.

Начальный список и SHA256: [protected-before.json](protected-before.json).
После полного suite до этих замеров проверка всех 37 файлов была успешной.
В конце: 29 совпали, 8 путей отличаются; итоговый провал сохранён в
[preservation.json](preservation.json), детализация — [files.json](data-incident/files.json).
Исходные базы/WAL не перезаписывались, не восстанавливались и не vacuum/checkpoint
ради совпадения hash. Ненужные собственные процессы завершены; чужие не трогались.

Все пути таблицы находятся под каталогом проекта `data/`:

| Файл | SHA256 до | SHA256 после |
|---|---|---|
| `adventure.sqlite3` | `79c584209f55e03363b8cd67caa45f9d4576e0590e6fc60b0b218353c24d3a8b` | `5ce6a6af31e1070de7a0c100d9db027e47d51bca8a9bc13621112df230c26248` |
| `cloud-usage.sqlite3` | `0687dda84a5c91df379f07d6b936ede6bd99ebdb60e9efb581a72f3e78f065a9` | `06245d5d43edd8ad76ba1eaef5f5ac031ba229ab9824381c5764193924643e89` |
| `cloud-usage.sqlite3-shm` | `fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb` | отсутствует |
| `cloud-usage.sqlite3-wal` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | отсутствует |
| `preparation.sqlite3` | `030ccc13002b956976f7cc14b21455e2feac7af9e20321ea5b01e16c21243a26` | `c79c80f72a19eac183f530157ac9398405cf37f01c71c86127d51e09eaae1d57` |
| `solo.sqlite3` | `5cabcf993a68af2182de72935d28977824072f5e01ea4981bf8aea0a8f875c8a` | `4865ac5b1ee700e1afc4fb3bf2195bd62d3ce444e979d0d4be6610b231150c21` |
| `solo.sqlite3-shm` | `fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb` | отсутствует |
| `solo.sqlite3-wal` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | отсутствует |

## Что доказано

- Все четыре текущие проектные базы имеют `integrity_check=ok`, пустой
  `foreign_key_check`. Чтение выполнялось напрямую SQLite в `mode=ro&immutable=1`,
  без Store/create_app. Полные схемы содержатся в запечатанных after-копиях;
  количества строк — [project-data-forensics.json](project-data-forensics.json).
  [Схемы и hashes логических строк после события](data-incident/after-logical.json);
  корректного before snapshot для их сравнения нет.
- Текущие `campaigns/actions/events/checkpoints`, `calls/approvals`,
  `drafts/sources`, `bundles/documents/jobs` пусты. В Solo имеются штатные
  1 master profile и 3 ruleset versions. Это наблюдение после события,
  не подмена отсутствующего логического baseline.
- Для `adventure.sqlite3` исходный SHA256 точно воспроизводится изменением
  только двух header change-counter полей 24..27 и 92..95 с 9 на 7 в памяти.
  Это доказывает отсутствие изменений остальных байтов этой базы. Оригинал не менялся.
- До события оба проверенных WAL имели SHA256 пустого файла; текущих WAL/SHM
  нет. Для остальных трёх DB отличие нельзя свести к доказанному изменению
  только header. Инициализация допускает изменения схемы и startup recovery;
  утверждать, что всё ограничилось SQLite layout, нельзя.
- 29 остальных файлов byte-identical; их перечень и hashes без содержимого ключей
  сохранены в files.json. Нельзя расширять это до заявления «все данные неизменны».
- Полных исходных копий этих DB/WAL на начало задачи не найдено. Старый clean
  preview.6 ZIP проверен только по central directory: записей `data/*.sqlite3`
  в нём нет. Большой архив не распаковывался. Нет основания использовать более
  старую/неполную базу вместо текущей.

## Сохранённые материалы и исправление harness

[data-incident/after](data-incident/after/) — копии четырёх доступных after DB,
снятые без миграций. Protected-before, все исходные отчёты, ZIP7/8/9 и QA-профили
сохранены. Ключи, пользовательские чаты и PDF в дистрибутив не добавлялись.

[measure_isolated.py](measure_isolated.py) устанавливает SOLO_TEST **до импорта**,
проверяет `solo.app.app is None`, использует отдельную копию QA-базы и блокирует
внешнюю сеть. Повторный замер проверил неизменность всех 37 текущих hash/missing
состояний. Это предотвращение повторения, а не отмена исходного инцидента.
Платных/живых AI-вызовов 0.

Для снятия блокера требуется корректный исходный snapshot с его WAL либо явное
решение владельца о принятии зафиксированной инициализации. До этого общий
release PASS и user_data_unchanged не объявляются. Восстановление вслепую запрещено.
