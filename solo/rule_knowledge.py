"""Private, page-backed rule references. Text never becomes executable mechanics."""
import hashlib,json,re,sqlite3,unicodedata
from pathlib import Path
from contextlib import contextmanager
from functools import lru_cache

TERMS={
 'advantage':r'преимуществ|помех|advantage|disadvantage',
 'check':r'проверк\w* характеристик|ability check|проверки d20|d20 test',
 'proficiency':r'бонус\w* владени|proficiency bonus',
 'stealth':r'скрытност|скрытн|прячусь|hide action|stealth',
 'surprise':r'врасплох|неожиданност|surprise',
 'initiative':r'инициатив|initiative',
 'concentration':r'концентрац|concentration',
 'exhaustion':r'истощени|exhaustion',
 'rest':r'отдых|rest',
 'saving_throw':r'спасброс|saving throw',
 'opportunity':r'провоцированн\w* атак|opportunity attack',
 'grapple':r'захват|grapple',
 'inspiration':r'вдохновени|heroic inspiration',
 'critical':r'критическ\w* (?:попадани|урон)|critical hit',
 'movement':r'перемещени|скорость|movement|speed',
 'spell':r'заклинани|spellcasting',
 'armor':r'класс\w* (?:брони|доспех)|armor class',
 'carry':r'грузоподъемност|carrying capacity',
 'action':r'бонусн\w* действи|bonus action',
}
RULE_HEADINGS={s.strip() for s in ('преимущество|помеха|преимущество/помеха|проверки характеристик|проверка характеристики|бонус владения|скрытность|спрятаться|неожиданность|инициатива|броски инициативы|концентрация|истощение|короткий отдых|длительный отдых|спасброски|провоцированные атаки|захват|героическое вдохновение|вдохновение|критическое попадание|критические попадания|перемещение|скорость|сотворение заклинаний|класс защиты|класс брони|грузоподъемность|бонусные действия|advantage|disadvantage|ability checks|proficiency bonus|stealth|surprise|initiative|concentration|exhaustion|short rest|long rest|saving throws|opportunity attacks|grappling|heroic inspiration|critical hits|movement|speed|casting spells|armor class|carrying capacity|bonus actions').split('|')}

