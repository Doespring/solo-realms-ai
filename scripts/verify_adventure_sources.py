"""Import user-supplied books into a private QA profile, report counts without source text."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from solo.adventure_library import AdventureLibrary,ImportJobs

NAMES=['Hoard of the Dragon Queen RUS.pdf','The Rise of Tiamat RUS.pdf','Out of the Abyss RUS.pdf','Icewind Dale - Rime of the Frostmaiden RUS.pdf','Врата Балдура_ Нисхождение в Авернус.pdf']

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',default='data/qa-adventure');parser.add_argument('--source-dir',default=r'C:\Users\iwa41\Downloads');parser.add_argument('--report',default='reports/adventure/real-source-import.json');args=parser.parse_args()
    library=AdventureLibrary(args.data_dir);jobs=ImportJobs(library);work=[]
    try:
        for name in NAMES:
            source=Path(args.source_dir)/name
            if not source.exists():continue
            existing=next((b for b in library.list() if b['metadata'].get('validation_source')==name),None)
            bundle=existing or library.create(source.stem,{'validation_source':name,'rights':'user_private_materials','edition':'2014','prepared':False})
            job=library.enqueue(bundle['id'],source,name,'book');jobs.submit(job['id']);work.append((name,bundle['id'],job['id']))
            if name.startswith('Icewind'):
                archive=Path(args.source_dir)/'Icewind Dale - Rime of the Frostmaiden RUS Maps.zip'
                if archive.exists():
                    map_job=library.enqueue(bundle['id'],archive,archive.name,'maps');jobs.submit(map_job['id']);work.append(('Icewind maps',bundle['id'],map_job['id']))
        while any(library.job(job)['status'] in ('queued','running') for _,_,job in work):
            print(json.dumps([{'source':i+1,**{k:library.job(job)[k] for k in ('status','done','total')}} for i,(_,_,job) in enumerate(work)]),flush=True)
            time.sleep(10)
        report=[]
        for name,bundle,job in work:
            review=library.review(bundle);record=library.job(job)
            result={'source':name,'status':record['status'],'bundle_status':review['bundle']['status'],'documents':len(review['documents']),'pdf_pages':sum(d['pages'] for d in review['documents'] if d['format']=='.pdf'),'candidates':len(review['candidates']),'prepared':False}
            if name=='Icewind maps':
                maps=[m for m in review['materials'] if m['kind']=='map']
                result.update(pdf_map_pages=len(maps),rendered_previews=sum((library.files/(m['document']+'-p'+str(m['page'])+'-w480.png')).is_file() for m in maps),all_maps_gm_only=all(m['scope']=='gm' for m in maps))
            report.append(result)
        target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps({'sources':report,'inference_calls':0,'commercial_materials_excluded_from_release':True,'adventurers_league_pdf_provided':False},ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if any(r['status']!='complete' for r in report):raise SystemExit('Some imports did not complete; inspect private QA job status')
    finally:jobs.close()

if __name__=='__main__':main()
