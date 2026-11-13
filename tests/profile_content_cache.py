"""One source-only Stage3 options timing, no host/server/model calls."""
import argparse
import cProfile
import json
import os
from pathlib import Path
import pstats
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.profile.is_absolute():parser.error('Explicit absolute synthetic profile required')
    os.environ['SOLO_TEST']='1';os.environ['SOLO_DATA_DIR']=str(args.profile)
    start=time.perf_counter()
    from solo.prototype_ui import creation_options
    from solo import content_verification_cache as cache
    imports=(time.perf_counter()-start)*1000
    count=[0];original=cache._sha_file
    def counted(path):count[0]+=1;return original(path)
    cache._sha_file=counted
    if hasattr(creation_options,'cache_clear'):creation_options.cache_clear()
    profiler=cProfile.Profile();profiler.enable();start=time.perf_counter()
    first=creation_options();cold=(time.perf_counter()-start)*1000;profiler.disable()
    first_hashes=count[0]
    if hasattr(creation_options,'cache_clear'):creation_options.cache_clear()
    start=time.perf_counter();second=creation_options();repeated=(time.perf_counter()-start)*1000
    assert first==second
    report=dict(import_ms=round(imports,2),options_first_profiled_ms=round(cold,2),
        options_repeated_without_ui_lru_ms=round(repeated,2),sha_files_first=first_hashes,
        sha_files_repeated=count[0]-first_hashes,profile=str(args.profile),paid_calls=0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    profiler.dump_stats(str(args.output.with_suffix('.prof')))
    print(json.dumps(report))
    pstats.Stats(profiler).strip_dirs().sort_stats('cumulative').print_stats(10)


if __name__=='__main__':main()