def normalize(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('ё','е')
    text=re.sub(r'[\u00ad\u200b\ufeff\ufffe]','',text)
    return re.sub(r'\s+',' ',re.sub(r'(?<=\w)[-‑]\s*\n\s*(?=\w)','',text)).strip()

def topics(query):return [key for key,pattern in TERMS.items() if re.search(pattern,normalize(query))]
def query_text(query):
    text=normalize(query)
    text=re.sub(r'\bистощенн\w*','истощение',text)
    return text.replace('класс защиты','класс брони')
def query_topics(query):return topics(query_text(query))

class RuleKnowledge:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.revision=0
        with self.db() as db:db.executescript('''
            CREATE TABLE IF NOT EXISTS books(sha TEXT PRIMARY KEY,title TEXT NOT NULL,path TEXT NOT NULL,edition TEXT NOT NULL,kind TEXT NOT NULL,license TEXT NOT NULL,partial INTEGER NOT NULL,pages INTEGER NOT NULL,readable INTEGER NOT NULL DEFAULT 0,complete INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sections(id INTEGER PRIMARY KEY,book TEXT NOT NULL REFERENCES books(sha),page INTEGER NOT NULL,printed TEXT,heading TEXT NOT NULL,text TEXT NOT NULL,public INTEGER NOT NULL,UNIQUE(book,page,text));
            CREATE VIRTUAL TABLE IF NOT EXISTS rule_fts USING fts5(search,content='');
            CREATE INDEX IF NOT EXISTS sections_page ON sections(book,page);
        ''')
        with self.db() as db:
            if 'index_version' not in {r[1] for r in db.execute('PRAGMA table_info(books)')}:db.execute('ALTER TABLE books ADD COLUMN index_version INTEGER NOT NULL DEFAULT 0')
            if 'topic' not in {r[1] for r in db.execute('PRAGMA table_info(sections)')}:
                db.execute("ALTER TABLE sections ADD COLUMN topic TEXT NOT NULL DEFAULT ''");db.execute('ALTER TABLE sections ADD COLUMN lead INTEGER NOT NULL DEFAULT 0')
                for row in db.execute('SELECT id,heading,text FROM sections').fetchall():
                    label=normalize(re.sub(r'\[[^]]+\]','',row['heading'])).strip(' .:');keys=topics(label) if label in RULE_HEADINGS else []
                    db.execute('UPDATE sections SET topic=?,lead=? WHERE id=?',(keys[0] if keys else '',int(normalize(row['text']).startswith(normalize(row['heading']))),row['id']))
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def import_pdf(self,path,*,title,edition,kind,license,partial=False):
        if edition not in ('dnd2014','dnd2024') or kind not in ('phb','dmg','srd'):raise ValueError('Укажите редакцию и вид источника')
        source=Path(path).resolve()
        with source.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        with self.db() as db:
            row=db.execute('SELECT * FROM books WHERE sha=?',(sha,)).fetchone()
            if row and row['edition']!=edition:raise ValueError('Тот же файл уже отмечен другой редакцией; автоматическая конверсия запрещена')
            if row and row['complete'] and row['index_version']==3:return dict(row,reused=True)
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(source)
        try:
            with self.db() as db:
                old=db.execute('SELECT id,text FROM sections WHERE book=?',(sha,)).fetchall()
                for old_row in old:
                    markers=' '.join('rule'+key.replace('_','') for key in topics(old_row['text']))
                    db.execute("INSERT INTO rule_fts(rule_fts,rowid,search) VALUES('delete',?,?)",(old_row['id'],normalize(old_row['text'])+' '+markers))
                db.execute('DELETE FROM sections WHERE book=?',(sha,))
                db.execute('INSERT OR REPLACE INTO books(sha,title,path,edition,kind,license,partial,pages,readable,complete,index_version) VALUES(?,?,?,?,?,?,?,?,?,0,3)',(sha,title,str(source),edition,kind,license,int(partial),len(pdf),0))
                readable=0
                for index in range(len(pdf)):
                    page=pdf[index];textpage=page.get_textpage()
                    try:text=textpage.get_text_range().replace('\r\n','\n')
                    finally:textpage.close();page.close()
                    if not text.strip():continue
                    readable+=1;lines=text.splitlines();heading=title;printed=None
                    match=re.match(r'^(\d{1,3})(?:$|\s+(?:глава|глоссарий|введение|приложение)\b)',normalize(lines[0])) if lines else None
                    if not match and lines:match=re.match(r'^(?:глава|глоссарий|введение|приложение)[^\n]{0,100}\s(\d{1,3})$',normalize(lines[0]))
                    if match:printed=match[1]
                    buffer=[];length=0
                    def flush():
                        nonlocal buffer,length
                        value='\n'.join(buffer).strip();buffer=[];length=0
                        if not value:return
                        label=normalize(re.sub(r'\[[^]]+\]','',heading)).strip(' .:');keys=topics(label) if label in RULE_HEADINGS else []
                        cursor=db.execute('INSERT OR IGNORE INTO sections(book,page,printed,heading,text,public,topic,lead) VALUES(?,?,?,?,?,?,?,?)',(sha,index+1,printed,heading,value,int(kind!='dmg'),keys[0] if keys else '',int(normalize(value).startswith(normalize(heading)))))
                        if not cursor.rowcount:return
                        markers=' '.join('rule'+key.replace('_','') for key in topics(value))
                        db.execute('INSERT INTO rule_fts(rowid,search) VALUES(?,?)',(cursor.lastrowid,normalize(value)+' '+markers))
                    for line in lines:
                        line=line.strip()
                        if not line:continue
                        label=normalize(re.sub(r'\[[^]]+\]','',line)).strip(' .:')
                        is_heading=label in RULE_HEADINGS or (4<=len(line)<=75 and line.isupper())
                        if is_heading:flush();heading=line
                        if length+len(line)>850:flush()
                        buffer.append(line);length+=len(line)
                    flush()
                db.execute('UPDATE books SET readable=?,complete=1 WHERE sha=?',(readable,sha))
        finally:pdf.close()
        self.revision+=1;self._search.cache_clear()
        return dict(sha=sha,title=title,edition=edition,kind=kind,partial=partial,pages=index+1,readable=readable,reused=False)
    def approve_rules(self,ids):
        # Only an explicit local review can release a DMG rule excerpt. Campaign
        # source material never enters this independent rule index.
        with self.db() as db:
            for id in ids:db.execute('UPDATE sections SET public=1 WHERE id=?',(id,))
        self.revision+=1;self._search.cache_clear()
    def catalog(self):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT sha,title,edition,kind,license,partial,pages,readable,complete AS indexed_complete FROM books ORDER BY edition,kind')]
    def search(self,query,edition,*,limit=2,budget=1000):
        if edition not in ('dnd2014','dnd2024'):raise ValueError('Неизвестная редакция правил')
        return json.loads(self._search(query_text(query)[:400],edition,min(3,max(1,limit)),min(1600,max(100,budget)),(self.revision,self.path.stat().st_mtime_ns)))
    @lru_cache(maxsize=128)
    def _search(self,query,edition,limit,budget,revision):
        keys=topics(query)
        if not keys:return json.dumps(dict(found=False,edition=edition,reason='Нет проверенного правила по этому вопросу. Новая механика не выводится из догадки.',excerpts=[]),ensure_ascii=False)
        expression=' OR '.join('"rule'+key.replace('_','')+'"' for key in keys)
        with self.db() as db:
            rows=db.execute('''SELECT s.id,s.page,s.printed,s.heading,s.text,s.topic,s.lead,b.title,b.sha,b.kind,b.partial,b.license,b.edition
                FROM rule_fts JOIN sections s ON s.id=rule_fts.rowid JOIN books b ON b.sha=s.book
                WHERE rule_fts MATCH ? AND b.edition=? AND b.complete=1 AND s.public=1 AND length(s.text)>=80
                ORDER BY CASE WHEN s.topic IN ('''+','.join('?' for _ in keys)+''') THEN 0 ELSE 1 END,s.lead DESC,bm25(rule_fts) LIMIT 24''',(expression,edition,*keys)).fetchall()
        excerpts=[];remaining=budget
        named=[a or b for a,b in re.findall(r'«([^»]+)»|"([^"]+)"',query)] if re.search(r'правил|заклинани|черт|способност',query) else []
        # Prefer a rule heading over an incidental mention; prefer Russian text
        # for the Russian user, retaining the licensed English SRD as a reference.
        rows=sorted(rows,key=lambda r:(0 if r['topic'] in keys else 1,-r['lead'],0 if re.search('[А-Яа-я]',r['text']) else 1))
        for row in rows:
            if named and not all(name in normalize(row['text']) for name in named):continue
            if remaining<80:break
            text=re.sub(r'[\ufffe\u00ad\u200b\ufeff]','',row['text'])[:min(700,remaining)];remaining-=len(text)
            excerpts.append(dict(source=row['title'],sha256=row['sha'],edition=row['edition'],page=row['page'],printed_page=row['printed'],section=row['heading'],text=text,partial_source=bool(row['partial']),reference_only=True))
            if len(excerpts)>=limit:break
        return json.dumps(dict(found=bool(excerpts),edition=edition,topics=keys,reason='' if excerpts else 'Правило не найдено в доступных проверенных фрагментах этой редакции.',excerpts=excerpts),ensure_ascii=False)
