"""PHB 2024 social interaction as host state, not as prose in a journal line.

Book grounding (`rules_data/v2/evidence/phb-008-033.txt`, PHB 2024 RU,
`sha256 40a8ea02…` of the owner's book):

* p.15 (pdf 16) — действие «Влияние»: «проверка Харизмы (Выступление,
  Запугивание, Обман или Убеждение) или Мудрости (Уход за животными),
  чтобы изменить отношение существа»;
* p.16 (pdf 17) — «Отношение НИПов … может быть Дружелюбным, Равнодушным
  или Враждебным»; «Мастер, основываясь на характере НИПов, а также на
  действиях и намерениях вашего персонажа, определяет их реакцию»;
  «элемент случайности остаётся, если Мастер решит, что реакцию НИПа
  должны определить кости»;
* p.18 (pdf 19) — пример: «У вас есть Преимущество, потому что он
  Дружелюбен».

Что здесь напечатано книгой, а что — правило хоста (честно, без выдачи
одного за другое):

* три отношения (Дружелюбное/Равнодушное/Враждебное) и преимущество за
  дружелюбие — книга;
* шаг на одну ступень за исход проверки — правило хоста: книга говорит,
  что Влияние «меняет отношение», но не печатает ни числа ступеней, ни
  того, что провал ухудшает отношение. Ступень названа здесь, чтобы
  переход был проверяемым, а не «на словах»;
* Сл проверки — не книжное число: книга оставляет его Мастеру (стр. 16),
  поэтому хост называет источник Сл (`scene` — запись сцены, `registry` —
  значение хоста) и не выдаёт 15 за напечатанное в книге.

Карточка r179 называла пять ступеней (`hostile/wary/neutral/friendly/
helpful`); книга печатает три, поэтому состоянием стали три, а старые
имена сцены читаются через `ALIASES` вместо нового выдуманного состояния.
"""
ATTITUDES = ("hostile", "indifferent", "friendly")
LABELS = {"hostile": "Враждебное", "indifferent": "Равнодушное",
          "friendly": "Дружелюбное"}
#: Имена, которыми отношение называли раньше (карточка r179, старые сцены).
#: Читаются, но новых состояний не создают: пять ступеней книга не печатает.
ALIASES = {"neutral": "indifferent", "wary": "hostile", "helpful": "friendly",
           "враждебное": "hostile", "равнодушное": "indifferent",
           "дружелюбное": "friendly"}
DEFAULT = "indifferent"
#: Реакция, уже определённая Мастером: проверка не нужна (стр. 16).
RECORDED = ("agrees", "refuses")
REACTION_LABELS = {"agrees": "согласие подтверждено",
                   "refuses": "отказ подтверждён"}


def normalise(value) -> str | None:
    """Ступень отношения из записи сцены; незнакомое — `None`, не догадка."""
    text = str(value or "").strip().casefold()
    if text in ATTITUDES:
        return text
    return ALIASES.get(text)


def attitude_of(npc) -> str:
    """Отношение НИПа как состояние; сцена его не задала — Равнодушное.

    Источник ровно один — `npc['attitude']`. Числовой `relationships.hero.trust`
    из подтверждённых мировых событий остаётся отдельным фактом: книга не
    печатает порога, по которому доверие становится Дружелюбием, и выдумывать
    такую шкалу здесь нельзя.
    """
    return normalise((npc or {}).get("attitude")) or DEFAULT


def position(attitude) -> int:
    return ATTITUDES.index(normalise(attitude) or DEFAULT)


def shifted(attitude, delta) -> str:
    """Ступень отношения, сдвинутая на `delta` и упёртая в край диапазона."""
    return ATTITUDES[max(0, min(len(ATTITUDES) - 1, position(attitude) + delta))]


def advantage(attitude) -> bool:
    """Преимущество на Влияние за дружелюбие — напечатано книгой, стр. 18."""
    return normalise(attitude) == "friendly"


def present(state) -> list:
    """НИПы текущего места, которые стоят в сцене (present по умолчанию)."""
    place = next((row for row in (state or {}).get("locations") or []
                  if row.get("name") == (state or {}).get("location")), None)
    return [npc for npc in (place or {}).get("npc") or [] if npc.get("present", True)]


