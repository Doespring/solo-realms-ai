"""Сторож разбора книги: прогон живёт в базе, а поднимает его сторож.

Зачем. `PreparationPipeline.run` — задача внутри процесса приложения. Владелец
закрыл вкладку, `solo-app` перезапустился, процесс упал — задача умирает вместе
с ним, а в `creation_runs` остаётся `running`, которого на самом деле нет.
Раньше такой прогон молча ждал кнопки. Теперь сторож каждые 60 с смотрит в
базу и поднимает его сам:

- в `running`, а воркера нет дольше 90 с (`heartbeat` старее порога или чужая
  метка воркера при живом сердцебиении второго экземпляра не в счёт) — поднять;
- `interrupted` с остатком — продолжить с того же блока;
- остатка нет — закрыть честным итогом, а не держать «в работе» вечно.

Кто именно воркер, видно по `creation_runs.worker` и `heartbeat`: это и есть
защита от двойной оплаты, когда рядом работает второй экземпляр приложения.

Расписание отделено от логики: `tick()` — один проход (его зовут в тесте),
`start()`/`stop()` — поток расписания в приложении. Всё, что поднято, попадает
в лог словами «поднял после перезапуска» и причиной — иначе «само продолжилось»
нечем доказать.
"""
import asyncio,logging,time
from pathlib import Path

logger=logging.getLogger('solo.preparation.watchdog')

#: Как часто сторож смотрит в базу.
DEFAULT_INTERVAL=60.0
#: Сколько прогон может молчать, прежде чем его сочтут брошенным.
DEFAULT_STALE=90.0


class PreparationWatchdog:
    def __init__(self,pipeline,*,interval=None,stale=None,log_path=None):
        from .preparation_pipeline import STALE_SECONDS
        self.pipeline=pipeline
        self.interval=float(DEFAULT_INTERVAL if interval is None else interval)
        self.stale=float(STALE_SECONDS if stale is None else stale) or DEFAULT_STALE
        self.log_path=Path(log_path) if log_path else None
        self.task=None
        self.raised=[]
        self.ticks=0

    # --- расписание -------------------------------------------------------
    async def start(self):
        if self.task is None or self.task.done():self.task=asyncio.create_task(self._loop())
        return self.task

    async def stop(self):
        task=self.task;self.task=None
        if task is not None and not task.done():
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass

    async def _loop(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception as exc:logger.warning('r238: сторож подготовки упал на проходе: %s',exc)
            await asyncio.sleep(self.interval)

    # --- один проход ------------------------------------------------------
    def log(self,text):
        """Слово в лог приложения и в файл профиля: подъём должен быть виден."""
        logger.warning('%s',text)
        if self.log_path is None:return
        try:
            self.log_path.parent.mkdir(parents=True,exist_ok=True)
            with self.log_path.open('a',encoding='utf8') as handle:
                handle.write(time.strftime('%Y-%m-%d %H:%M:%S ')+text+'\n')
        except OSError:pass

    def candidates(self):
        with self.pipeline.preparation.connection() as db:
            return [dict(row) for row in db.execute('SELECT id,status,worker,heartbeat FROM creation_runs '
                'WHERE status IN ("queued","running","interrupted") ORDER BY created')]

    def live(self,id):
        """Живой ли воркер у прогона В ЭТОМ процессе."""
        task=self.pipeline.tasks.get(id)
        return task is not None and not task.done()

    async def tick(self):
        """Один проход: что подняли и почему. Возвращает id поднятых прогонов."""
        now=time.time();raised=[];self.ticks+=1
        for row in self.candidates():
            id=row['id']
            if self.live(id):continue
            left=self.pipeline.remaining(id)
            if not left['resumable']:
                # Остатка нет — поднимать нечего, но и «в работе» держать незачем.
                if left['total'] and row['status']!='interrupted':
                    self.pipeline.close_leftover(id,left)
                    self.log(f'r238: прогон {id} закрыт честным итогом: готово {left["done"]} из {left["total"]}')
                continue
            quiet=now-float(row['heartbeat'] or 0)
            foreign=bool(row['worker']) and row['worker']!=self.pipeline.worker
            if row['status']=='running':
                if quiet<self.stale:
                    # Сердцебиение свежее: либо это наш воркер (тогда `live`
                    # выше уже отсёк), либо чужой живой экземпляр приложения.
                    # Второй воркер на тот же прогон — двойная оплата.
                    continue
                reason=('воркера нет '+str(int(quiet))+' с' if not foreign
                    else 'воркер '+str(row['worker'])+' пропал, молчит '+str(int(quiet))+' с')
            elif row['status']=='interrupted':
                reason='после перезапуска приложения, осталось '+str(left['total']-left['done'])+' блоков'
            else:
                reason='стоял в очереди без воркера'
            try:await self.pipeline.resume(id,reason)
            except Exception as exc:
                self.log(f'r238: прогон {id} не поднялся ({reason}): {exc}')
                continue
            self.raised.append(id);raised.append(id)
            self.log(f'r238: поднял после перезапуска прогон {id} ({reason}), готово {left["done"]} из {left["total"]}')
        return raised
