"""Small, explicitly selected raster previews. No remote URLs or whole books."""
import base64,io,json,sqlite3

# A page or an illustration is a preview: the model needs to recognise it, not
# read it. A map is different - room numbers and names are the content, and at
# 384 px they are gone, so a map is rendered at MAP_SIDE and paid for with
# palette depth instead of pixels.
PAGE_SIDE=384
MAP_SIDE=1024
# What the model should get for a map: a real scan costs ~1.2 MB as a full-colour
# PNG at 1280 px, so the pixels above MAP_SIDE are bought with a palette (16, then
# 8, then 4 colours) and only then with size. A printed map drawn in flat vector
# colours keeps every colour, a watercolour scan reaches 1280 in four.
VISION_SIDE=1280
# The review screen shows a map at 2048, and the model's picture is cut from the
# same render: a window of it then arrives at full density instead of a shrunken
# whole, which is what makes a label inside the map readable.
MAP_RENDER_SIDE=2048
SIDE_STEP=64
PALETTES=(16,8,4)
PALETTE_BITS={16:4,8:3,4:2}
# The request validator and the provider accept at most this many characters in
# one data: URL, so the renderer measures the encoded URL before sending.
MAX_URL=150000
# Bumped when the pixels a model gets for a map change, so an old analysis is not
# reused for a picture it never saw. Only map caches carry it.
VISION_VERSION='map-vision-1280-v1'

def image_count(user):
    if isinstance(user,str):return 0
    if not isinstance(user,list) or len(user)>8:raise ValueError('Неверный мультимодальный пакет')
    count=0
    from PIL import Image
    for part in user:
        if not isinstance(part,dict):raise ValueError('Неверная часть пакета')
        if part.get('type')=='text':
            if not isinstance(part.get('text'),str):raise ValueError('Неверный текст пакета')
        elif part.get('type')=='image_url':
            image_url=part.get('image_url',{})
            url=image_url.get('url','')
            if not url.startswith('data:image/png;base64,') or len(url)>MAX_URL:raise ValueError('Разрешены только небольшие локальные PNG')
            limit=VISION_SIDE if image_url.get('detail')=='high' else PAGE_SIDE
            try:
                raw=base64.b64decode(url.split(',',1)[1],validate=True)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format!='PNG' or max(image.size)>limit:raise ValueError('Изображение превышает '+str(limit)+' px')
                    image.verify()
            except Exception as e:raise ValueError('Неверное изображение') from e
            count+=1
        else:raise ValueError('Неподдержанная часть пакета')
    if count>2:raise ValueError('Не более двух изображений на запрос')
    return count

def serialized(user):return user if isinstance(user,str) else json.dumps(user,ensure_ascii=False,separators=(',',':'))

def _encode(image,bits=None):
    buffer=io.BytesIO();image.save(buffer,format='PNG',optimize=True,**({'bits':bits} if bits else {}))
    return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')

