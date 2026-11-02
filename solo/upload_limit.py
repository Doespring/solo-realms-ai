"""Bound multipart bodies before Starlette can spool an unlimited request to disk."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .adventure_library import MAX_FILE

class UploadSizeMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        upload=scope['type']=='http' and scope.get('method')=='POST' and path.endswith('/files') and path.startswith(('/api/creation/drafts/','/api/adventure/library/'))
        if not upload:return await self.app(scope,receive,send)
        bound=MAX_FILE+1024*1024;length=dict(scope.get('headers',[])).get(b'content-length');message='Загрузка превышает предел 384 МиБ на файл.'
        if length:
            try:oversized=int(length)>bound
            except ValueError:oversized=True
            if oversized:return await JSONResponse({'detail':message},status_code=413)(scope,receive,send)
        received=0;exceeded=False;replaced=False
        async def bounded_receive():
            nonlocal received,exceeded
            event=await receive()
            if event['type']=='http.request':
                received+=len(event.get('body',b''))
                if received>bound:exceeded=True;raise HTTPException(413,message)
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
