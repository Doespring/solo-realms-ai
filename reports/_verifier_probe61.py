
import os, sys, json
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
import solo.nat20_content as nc
print('ROOT       =', nc.ROOT)
print('VENDOR     =', nc.VENDOR, 'exists=', nc.VENDOR.exists())
import dnd5e_engine
print('dnd5e_engine.__file__ =', dnd5e_engine.__file__)
print('dnd5e_engine.__path__ =', list(getattr(dnd5e_engine, '__path__', [])))
print()
print('=== is it a plain sys.path entry (real package) or a .pth/vendor dir? ===')
import importlib.util
spec = importlib.util.find_spec('dnd5e_engine')
print('spec.origin =', spec.origin)
print('spec.submodule_search_locations =', list(spec.submodule_search_locations or []))
print()
print('=== PYTHONPATH ===')
print(os.environ.get('PYTHONPATH'))
