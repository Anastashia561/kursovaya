from fastapi import FastAPI, Request

def create_app():
    app = FastAPI(title='external-fixture')
    @app.get('/healthz')
    def health(): return {'status':'alive'}
    @app.post('/echo')
    async def echo(request: Request):
        body = await request.body()
        return {'accepted':True, 'bytes':len(body), 'service':'external-fixture'}
    return app
