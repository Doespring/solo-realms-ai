"""Read-only comparison against owned pre-change backups; no private rows in report."""
import hashlib,json,sqlite3,os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def connect(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)

def compare(profile,baseline):
    with connect(baseline/'solo.sqlite3') as old,connect(profile/'solo.sqlite3') as current:
        counts={}
        for table in ('campaigns','actions','events','checkpoints'):
            if not old.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone():continue
            columns=[r[1] for r in old.execute('PRAGMA table_info('+table+')')]
            fields=','.join('"'+c+'"' for c in columns)
            before=old.execute('SELECT '+fields+' FROM '+table+' ORDER BY rowid').fetchall()
            after=current.execute('SELECT '+fields+' FROM '+table+' ORDER BY rowid').fetchall()
            if before!=after:raise ValueError('Existing private records changed: '+table)
            counts[table]=len(before)
    with connect(profile/'adventure.sqlite3') as library:
        docs=library.execute('SELECT id FROM documents').fetchall()
        for sha, in docs:
            with (profile/'adventure-files'/(sha+'.bin')).open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=sha:raise ValueError('Source SHA256 mismatch')
        pages=library.execute('SELECT count(*) FROM pages').fetchone()[0]
        maps=library.execute("SELECT count(*) FROM materials WHERE kind='map' AND scope='gm'").fetchone()[0]
    return dict(existing_records_preserved=counts,source_sha256_verified=len(docs),indexed_pages=pages,gm_map_pages=maps)

def main():
    windows=Path(os.environ['LOCALAPPDATA'])/'SoloRealmsAI'
    result={}
    for name,profile in [('browser',ROOT/'data'),('windows',windows)]:
        backups=sorted((profile/'backups').glob('adventure-intelligence-baseline-*'))
        if len(backups)!=1:raise ValueError('Expected one explicit baseline backup')
        result[name]=compare(profile,backups[0])
    result.update(inference_generation_calls=0,adventurers_league_source_provided=False)
    (ROOT/'reports/adventure/data-integrity.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))

if __name__=='__main__':main()
