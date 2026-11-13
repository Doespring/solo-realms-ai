# Build from the project root with: python -m PyInstaller packaging/SoloRealmsAI.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files,collect_submodules,collect_dynamic_libs
root=Path(SPECPATH).parent
a=Analysis([str(root/'desktop_main.py')],pathex=[str(root)],binaries=collect_dynamic_libs('pypdfium2_raw'),
 datas=[(str(root/'web'),'web'),(str(root/'assets'),'assets'),(str(root/'visual_catalog'),'visual_catalog'),(str(root/'prompts'),'prompts')]+collect_data_files('pypdfium2')+collect_data_files('pypdfium2_raw'),
 hiddenimports=['uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','uvicorn.protocols.websockets.websockets_impl','uvicorn.lifespan.on','scripts.smoke_fixture','scripts.desktop_cloud_smoke','scripts.desktop_creation_smoke'],
 hookspath=[],runtime_hooks=[],excludes=['pytest','tkinter','PyQt5','PyQt6','PySide2','PySide6','torch','numpy','matplotlib','cefpython3'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='SoloRealmsAI',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,icon=str(root/'assets/solo.ico'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SoloRealmsAI')