def png_part(image,max_side=PAGE_SIDE):
    """One PNG part under MAX_URL, and a map keeps its pixels as long as it can.

    A preview is shrunk by 3/4 steps until it fits. A map is first cut to a
    16-colour, 4-bit palette at the requested size, because a scanned map is
    bytes-heavy yet has few real colours: giving up shades costs the reader far
    less than giving up sharpness. Resolution is only reduced when even the
    palette does not fit.
    """
    from PIL import Image
    image=image.convert('RGB')
    image.thumbnail((max_side,max_side),Image.Resampling.LANCZOS)
    detail='high' if max_side>PAGE_SIDE else 'low'
    while True:
        url=_encode(image)
        if len(url)<=MAX_URL:break
        if detail=='high':
            url=_encode(image.quantize(colors=16,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE),bits=4)
            if len(url)<=MAX_URL:break
        if max(image.size)<=64:break
        image=image.resize((max(64,image.width*3//4),max(64,image.height*3//4)),Image.Resampling.LANCZOS)
    return dict(type='image_url',image_url=dict(url=url,detail=detail))

def _palette_url(image,colors):
    """The same picture in `colors` colours, or None when it is still too heavy."""
    from PIL import Image
    small=image.quantize(colors=colors,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
    url=_encode(small,bits=PALETTE_BITS[colors])
    return url if len(url)<=MAX_URL else None

def _heavy(image):
    """The encoded URL of one picture at the deepest palette that still fits."""
    for colors in PALETTES:
        url=_palette_url(image,colors)
        if url:return url,colors
    return None,0

def vision_whole(image,side=VISION_SIDE,floor=MAP_SIDE):
    """A map for the model: as large as the limit allows, colours given up first.

    A watercolour scan does not fit 150 000 characters as a 1280 px PNG, and a
    shrunken whole map is exactly the picture whose labels cannot be read. So the
    palette is tried before the size, and the size steps down in SIDE_STEP pixels
    only when even four colours are too heavy. Returns the part, its long side and
    the number of colours it kept.
    """
    from PIL import Image
    image=image.convert('RGB').copy()
    image.thumbnail((side,side),Image.Resampling.LANCZOS)
    while True:
        url,colors=_heavy(image)
        if url:return dict(type='image_url',image_url=dict(url=url,detail='high')),max(image.size),colors
        longest=max(image.size)
        if longest<=floor:break
        factor=(longest-SIDE_STEP)/longest
        image=image.resize((max(1,round(image.width*factor)),max(1,round(image.height*factor))),Image.Resampling.LANCZOS)
    # Four colours at MAP_SIDE still does not fit: this is noise, not a drawing,
    # and a picture that cannot be sent is worse than a small one.
    return png_part(image,floor),max(image.size),0

def _densest(image,cells=8):
    """Where the picture has the most ink: the keyed part of a map, not its sea."""
    from PIL import Image,ImageFilter
    edges=image.convert('L').filter(ImageFilter.FIND_EDGES)
    rows=max(1,round(cells*edges.height/max(1,edges.width)))
    small=edges.resize((cells,rows),Image.Resampling.BOX)
    pixels=small.tobytes()
    best=max(range(len(pixels)),key=lambda index:pixels[index])
    return (best%cells+0.5)/cells,(best//cells+0.5)/rows

def map_window(image,side=VISION_SIDE,focus=None):
    """A window of a map at full render density, not a shrunken whole map.

    `focus` is the point the window is centred on, in 0..1 of the picture: the
    caller that knows where the hero stands passes it. Without one the window is
    put where the drawing has the most ink, because a map is read while the module
    is prepared, when no hero stands anywhere yet.
    """
    image=image.convert('RGB')
    width=min(side,image.width);height=min(side,image.height)
    if width==image.width and height==image.height:return image.copy()
    centre=_densest(image) if focus is None else (min(max(focus[0],0),1),min(max(focus[1],0),1))
    left=min(max(round(centre[0]*image.width-width/2),0),image.width-width)
    top=min(max(round(centre[1]*image.height-height/2),0),image.height-height)
    return image.crop((left,top,left+width,top+height))

def vision_parts(image,extra=False,focus=None):
    """The parts a map sends: the whole picture and, when it pays, a window.

    One picture keeps the layout of the map; a window at full density keeps the
    labels readable. A request carries at most two pictures, so the window is
    added only when the caller has no second page to spend the slot on (`extra`).
    """
    whole,longest,colors=vision_whole(image)
    parts=[whole]
    if extra and longest<VISION_SIDE:
        part,side,kept=vision_whole(map_window(image,VISION_SIDE,focus))
        if side>=MAP_SIDE:parts.append(part)
    return parts

def map_labels(library,material,limit=600):
    """The names a map carries, as text: compression never touches a list.

    The printed text inside the material's own box comes first (the labels the
    book set on the map), then the words a person used to describe the picture
    when it was marked up. A map drawn as a picture has no text layer at all -
    then only the markup answers, and an empty string means nothing is known.
    """
    words=[]
    try:
        printed=library.material_text(material)
        if printed.get('mode')=='box':words.append(' '.join(str(printed.get('text') or '').split()))
    except (ValueError,sqlite3.Error,KeyError):pass
    try:
        from .material_markup import searchable
        words.append(searchable(library.material(material)))
    except (ValueError,ImportError,KeyError):pass
    text=' · '.join(word for word in words if word)[:limit]
    return 'Подписи и места на карте: '+text if text else ''

def map_materials(library,document,pages):
    """The printed map cut out of each requested page, by page number."""
    if not pages:return {}
    with library.db() as db:
        rows=db.execute('SELECT id,page FROM materials WHERE document=? AND kind=\'map\' AND page IN ('+','.join('?'*len(pages))+') ORDER BY id',(document,*pages)).fetchall()
    found={}
    for row in rows:found.setdefault(row['page'],row['id'])
    return found

def render_selected(library,document,pages,kind='page',focus=None):
    """Selected pages as parts; kind='map' renders the map itself, larger.

    A map is rendered from its own printed box at the density the review screen
    shows (MAP_RENDER_SIDE), because the page also carries columns of text that
    only waste pixels. What the model gets is decided by `vision_parts`: the whole
    map as large as the provider's limit allows and - when one page leaves the
    second picture slot free - a window of it at full density where the drawing is
    densest. `focus` centres that window on a known point (0..1 of the picture).
    If the book was never indexed for materials, the whole page is used instead.
    """
    from PIL import Image
    with library.db() as db:row=db.execute('SELECT format FROM documents WHERE id=?',(document,)).fetchone()
    if not row:raise ValueError('Источник отсутствует')
    if len(pages)>2:raise ValueError('Не более двух страниц с изображениями')
    maps=map_materials(library,document,pages) if kind=='map' else {}
    # Two pictures per request is the ceiling, so a window is affordable only when
    # no second page is asking for the slot.
    extra=kind=='map' and len(pages)==1
    side=MAP_RENDER_SIDE if kind=='map' else PAGE_SIDE
    label='Изображение карты на физической странице ' if kind=='map' else 'Изображение физической страницы '
    def picture(image):
        return vision_parts(image,extra=extra,focus=focus) if kind=='map' else [png_part(image,PAGE_SIDE)]
    def described(number,material,seen):
        result.extend([dict(type='text',text=label+str(number)),*seen])
        labels=map_labels(library,material) if material else ''
        if labels:result.append(dict(type='text',text=labels))
    result=[];path=library.blob(document)
    if row['format'].lower()=='.pdf':
        from .adventure_worker import render_material
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as doc:
            for number in pages:
                if number>len(doc):raise ValueError('Страница отсутствует')
                material=maps.get(number)
                if material:
                    with Image.open(render_material(library,material,side)) as image:seen=picture(image.convert('RGB'))
                else:
                    page=doc[number-1]
                    try:
                        # Scale 1 renders a page at its printed points; a map needs
                        # more pixels than the page has points, so only a preview is
                        # capped at 1.
                        scale=min(1,side/max(page.get_size())) if kind!='map' else side/max(page.get_size())
                        bitmap=page.render(scale=scale)
                        try:seen=picture(bitmap.to_pil())
                        finally:bitmap.close()
                    finally:page.close()
                described(number,material,seen)
    elif row['format'].lower() in ('.png','.jpg','.jpeg','.webp'):
        if pages!=[1]:raise ValueError('У растрового источника только страница 1')
        with Image.open(path) as image:seen=picture(image)
        described(1,None,seen)
    else:raise ValueError('Изображения доступны только для PDF и растровых источников')
    image_count(result);return result