def select(state, npc_id=None) -> dict:
    """НИП, к которому обращено действие; неоднозначность — отказ, не догадка.

    Действие Влияние адресуется существу, а не сцене. Один НИП в сцене —
    он и есть цель; несколько — нужен `npc_id` из кнопки/команды, иначе
    хост спрашивает имя вместо того, чтобы повлиять на случайного.
    """
    actors = present(state)
    if npc_id:
        by_id = [npc for npc in actors if npc.get("id") == npc_id]
        found = by_id or [npc for npc in actors if npc.get("name") == npc_id]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            # Одноимённые НИПы без id: выбрать первого значило бы повлиять не
            # на того. Честнее отказать, чем угадать.
            raise ValueError("В сцене несколько НИПов с именем «%s»: их нельзя "
                             "различить, назовите другого." % npc_id)
        raise ValueError("В сцене нет НИПа «%s»." % npc_id)
    if not actors:
        raise ValueError("В сцене нет НИПа, на которого можно повлиять. "
                         "Объявите, к кому обращается герой.")
    if len(actors) > 1:
        raise ValueError("В сцене несколько НИПов (%s): назовите, к кому "
                         "обращается герой." % ", ".join(npc.get("name", "?") for npc in actors))
    return actors[0]


def recorded_reaction(npc) -> str | None:
    """Реакция, уже подтверждённая сценой/Мастером, если она записана."""
    text = str((npc or {}).get("reaction") or "").strip().casefold()
    return text if text in RECORDED else None


def check_needed(npc) -> dict:
    """Нужна ли проверка Влияния — решает хост, а не модель (стр. 16).

    Кость бросается только когда реакция НИПа не определена: Мастер уже
    назвал согласие или отказ — проверка была бы вторым ответом на тот же
    вопрос. Возвращается решение с причиной, чтобы журнал объяснил отказ.
    """
    reaction = recorded_reaction(npc)
    if reaction:
        return dict(needed=False, reaction=reaction,
                    reason="реакция НИПа уже определена Мастером (%s), проверка не нужна"
                           % REACTION_LABELS[reaction])
    return dict(needed=True, reaction=None,
                reason="реакция НИПа не определена: Мастер решил, что её определяют кости")


def attitude_change(npc, success) -> dict:
    """Сдвиг отношения по исходу проверки: успех к дружелюбию, провал от него.

    Пишет новое отношение в запись НИПа (состояние кампании) и возвращает
    пару «было/стало», чтобы журнал и память назвали переход, а не «стало
    иначе». Исход `None` (у проверки нет Сл) отношения не двигает.
    """
    before = attitude_of(npc)
    if success is None:
        return dict(before=before, after=before, changed=False, moved=0)
    after = shifted(before, 1 if success else -1)
    if after != before:
        npc["attitude"] = after
    return dict(before=before, after=after, changed=after != before,
                moved=position(after) - position(before))


def describe(name: str, change: dict) -> str:
    """Строка журнала: имя НИПа и переход отношения по-русски."""
    if change["changed"]:
        return "Отношение НИПа «%s»: %s → %s." % (
            name, LABELS[change["before"]], LABELS[change["after"]])
    return "Отношение НИПа «%s» не изменилось: %s." % (
        name, LABELS[change["before"]])

#: Что хост отвечает, когда намерение не попало в каталог действий: книга
#: (стр. 15) прямо говорит, что герой может придумать действие, которого нет
#: в правилах, и тогда Мастер сообщает, возможно ли оно и какой Тест к20
#: нужен. Отказ модели такой ход проглотить не должен.
DECLARE = ("Если задумано действие вне каталога, объявите одной фразой, что именно "
           "делает герой: Мастер скажет, возможно ли оно и нужна ли проверка.")


def declaration_prompt(message: str) -> str:
    """Отказ + просьба объявить намерение; повторно строку не приписывает."""
    text = str(message or "").strip()
    if "что именно делает герой" in text:
        return text
    return (text + " " if text else "") + DECLARE
