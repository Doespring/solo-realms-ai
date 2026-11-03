def pytest_collection_finish(session):
    from pathlib import Path
    T = Path(r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI')/'solo'/'chronicles.py'
    print('DURING pytest: disk has insert ->', 'sys.path.insert' in T.read_text(encoding='utf-8'))
    import tests.test_copy_does_not_reach_back as m
    print('DURING pytest: sources() n =', len(m.sources()))
    hits=[p.name for p in m.sources() if 'sys.path.insert' in p.read_text(encoding='utf-8',errors='replace')]
    print('DURING pytest: offenders =', hits)
