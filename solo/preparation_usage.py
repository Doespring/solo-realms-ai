"""Сколько стоил один разбор книги: вызовы, токены и отказы по прогону.

Считать цену подготовки можно только по леджеру расходов: подготовка пишет
каждый вызов модели в `cloud-usage.sqlite3` с `turn_ref` вида
`<id прогона>:<ключ части>` (см. `PreparationPipeline` → `llm.scope`), поэтому
прогон находится префиксом — новой таблицы и правки схемы не нужно.
Наружу уходят только числа: ключей и адресов провайдера здесь нет и быть не
может, поэтому в ответ их не попадает ничего.
"""
import sqlite3
from pathlib import Path

# Отказ — вызов, который не дал результата (и мог быть повторён). Отмена —
# решение игрока, а не отказ: в счётчик отказов она не идёт.
REFUSED=('error','invalid_contract','interrupted')


def _connect(path):
    """Только чтение: база живого приложения может быть открыта и кем-то ещё."""
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)


def counted(n,one,few,many):
    """«1 вызов», «2 вызова», «5 вызовов»: число и слово читаются вместе."""
    n=int(n or 0);hundred=n%100
    if 11<=hundred<=14:return f'{n} {many}'
    last=n%10
    return f'{n} '+(one if last==1 else few if 2<=last<=4 else many)


def human_tokens(n):
    """«1,3 млн» вместо «1300000»: длинное число на экране не читается."""
    n=int(n or 0)
    if n>=1000000:return f'{n/1000000:.1f}'.replace('.',',')+' млн'
    return f'{n:,}'.replace(',',' ')


def usage_text(value):
    """Одна строка для экрана: «128 блоков, 95 вызовов, 1,3 млн токенов на вход»."""
    parts=[]
    if value.get('blocks') is not None:parts.append(counted(value['blocks'],'блок','блока','блоков'))
    parts.append(counted(value.get('calls'),'вызов','вызова','вызовов'))
    parts.append(human_tokens(value.get('input_tokens'))+' токенов на вход')
    if value.get('failed'):parts.append(f'{int(value["failed"])} в отказ')
    return ', '.join(parts)


def run_usage(path,run_id,*,blocks=None,total=None):
    """Числа одного прогона или `None`, если леджера либо записей по нему нет.

    Старые прогоны леджера не имеют (счётчик появился позже), и это не ошибка:
    пустое поле экран переживает так же, как пустую историю ошибок.
    """
    if not path or not run_id:return None
    try:db=_connect(path)
    except sqlite3.Error:return None
    try:
        row=db.execute('SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),'
            "COALESCE(SUM(CASE WHEN status IN ('error','invalid_contract','interrupted') THEN 1 ELSE 0 END),0),"
            'COALESCE(SUM(CASE WHEN retry_of IS NOT NULL THEN 1 ELSE 0 END),0) FROM calls WHERE turn_ref LIKE ?',
            (str(run_id)+':%',)).fetchone()
    except sqlite3.Error:return None
    finally:db.close()
    calls=int(row[0] or 0)
    if not calls:return None
    input_tokens,output_tokens,failed,retries=(int(value or 0) for value in row[1:5])
    value:dict=dict(calls=calls,input_tokens=input_tokens,output_tokens=output_tokens,failed=failed,retries=retries)
    if blocks is not None:value['blocks']=int(blocks)
    if total is not None:value['total']=int(total)
    value['text']=usage_text(value)
    return value
