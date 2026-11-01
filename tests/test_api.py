import time
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from common.tokens import issue_token, verify_token
from common.models import Rule, Outbox, Event
from services.security_service.main import create_app as security_app
from services.auth_service.main import create_app as auth_app
from services.log_service.main import create_app as log_app
from services.security_service.events import flush_outbox

def headers(cfg):
    return {'X-Gateway-Key': cfg.gateway_key, 'X-Original-URI': '/ext/external-service.external.svc.cluster.local/echo',
            'X-Original-Method': 'POST', 'X-Source-IP': '10.1.1.5', 'X-Body-Size': '10', 'X-Body-Type': 'application/json'}

def test_gateway_identity_required(store, cfg):
    c = TestClient(security_app(cfg, store, lambda ip: 'app-service'))
    assert c.get('/api/v1/check').status_code == 403
    assert c.get('/api/v1/check', headers=headers(cfg)).status_code == 200

@pytest.mark.parametrize('key,value', [('X-Body-Size',''),('X-Body-Size','-1'),('X-Body-Size','0'),
 ('X-Body-Size','1048577'),('X-Original-URI','/ext/user@host/echo'),('X-Original-URI','/ext/a/echo?secret=x'),
 ('X-Original-URI','/ext/127.0.0.1:443/echo'),('X-Original-Method','GET')])
def test_envelope_rejected(store, cfg, key, value):
    c=TestClient(security_app(cfg, store, lambda ip:'app-service'))
    h=headers(cfg);h[key]=value
    assert c.get('/api/v1/check', headers=h).status_code == 403

def test_unknown_source(store,cfg):
    c=TestClient(security_app(cfg,store,lambda ip:None))
    assert c.get('/api/v1/check',headers=headers(cfg)).status_code==403

def test_database_failure_is_closed(store,cfg,monkeypatch):
    from sqlalchemy.exc import OperationalError
    import services.security_service.main as m
    def fail(*a):raise OperationalError('select',{},Exception('down'))
    monkeypatch.setattr(m,'check_request',fail)
    c=TestClient(security_app(cfg,store,lambda ip:'app-service'))
    assert c.get('/api/v1/check',headers=headers(cfg)).status_code==503

def test_login_roles_and_lockout(store,cfg):
    c=TestClient(auth_app(cfg,store))
    a=c.post('/api/v1/token',json={'username':'auditor','password':'test-password'})
    assert a.status_code==200
    token=a.json()['access_token']
    h={'Authorization':'Bearer '+token}
    assert c.put('/api/v1/rules/app-service',headers=h,json={'hosts':[cfg.allowed_host],'types':['application/json'],'max_bytes':100,'max_requests':3,'window_seconds':60,'block_seconds':30}).status_code==403
    for _ in range(5): assert c.post('/api/v1/token',json={'username':'admin','password':'bad'}).status_code==401
    assert c.post('/api/v1/token',json={'username':'admin','password':'test-password'}).status_code==429

def test_jwt_tamper_rejected(cfg):
    from fastapi import HTTPException
    token=issue_token('admin','admin',cfg.key('jwt-private.pem'))
    assert verify_token('Bearer '+token,cfg.key('jwt-public.pem'))['role']=='admin'
    head,body,sig=token.split('.')
    changed=('A' if sig[0]!='A' else 'B')+sig[1:]
    with pytest.raises(HTTPException):verify_token('Bearer '+head+'.'+body+'.'+changed,cfg.key('jwt-public.pem'))

def test_log_idempotency_and_access(store,cfg):
    c=TestClient(log_app(cfg,store))
    event={'id':str(uuid4()),'created':1000,'service':'app-service','host':cfg.allowed_host,'bytes':5,'decision':'DENY','rule':'R2'}
    assert c.post('/api/v1/events',json=event).status_code==403
    assert c.post('/api/v1/events',json=event,headers={'X-Log-Key':cfg.log_key}).json()['stored']
    assert not c.post('/api/v1/events',json=event,headers={'X-Log-Key':cfg.log_key}).json()['stored']
    event['bytes']=6
    assert c.post('/api/v1/events',json=event,headers={'X-Log-Key':cfg.log_key}).status_code==409
    assert c.get('/api/v1/events').status_code==401

def test_outbox_retry(store,cfg):
    c=TestClient(security_app(cfg,store,lambda ip:'app-service'))
    c.get('/api/v1/check',headers=headers(cfg))
    def fail(req):return httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.HTTPStatusError):flush_outbox(store,client,cfg.log_url,cfg.log_key)
    with store() as db:assert db.query(Outbox).count()==1
    log=TestClient(log_app(cfg,store))
    assert flush_outbox(store,log,'',cfg.log_key)==1
    with store() as db:assert db.query(Outbox).count()==0 and db.query(Event).count()==1

def test_metrics_record_allow_and_deny(store,cfg):
    c=TestClient(security_app(cfg,store,lambda ip:'app-service'))
    h=headers(cfg);c.get('/api/v1/check',headers=h)
    h['X-Original-URI']='/ext/attacker.example/echo';c.get('/api/v1/check',headers=h)
    metrics=c.get('/metrics').text
    assert 'egress_requests_total{service="app-service"} 2.0' in metrics
    assert 'rule="R2"' in metrics
