"""Local corpus QA only. No API client, user profile mutation, or EXE build."""
import os,sys,json,time,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ['SOLO_TEST']='1'
from solo.adventure_library import AdventureLibrary,digest
from solo.adventure_worker import run,render_material
from solo.pdf_pipeline import PDFPipeline
from solo.rule_registry import coverage

def main():
    qa=ROOT/'data/qa-pdf-pipeline-stage1';qa.mkdir(exist_ok=False)
    reports=ROOT/'reports/pdf-pipeline-stage1';reports.mkdir(parents=True,exist_ok=True)
    sources=[(Path.home()/'Downloads/Hoard of the Dragon Queen RUS.pdf','dnd2014'),(Path.home()/'Downloads/Руководство Мастера 2024 v1.1.pdf','dnd2024')]
    phb=Path.home()/"Downloads/Player's Handbook 2024 RUS.pdf"
    protected={str(p):digest(p) for p in [*(p for p,e in sources),phb]}
    lib=AdventureLibrary(qa);index=PDFPipeline(lib);results=[];evidence=[]
    for path,edition in sources:
        started=time.perf_counter();bundle=lib.create(path.stem,dict(edition=edition));job=lib.enqueue(bundle['id'],path,path.name,'book');run(qa,job['id'])
        result=lib.job(job['id']);assert result['status']=='complete',result
        doc=job['document'];summary=index.summary(doc);assert summary['indexed_pages']==result['total']
        with lib.db() as db:
            pages=db.execute('SELECT number,content FROM pages WHERE document=? ORDER BY number',(doc,)).fetchall()
            texts=sum(len(p['content']) for p in pages)
            geometry_missing=0
            for page in pages:
                layout=index.page(doc,page['number']);geometry_missing+=layout['geometry_missing']
                for region in layout['regions']:assert 0<=region['start']<region['end']<=len(page['content'])
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
        import_seconds=time.perf_counter()-started
        repeated=index.index(doc);assert repeated['cached']==len(pages) and repeated['indexed']==0 and not repeated['errors']
        repeat_job=lib.enqueue(bundle['id'],path,path.name,'book');assert repeat_job['id']==job['id']
        # Simulate an obsolete derived index on one page; keep source and all
        # other page checkpoints. Rebuild only this page.
        selected=min(8,len(pages))
        with lib.db() as db:
            other_before=list(db.execute('SELECT page,text_sha,body FROM pdf_layout WHERE document=? AND page!=? ORDER BY page',(doc,selected)))
            db.execute('UPDATE pdf_layout SET version=0 WHERE document=? AND page=?',(doc,selected))
        resumed=index.index(doc,[selected]);assert resumed['indexed']==1 and not resumed['errors']
        with lib.db() as db:assert list(map(tuple,other_before))==list(map(tuple,db.execute('SELECT page,text_sha,body FROM pdf_layout WHERE document=? AND page!=? ORDER BY page',(doc,selected))))
        table_page=next((p['number'] for p in pages if any(h['kind']=='table' for h in index.page(doc,p['number'])['hints'])),selected)
        for number in set([selected,table_page]):
            with lib.db() as db:material=db.execute('SELECT id FROM materials WHERE document=? AND page=?',(doc,number)).fetchone()[0]
            preview=render_material(lib,material,1000);evidence.append(dict(document=doc,page=number,preview=str(preview.relative_to(ROOT))))
        results.append(dict(file=path.name,sha256=doc,pages=len(pages),characters=texts,summary=summary,import_seconds=round(import_seconds,3),geometry_missing=geometry_missing,cache_reused=len(pages),resumed_single_page=selected,other_checkpoints_unchanged=True,semantic_analysis='not_run_without_paid_consent'))
        print(json.dumps(dict(file=path.name,pages=len(pages),regions=summary['text_regions'],images=summary['image_regions'],seconds=round(import_seconds,2)),ensure_ascii=False),flush=True)
    assert all(digest(Path(p))==sha for p,sha in protected.items())
    assert len(list(lib.files.glob('*.bin')))==2
    report=dict(stage=1,corpus=results,phb=dict(file=phb.name,sha256=protected[str(phb)],analysis='reserved_for_next_stage'),originals_unchanged=True,source_blobs=2,paid_requests=0,model_results='mock_only',qa_evidence_retained=True,rule_coverage=coverage(),previews=evidence)
    (reports/'corpus.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (reports/'coverage.json').write_text(json.dumps(coverage(),ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
