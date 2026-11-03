"""Immutable visual registry derived from the supplied source catalog; never campaign storage."""
import json,sqlite3
from pathlib import Path
from contextlib import closing

def registry(root):
    root=Path(root);manifest=json.loads((root/'web/visual/manifest.json').read_text(encoding='utf-8'))
    with closing(sqlite3.connect((root/'visual_catalog/assets.sqlite3').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.row_factory=sqlite3.Row;reviews=[dict(r) for r in db.execute('SELECT * FROM source_reviews ORDER BY source_id')]
    return dict(assets=manifest['assets'],catalog_sha256=manifest['catalog_sha256'],reviews=reviews,source_count=31,attribution='/static/visual/ATTRIBUTION.md')

def register_visual_api(app,root):
    @app.get('/api/visual-assets')
    def visual_assets():return registry(root)
