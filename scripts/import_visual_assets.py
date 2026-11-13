"""Curated, reproducible visual import from the user catalog. No campaign DB or API keys."""
import csv,json,sqlite3,hashlib,io,re,zipfile,shutil
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
import httpx
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];CATALOG=ROOT/'visual_catalog/source';CACHE=ROOT/'data/asset-cache';VISUAL=ROOT/'web/visual'
LICENSES={'CC0','CC-BY-3.0','OFL-1.1'}
ICONS={'book':'lorc/open-book.svg','chronicle':'lorc/book-cover.svg','inventory':'delapouite/backpack.svg','journal':'lorc/scroll-unfurled.svg','combat':'lorc/crossed-swords.svg','longsword':'lorc/broadsword.svg','shield':'delapouite/attached-shield.svg','leather_armor':'delapouite/leather-armor.svg','healing_potion':'lorc/potion-ball.svg','rope':'delapouite/rope-coil.svg','shortbow':'delapouite/bow-arrow.svg','arrows':'delapouite/quiver.svg','torch':'delapouite/torch.svg','wall':'delapouite/stone-wall.svg','stairs':'delapouite/stairs.svg','door':'delapouite/door.svg','forest':'lorc/pine-tree.svg','compass':'lorc/compass.svg','chest':'lorc/locked-chest.svg','coins':'delapouite/coins.svg','tavern':'delapouite/tavern-sign.svg','person':'delapouite/person.svg','rogue':'lorc/hood.svg','wizard':'delapouite/wizard-face.svg','heart':'delapouite/heart-beats.svg','intelligence':'lorc/brain.svg','perception':'delapouite/eye-target.svg','settings':'delapouite/settings-knobs.svg','save':'delapouite/save.svg','map':'lorc/treasure-map.svg','search':'lorc/magnifying-glass.svg','feather':'lorc/feather.svg','skills':'delapouite/skills.svg','spells':'delapouite/spell-book.svg','rations':'lorc/meat.svg','unarmed':'lorc/hand.svg','club':'delapouite/wood-club.svg','exit':'delapouite/exit-door.svg'}

