"""Small, explicitly selected raster previews. No remote URLs or whole books."""
import base64,io,json

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
            url=part.get('image_url',{}).get('url','')
            if not url.startswith('data:image/png;base64,') or len(url)>150000:raise ValueError('Разрешены только небольшие локальные PNG')
            try:
                raw=base64.b64decode(url.split(',',1)[1],validate=True)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format!='PNG' or max(image.size)>384:raise ValueError('Изображение превышает 384 px')
                    image.verify()
            except Exception as e:raise ValueError('Неверное изображение') from e
            count+=1
        else:raise ValueError('Неподдержанная часть пакета')
    if count>2:raise ValueError('Не более двух изображений на запрос')
    return count

def serialized(user):return user if isinstance(user,str) else json.dumps(user,ensure_ascii=False,separators=(',',':'))

# The request validator accepts at most this many characters, so the renderer
# must stay under it. A dense scan can exceed it at 384 px, and shrinking is
# preferable to sending a page the validator will reject.
MAX_URL=150000

def png_part(image):
    from PIL import Image
    image=image.convert('RGB');image.thumbnail((384,384),Image.Resampling.LANCZOS)
    while True:
        buffer=io.BytesIO();image.save(buffer,format='PNG',optimize=True)
        url='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')
        if len(url)<=MAX_URL or max(image.size)<=64:break
        image=image.resize((max(64,image.width*3//4),max(64,image.height*3//4)),Image.Resampling.LANCZOS)
    return dict(type='image_url',image_url=dict(url=url,detail='low'))

def render_selected(library,document,pages):
    from PIL import Image
    with library.db() as db:row=db.execute('SELECT format FROM documents WHERE id=?',(document,)).fetchone()
    if not row:raise ValueError('Источник отсутствует')
    if len(pages)>2:raise ValueError('Не более двух страниц с изображениями')
    result=[];path=library.blob(document)
    if row['format'].lower()=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as doc:
            for number in pages:
                if number>len(doc):raise ValueError('Страница отсутствует')
                page=doc[number-1]
                try:
                    bitmap=page.render(scale=min(1,384/max(page.get_size())))
                    try:part=png_part(bitmap.to_pil())
                    finally:bitmap.close()
                finally:page.close()
                result.extend([dict(type='text',text='Изображение физической страницы '+str(number)),part])
    elif row['format'].lower() in ('.png','.jpg','.jpeg','.webp'):
        if pages!=[1]:raise ValueError('У растрового источника только страница 1')
        with Image.open(path) as image:part=png_part(image)
        result=[dict(type='text',text='Изображение физической страницы 1'),part]
    else:raise ValueError('Изображения доступны только для PDF и растровых источников')
    image_count(result);return result
