"""Run real HTTP services and NGINX; SQLite is a single-machine test profile."""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from zipfile import ZipFile
from pathlib import Path
import httpx
from sqlalchemy import create_engine
from scripts.prepare import ROOT, prepare
from scripts.bootstrap import seed, seed_app

def wait(url,process=None):
    for _ in range(100):
        if process and process.poll() is not None:raise RuntimeError(f'Process exited; see .local/local/*.log: {url}')
        try:
            if httpx.get(url,timeout=1,trust_env=False).status_code==200:return
        except httpx.HTTPError:pass
        time.sleep(.2)
    raise RuntimeError('Readiness timeout: '+url)

def nginx_binary(root):
    if os.name!='nt':
        binary=shutil.which('nginx')
        if not binary:raise RuntimeError('Install NGINX with auth_request support or use Kubernetes')
        return binary
    target=root/'nginx-1.28.0'/'nginx.exe'
    if not target.exists():
        archive=root/'nginx.zip'
        urllib.request.urlretrieve('https://nginx.org/download/nginx-1.28.0.zip',archive)
        with ZipFile(archive) as z:
            for info in z.infolist():
                resolved=(root/info.filename).resolve()
                if not resolved.is_relative_to(root.resolve()):raise RuntimeError('Unsafe ZIP entry')
            z.extractall(root)
    return str(target)

def main(smoke=False):
    os.chdir(ROOT)
    root=ROOT/'.local'/'local';cfg=prepare(root)
    for port in [18000,18080,18081,18082,18083,18443]:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:raise RuntimeError(f'Port {port} is already in use')
    sessions_url='sqlite:///'+str(root/'security.db')
    app_url='sqlite:///'+str(root/'app.db')
    seed(create_engine(sessions_url),cfg);seed_app(create_engine(app_url))
    binary=nginx_binary(root)
    (root/'logs').mkdir(exist_ok=True)
    (root/'client_temp').mkdir(exist_ok=True)
    (root/'proxy_temp').mkdir(exist_ok=True)
    config=(ROOT/'deploy/gateway/nginx.conf').read_text()
    config=config.replace('listen 8080','listen 127.0.0.1:18080').replace('DNS_RESOLVER','127.0.0.1').replace('GATEWAY_SECRET',cfg['gateway_key'])
    config=config.replace('http://security-service.security.svc.cluster.local:8081','http://127.0.0.1:18081')
    config=config.replace('proxy_pass https://$route_host:443/echo','set $local_target https://127.0.0.1:18443;\n      proxy_pass $local_target/echo')
    config=config.replace('pid /tmp/nginx.pid;',f'pid "{(root/"nginx.pid").as_posix()}";')
    config=config.replace('/run/tls/ca.crt',(root/'ca.crt').as_posix()).replace('/tmp/client_temp',(root/'client_temp').as_posix()).replace('/tmp/proxy_temp',(root/'proxy_temp').as_posix())
    config=config.replace('/tmp/',root.as_posix()+'/')
    if os.name=='nt':config=config.replace('access_log /dev/stdout;','access_log logs/access.log;').replace('error_log /dev/stderr warn;','error_log logs/error.log warn;')
    (root/'nginx.conf').write_text(config,encoding='utf-8')
    env=dict(os.environ,SERVICE_MODE='local',SECRET_DIR=str(root),DATABASE_URL=sessions_url,GATEWAY_KEY=cfg['gateway_key'],LOG_KEY=cfg['log_key'],LOG_URL='http://127.0.0.1:18083',SECURITY_URL='http://127.0.0.1:18081',GATEWAY_URL='http://127.0.0.1:18080')
    processes=[];logs=[];flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    try:
        for role,port in [('security_service',18081),('auth_service',18082),('log_service',18083),('app_service',18000),('external_service',18443)]:
            e=dict(env,SERVICE=role,PORT=str(port))
            if role=='app_service':e['DATABASE_URL']=app_url
            args=[sys.executable,'-m','uvicorn',f'services.{role}.main:create_app','--factory','--host','127.0.0.1','--port',str(port)]
            if role=='external_service':args+=['--ssl-certfile',str(root/'tls.crt'),'--ssl-keyfile',str(root/'tls.key')]
            log=open(root/(role+'.log'),'w',encoding='utf-8');logs.append(log)
            processes.append(subprocess.Popen(args,env=e,stdout=log,stderr=subprocess.STDOUT,creationflags=flags))
        for port in [18000,18081,18082,18083]:wait(f'http://127.0.0.1:{port}/healthz')
        log=open(root/'gateway.log','w',encoding='utf-8');logs.append(log)
        args=[binary,'-p',root.as_posix()+'/', '-c',(root/'nginx.conf').as_posix()]
        subprocess.run(args+['-t'],check=True,stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
        nginx=subprocess.Popen(args+['-g','daemon off;'],stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
        wait('http://127.0.0.1:18080/healthz',nginx)
        if smoke:
            from scripts.smoke import verify
            verify(root,cluster=False)
        else:
            print('Running: app :18000, gateway :18080, security :18081, auth :18082, log :18083. Ctrl+C stops only this environment.')
            while True:time.sleep(1)
    finally:
        if 'nginx' in locals():
            subprocess.run(args+['-s','quit'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
            try:nginx.wait(timeout=10)
            except subprocess.TimeoutExpired:nginx.terminate()
        for p in processes:
            p.terminate()
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill()
        for f in logs:f.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--smoke',action='store_true');a=p.parse_args()
    try:main(a.smoke)
    except KeyboardInterrupt:pass