def sha(data):return hashlib.sha256(data).hexdigest()
def verify_catalog():
    raw=json.loads((CATALOG/'asset_sources.json').read_text(encoding='utf-8-sig'))
    with (CATALOG/'asset_sources.csv').open(encoding='utf-8-sig',newline='') as f:csv_rows=list(csv.DictReader(f))
    with closing(sqlite3.connect((CATALOG/'SoloRealms_AssetCatalog.sqlite3').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
        c.row_factory=sqlite3.Row
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        rows=[dict(r) for r in c.execute('SELECT * FROM sources')]
    keys=set(raw[0]);lookup={r['id']:r for r in rows};assert len(raw)==len(rows)==len(csv_rows)==31 and set(lookup)=={r['id'] for r in raw}
    assert {r['id']:r for r in raw}=={r['id']:r for r in csv_rows}
    for row in raw:assert row=={k:lookup[row['id']][k] for k in keys}
    return raw

def normalize_svg(content):
    if len(content)>500000 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():raise ValueError('Unsafe SVG document')
    root=ET.fromstring(content);allowed={'svg','g','path','rect','circle','ellipse','line','polygon','polyline','title','desc'}
    for element in root.iter():
        if element.tag.split('}')[-1] not in allowed:raise ValueError('Unsafe SVG element')
        for key,value in element.attrib.items():
            if key.lower().startswith('on') or key.split('}')[-1] in ('href','src','style') or re.search(r'url\s*\(|https?://|javascript:|data:',value,re.I):raise ValueError('External or executable SVG')
    for element in list(root):
        if element.tag.split('}')[-1]=='path' and element.attrib.get('fill','black').lower() in ('#000','#000000','black') and element.attrib.get('d','').replace(' ','') in ('M0 0h512v512H0z'.replace(' ',''),'M0512h512V0H0z'):root.remove(element)
    for element in root.iter():
        for attr in ('fill','stroke'):
            if attr in element.attrib and element.attrib[attr]!='none':element.attrib[attr]='#202126'
    root.set('width','64');root.set('height','64');ET.register_namespace('','http://www.w3.org/2000/svg')
    return ET.tostring(root,encoding='utf-8')

def main():
    sources=verify_catalog();VISUAL.mkdir(parents=True,exist_ok=True);license_dir=VISUAL/'licenses';license_dir.mkdir(exist_ok=True)
    registry=ROOT/'visual_catalog/assets.sqlite3'
    with closing(sqlite3.connect(registry)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,origin TEXT NOT NULL,author TEXT NOT NULL,license TEXT NOT NULL,url TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,original_sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,thumbnail TEXT NOT NULL,media TEXT NOT NULL,width INTEGER,height INTEGER,adaptation TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS source_reviews(source_id TEXT PRIMARY KEY,status TEXT NOT NULL,reason TEXT NOT NULL,checked_url TEXT,license_path TEXT,download_sha256 TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS catalog_provenance(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        for file in CATALOG.iterdir():db.execute('INSERT OR REPLACE INTO catalog_provenance VALUES(?,?)',(file.name,sha(file.read_bytes())))
        entries=[]
        def register(id,source_id,origin,content,author,license,url,media,adaptation='Unmodified original'):
            if license not in LICENSES:raise ValueError('Unapproved license')
            if source_id not in {s['id'] for s in sources}:raise ValueError('Source is absent from the user catalog')
            original=sha(content);width=height=None
            if media=='svg':content=normalize_svg(content);adaptation='Obsidian monochrome foreground, transparent background; original geometry retained'
            dest=VISUAL/('fonts' if media=='ttf' else 'icons' if media=='svg' else 'tiles' if id.startswith(('tile-','token-')) else 'frames')/(id+'.'+media);dest.parent.mkdir(exist_ok=True);dest.write_bytes(content)
            thumb=dest
            if media=='png':
                im=Image.open(io.BytesIO(content));width,height=im.size
                if width*height>4000000:raise ValueError('Oversized image')
                thumb=VISUAL/'thumbnails'/(id+'.png');thumb.parent.mkdir(exist_ok=True);im.thumbnail((96,96));im.save(thumb,optimize=True)
            entry=dict(id=id,source_id=source_id,origin=origin,author=author,license=license,url=url,path=dest.relative_to(ROOT/'web').as_posix(),sha256=sha(content),original_sha256=original,bytes=len(content),thumbnail=thumb.relative_to(ROOT/'web').as_posix(),media=media,width=width,height=height,adaptation=adaptation)
            entries.append(entry);db.execute('INSERT OR REPLACE INTO assets VALUES('+','.join('?' for _ in entry)+')',tuple(entry.values()))
        with httpx.Client(trust_env=False,follow_redirects=True,timeout=25) as client:
            def get(url):
                cached=CACHE/('download-'+sha(url.encode()));
                if cached.exists():return cached.read_bytes()
                response=client.get(url);response.raise_for_status()
                if len(response.content)>20000000:raise ValueError('Download exceeds limit')
                cached.write_bytes(response.content);return response.content
            icon_commit=(CACHE/'game-icons-icons-commit.txt').read_text().strip();font_commit=(CACHE/'google-fonts-commit.txt').read_text().strip()
            license_content=(CACHE/'game-icons-license.txt').read_bytes();assert b'Creative Commons 3.0 BY' in license_content;(license_dir/'game-icons.txt').write_bytes(license_content)
            for id,path in ICONS.items():
                url=f'https://raw.githubusercontent.com/game-icons/icons/{icon_commit}/{path}';content=get(url);(CACHE/('icon-'+id+'.svg')).write_bytes(content)
                register(id,'game-icons-github',path,content,'Lorc' if path.startswith('lorc/') else 'Delapouite','CC-BY-3.0',url,'svg')
            for family,files in {'ebgaramond':{'book-regular':'EBGaramond[wght].ttf','book-italic':'EBGaramond-Italic[wght].ttf'},'ptsans':{'ui-regular':'PT_Sans-Web-Regular.ttf','ui-bold':'PT_Sans-Web-Bold.ttf'}}.items():
                prefix=f'https://raw.githubusercontent.com/google/fonts/{font_commit}/ofl/{family}/';license=get(prefix+'OFL.txt');assert b'SIL OPEN FONT LICENSE' in license;(license_dir/(family+'-OFL.txt')).write_bytes(license)
                metadata=get(prefix+'METADATA.pb');assert b'cyrillic' in metadata
                for id,name in files.items():register(id,'fonts-eb',family+'/'+name,get(prefix+name),'EB Garamond Project' if family=='ebgaramond' else 'ParaType','OFL-1.1',prefix+name,'ttf','Supplementary PT Sans family from the same OFL publisher; source catalog retained' if family=='ptsans' else 'Unmodified variable TTF')
        for pack,source,url,selection in [
            ('fantasy','kenney-fantasy-ui','https://kenney.nl/assets/fantasy-ui-borders',{**{'frame-'+str(n):f'PNG/Default/Border/panel-border-{n:03}.png' for n in [0,2,14,15]},'divider':'PNG/Default/Divider/divider-000.png'}),
            ('scribble','kenney-scribble','https://kenney.nl/assets/scribble-dungeons',{**{'tile-'+n:'PNG/Default (64px)/'+n+'.png' for n in ['tile','tiles','wood','water','grass','wall','tree','door_closed','door_open','stairs_down','barrel','chest','campfire','bridge']},**{'token-'+n:'PNG/Default (64px)/Characters/'+n+'_character.png' for n in ['yellow','red','green','purple']}})]:
            with zipfile.ZipFile(CACHE/(pack+'.zip')) as archive:
                assert archive.testzip() is None;lic=archive.read('License.txt');assert b'CC0' in lic;(license_dir/(pack+'-CC0.txt')).write_bytes(lic)
                for id,path in selection.items():register(id,source,path,archive.read(path),'Kenney','CC0',url,'png')
            db.execute('INSERT OR REPLACE INTO source_reviews VALUES(?,?,?,?,?,?)',(source,'imported','Curated stylistic subset; embedded CC0 license verified',url,'visual/licenses/'+pack+'-CC0.txt',sha((CACHE/(pack+'.zip')).read_bytes())))
        for row in sources:
            if row['id'] in ('game-icons','game-icons-github','fonts-eb'):status='imported' if row['id']!='game-icons' else 'reference';reason='Pinned original SVG files / per-author license and OFL fonts verified' if status=='imported' else 'Same icon collection as game-icons-github; avoid duplicate assets'
            elif row['id'] in ('kenney-fantasy-ui','kenney-scribble'):continue
            elif row['policy']=='excluded':status='rejected';reason='Non-commercial restriction; no redistribution permission'
            elif row['verification']=='collection':status='deferred';reason='Collection license does not cover individual resources'
            else:status='deferred';reason='Not selected: additional styles/duplicates/unneeded controls; no bulk downloading'
            db.execute('INSERT OR REPLACE INTO source_reviews VALUES(?,?,?,?,?,?)',(row['id'],status,reason,row['url'],None,None))
        db.commit()
    (VISUAL/'manifest.json').write_text(json.dumps(dict(version=1,catalog_sha256=sha((CATALOG/'SoloRealms_AssetCatalog.sqlite3').read_bytes()),assets=entries),ensure_ascii=False,indent=2),encoding='utf-8')
    (VISUAL/'ATTRIBUTION.md').write_text('# Obsidian Chronicle visual credits\n\nIcons made by Lorc and Delapouite — Game-icons.net, CC BY 3.0, https://creativecommons.org/licenses/by/3.0/. Geometry retained, background removed and foreground changed to obsidian. Per-file source, author and hashes: manifest.json.\n\nKenney Fantasy UI Borders and Scribble Dungeons — Kenney, CC0, https://kenney.nl/. Curated original PNG assets, resized local previews; embedded licenses in licenses/.\n\nEB Garamond — EB Garamond Project; PT Sans — ParaType. SIL OFL 1.1, unmodified TTF with Cyrillic support. PT Sans supplements the catalog font family; original catalog remains unchanged.\n\nPrepared source catalog supplied by the user, retained as visual_catalog/source. Registration of a source is not a blanket redistribution license. No NC/mixed/unknown assets were imported.\n',encoding='utf-8')
    report=dict(catalog_sources=len(sources),json_csv_sqlite_equal=True,original_catalog_unchanged=True,assets_imported=len(entries),bytes=sum(e['bytes'] for e in entries),fonts=4,licenses=sorted({e['license'] for e in entries}),campaign_database_touched=False)
    (ROOT/'reports/obsidian21/catalog-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report))
if __name__=='__main__':main()
