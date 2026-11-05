"""Bound multipart bodies before Starlette can spool an unlimited request to disk."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .adventure_library import MAX_FILE,file_size

class UploadSizeMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        upload=scope['type']=='http' and scope.get('method')=='POST' and path.endswith('/files') and path.startswith(('/api/creation/drafts/','/api/adventure/library/'))
        if not upload:return await self.app(scope,receive,send)
        # Предел и ФАКТИЧЕСКИЙ размер: «слишком большой» без числа игрок не
        # может сопоставить со своим файлом и не знает, насколько резать.
        bound=MAX_FILE+1024*1024;length=dict(scope.get('headers',[])).get(b'content-length')
        limit=file_size(MAX_FILE)
        message='Загрузка превышает предел '+limit+' на файл.'
        if length:
            try:oversized=int(length)>bound
            except ValueError:oversized=True
            if oversized:
                message=('Файл больше предела '+limit+': запрос весит '+file_size(int(length) if length.isdigit() else 0)+
                         '. Оригинал на вашем диске не изменён.')
                return await JSONResponse({'detail':message},status_code=413)(scope,receive,send)
        received=0;exceeded=False;replaced=False
        async def bounded_receive():
            nonlocal received,exceeded,message
            event=await receive()
            if event['type']=='http.request':
                received+=len(event.get('body',b''))
                if received>bound:
                    exceeded=True
                    message='Файл больше предела '+limit+' (принято уже '+file_size(received)+'). Оригинал на вашем диске не изменён.'
                    raise HTTPException(413,message)
            return event
        async def bounded_send(event):
            nonlocal replaced
            # Multipart parsing may wrap the receive exception as a generic 400.
            # Preserve a useful, stable limit response in that case.
            if exceeded and event['type']=='http.response.start':
                replaced=True
                await JSONResponse({'detail':message},status_code=413)(scope,receive,send)
            elif not replaced:await send(event)
        return await self.app(scope,bounded_receive,bounded_send)
