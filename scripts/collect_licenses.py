"""Copy runtime redistribution notices from installed package metadata into the release assets."""
from importlib.metadata import distribution
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1]
PACKAGES=['fastapi','starlette','pydantic','pydantic_core','httpx','httpcore','anyio','certifi','idna','h11','uvicorn','click','colorama','typing_extensions','typing-inspection','annotated-types','annotated-doc','opentelemetry-api','psutil','pywebview','pythonnet','clr_loader','bottle','proxy_tools','cffi','pycparser']
def main():
    PACKAGES.extend(['pypdf','pypdfium2','pillow'])
    target=ROOT/'assets/licenses';target.mkdir(parents=True,exist_ok=True);manifest=[]
    for name in PACKAGES:
        package=distribution(name);copied=[];folder=target/name;folder.mkdir(exist_ok=True)
        for item in package.files or []:
            if '.dist-info/' not in str(item).replace('\\','/'):continue
            relative=str(item).replace('\\','/')
            if '/licenses/' in relative or any(word in item.name.lower() for word in ('license','licence','copying','notice','copyright')):
                path=Path(package.locate_file(item))
                if path.is_file() and path.stat().st_size<1000000:
                    destination=folder/(relative.split('.dist-info/',1)[1] if '/licenses/' in relative else item.name)
                    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(path.read_bytes());copied.append(destination.relative_to(folder).as_posix())
        if not copied:(folder/'METADATA.txt').write_text(str(package.metadata),encoding='utf-8')
        manifest.append(dict(name=name,version=package.version,license=package.metadata.get('License-Expression',package.metadata.get('License','See package metadata')),files=copied or ['METADATA.txt']))
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():(target/'Python-LICENSE.txt').write_bytes(python_license.read_bytes())
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
