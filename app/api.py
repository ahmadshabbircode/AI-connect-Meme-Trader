import os, json
from fastapi import FastAPI, Header, HTTPException
from app.scanner import load_db
app = FastAPI(title='AI-connect Meme Trader V5 API', version='5.0.0')

def auth(x_app_token):
    expected = os.getenv('APP_API_TOKEN')
    if expected and x_app_token != expected: raise HTTPException(status_code=401, detail='Invalid app token')

@app.get('/health')
def health(): return {'ok': True, 'service': 'AI-connect Meme Trader V5'}

@app.get('/tokens')
def tokens(min_score: int = 0, x_app_token: str | None = Header(default=None)):
    auth(x_app_token)
    data = list(load_db().values())
    data = [x for x in data if (x.get('scoring') or {}).get('total', 0) >= min_score]
    return sorted(data, key=lambda x: (x.get('scoring') or {}).get('total', 0), reverse=True)
