"""One bounded upload path shared by preparation and the source library."""
import asyncio,uuid
from .adventure_library import MAX_FILE,file_size

async def receive_source(library,jobs,bundle,file,purpose='book',request=None):
    library.get(bundle)
    temporary=library.files/('.upload-'+uuid.uuid4().hex+'.part');size=0
    try:
        with temporary.open('wb') as stream:
            while chunk:=await file.read(1024*1024):
                if request is not None and await request.is_disconnected():raise asyncio.CancelledError()
                size+=len(chunk)
                if size>MAX_FILE:raise ValueError('Файл больше предела '+file_size(MAX_FILE)+' (принято уже '+file_size(size)+'). Оригинал на вашем диске не изменён.')
                await asyncio.to_thread(stream.write,chunk)
        job=await asyncio.to_thread(library.enqueue,bundle,temporary,file.filename or 'source',purpose)
        jobs.submit(job['id'])
        return job
    finally:
        await file.close()
        if temporary.exists():temporary.unlink()
