"""Small player-facing metadata projection; no GM world data or inference."""
import json

DRAFTS_SCHEMA = ('CREATE TABLE IF NOT EXISTS character_drafts(id TEXT PRIMARY KEY,adventure TEXT NOT NULL UNIQUE REFERENCES drafts(id),body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL,rolls TEXT,allow_rolls INTEGER NOT NULL DEFAULT 0,confirmed TEXT,request TEXT)')


def register_chronicles_api(app,store):
    @app.get('/api/chronicles')
    def chronicles():
        with store.connection() as db:
            campaigns=[dict(row) for row in db.execute('''SELECT c.id,c.name,c.created,
                -- r242: экран «Мои приключения» решает по этому полю, открывается
                -- ли история. Без него каждая кампания, включая созданную минуту
                -- назад, подписана «создана в прежней версии» и не открывается.
                CASE WHEN json_extract(c.state,'$.nat20') IS NULL THEN 'legacy' ELSE 'nat20' END AS backend,
                json_extract(c.state,'$.hero.name') AS hero,
                json_extract(c.state,'$.hero.class_name') AS class_name,
                json_extract(c.state,'$.hero.level') AS level,
                json_extract(c.state,'$.location') AS scene,
                -- L02: решение игрока видно в карточке: книга просит уровень,
                -- а Мастер ведёт опасность под героя.
                json_extract(c.state,'$.balance_override') AS balance_override,
                json_extract(c.state,'$.level_gate.required') AS required_level,
                json_extract(c.state,'$.level_gate.gap') AS level_gap,
                coalesce(nullif(json_extract(c.state,'$.preparation.public_description'),''),substr(json_extract(c.state,'$.hero.background'),1,240),'') AS description,
                coalesce((SELECT max(e.created) FROM events e WHERE e.campaign=c.id AND e.undone=0),c.created) AS updated
                FROM campaigns c ORDER BY updated DESC''')]
        creation=app.state.creation;drafts=[]
        with creation.preparation.connection() as db:
            # Схему черновиков героя создавал прежний редактор. Он отключён
            # (единый путь: мастер Nat20), а хроники продолжают читать эту
            # таблицу, поэтому схема объявляется здесь же.
            db.execute(DRAFTS_SCHEMA)
            characters={r['adventure']:dict(r) for r in db.execute("SELECT adventure,json_extract(body,'$.name') AS name,json_extract(body,'$.mode') AS mode FROM character_drafts")}
        for row in creation.list():
            if row['campaign']:continue
            run=app.state.preparation_pipeline.latest(row['id']);status=row['status']
            if run and run['status'] in ('queued','running'):status='preparing'
            elif run and run['status'] in ('failed','blocked','interrupted','stale'):status='needs_review'
            character=characters.get(row['id'],{})
            drafts.append(dict(id=row['id'],name=row['name'],description=row['description'][:500],updated=row['updated'],status=status,files=len(row['files']),hero=character.get('name',''),character_mode=character.get('mode')))
        return dict(campaigns=campaigns,drafts=drafts)
