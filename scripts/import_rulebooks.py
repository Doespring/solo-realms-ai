"""Explicit local ingestion; no model calls and no edits to campaign databases."""
import os,sys,argparse,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ['SOLO_TEST']='1'
from solo.rule_knowledge import RuleKnowledge

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);parser.add_argument('--user-books',action='store_true');parser.add_argument('--srd-dir',type=Path);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    knowledge=RuleKnowledge(Path(args.data_dir)/'rules-knowledge.sqlite3');results=[]
    sources=[]
    if args.user_books:
        folder=Path(os.environ['USERPROFILE'])/'Downloads'
        sources += [(folder/"Player's Handbook 2024 RUS.pdf",'Player’s Handbook 2024 RUS','dnd2024','phb','user_private',False),(folder/"Dungeon Master's Guide 2024 RUS.pdf",'Dungeon Master’s Guide 2024 RUS (неполный)','dnd2024','dmg','user_private',True)]
    if args.srd_dir:
        sources += [(args.srd_dir/'SRD-5.1.pdf','SRD 5.1','dnd2014','srd','CC-BY-4.0',False),(args.srd_dir/'SRD-5.2.1.pdf','SRD 5.2.1','dnd2024','srd','CC-BY-4.0',False)]
    for file,title,edition,kind,license,partial in sources:
        if not file.exists():results.append(dict(title=title,missing=True));continue
        started=time.monotonic();value=knowledge.import_pdf(file,title=title,edition=edition,kind=kind,license=license,partial=partial);value.pop('path',None);value['seconds']=round(time.monotonic()-started,2);results.append(value)
        print(json.dumps(dict(title=title,pages=value['pages'],partial=partial,seconds=value['seconds'],reused=value['reused']),ensure_ascii=False),flush=True)
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(dict(sources=results,catalog=knowledge.catalog(),model_calls=0,closed_book_text_distributed=False),ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()
