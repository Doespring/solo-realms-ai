# Build from the project root with: python -m PyInstaller packaging/SoloRealmsAI.spec
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files,collect_submodules,collect_dynamic_libs,copy_metadata
root=Path(SPECPATH).parent
prototype=os.environ.get('SOLO_PACKAGE_PROTOTYPE')=='1'
# Kept for explicit --smoke-test only; ordinary desktop imports none of these.
# Do not collect all scripts or depend on pytest/tests in the shipped runtime.
qa_helpers=['scripts.smoke_fixture','scripts.desktop_cloud_smoke','scripts.desktop_creation_smoke']
nat20_data=[]
for package in ('dnd5e_engine','dnd5e_srd_data'):
    # Runtime verifies the installed source bytes against the pinned manifest.
    nat20_data+=collect_data_files(package,include_py_files=True,excludes=['**/canonical/**'])
for package in ('dnd5e-engine','dnd5e-srd-data','d20','lark-parser','cachetools'):
    nat20_data+=copy_metadata(package)
nat20_data += [(str(root/'vendor/nat20/UPSTREAM.json'),'vendor/nat20'),
               (str(root/'integration/nat20/imported'),'integration/nat20/imported'),
               (str(root/'integration/nat20/playable'),'integration/nat20/playable'),
               (str(root/'examples/lantern-post'),'examples/lantern-post'),
               (str(root/('packaging/prototype.json' if prototype else 'packaging/preview-mock.json')),'.')]
if prototype:
    # One pinned engine, one canonical corpus; the old minimal release stays for replay.
    nat20_data += [(str(root/'vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical'),
                    'vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical'),
                   (str(root/'integration/nat20/releases/prototype-srd-2024-v1'),
                    'integration/nat20/releases/prototype-srd-2024-v1')]
for name in ('LICENSE','LICENSES.md','NOTICE'):
    nat20_data.append((str(root/'vendor/nat20'/name),'assets/licenses/nat20'))
for package in ('dnd5e-engine','dnd5e-srd-data'):
    nat20_data.append((str(root/'vendor/nat20/packages'/package/'LICENSE'),'assets/licenses/nat20/'+package))
a=Analysis([str(root/'desktop_main.py')],pathex=[str(root)],binaries=collect_dynamic_libs('pypdfium2_raw'),
 datas=[(str(root/'web'),'web'),(str(root/'assets'),'assets'),(str(root/'visual_catalog'),'visual_catalog'),(str(root/'prompts'),'prompts')]+nat20_data+collect_data_files('d20')+collect_data_files('lark')+collect_data_files('pypdfium2')+collect_data_files('pypdfium2_raw'),
 hiddenimports=['uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','uvicorn.protocols.websockets.websockets_impl','uvicorn.lifespan.on']+qa_helpers+(['solo.prototype_ui'] if prototype else [])+collect_submodules('dnd5e_engine')+collect_submodules('dnd5e_srd_data'),
 hookspath=[],runtime_hooks=[],excludes=['pytest','tkinter','PyQt5','PyQt6','PySide2','PySide6','torch','numpy','matplotlib','cefpython3'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='SoloRealmsAI',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,icon=str(root/'assets/solo.ico'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SoloRealmsAI-Prototype' if prototype else 'SoloRealmsAI')
