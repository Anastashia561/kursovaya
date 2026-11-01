import argparse
import json
import subprocess
import sys
from pathlib import Path
from scripts.prepare import ROOT, prepare

def run(*args, **kw):
    return subprocess.run(list(args),check=True,text=True,**kw)
def get(*args):return json.loads(run('kubectl',*args,stdout=subprocess.PIPE).stdout)
def apply(data):run('kubectl','apply','-f','-',input=json.dumps(data))
def secret(ns,name,data):apply({'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':ns},'type':'Opaque','stringData':data})
def database_url(role,password,host,db):return f'postgresql+psycopg://{role}:{password}@{host}:5432/{db}'

def deploy(provider='minikube',profile='ubi193',build=True):
    import os
    os.chdir(ROOT)
    cfg=prepare(ROOT/'.local'/'cluster');path=ROOT/'.local'/'cluster'
    if build:
        if provider=='minikube':run('minikube','-p',profile,'image','build','-t','ubi193:dev','.')
        else:
            run('docker','build','-t','ubi193:dev','.')
            run('kind','load','docker-image','ubi193:dev','--name',profile)
    run('kubectl','apply','-f','deploy/k8s/00-namespaces.yaml')
    root_url=database_url('postgres',cfg['db_root'],'security-db.security.svc.cluster.local','security')
    app_url=database_url('postgres',cfg['db_app'],'app-db.app.svc.cluster.local','application')
    secret('security','service-config',{
      'DB_PASSWORD':cfg['db_root'],'BOOTSTRAP_DATABASE_URL':root_url,
      'SECURITY_DATABASE_URL':database_url('svc_security',cfg['db_security'],'security-db.security.svc.cluster.local','security'),
      'AUTH_DATABASE_URL':database_url('svc_auth',cfg['db_auth'],'security-db.security.svc.cluster.local','security'),
      'LOG_DATABASE_URL':database_url('svc_log',cfg['db_log'],'security-db.security.svc.cluster.local','security'),
      'APP_DATABASE_URL':app_url,'GATEWAY_KEY':cfg['gateway_key'],'LOG_KEY':cfg['log_key'],
      'ADMIN_PASSWORD':cfg['admin_password'],'AUDITOR_PASSWORD':cfg['auditor_password'],
      'SVC_SECURITY_PASSWORD':cfg['db_security'],'SVC_AUTH_PASSWORD':cfg['db_auth'],'SVC_LOG_PASSWORD':cfg['db_log']})
    secret('app','service-config',{'DB_PASSWORD':cfg['db_app'],'APP_DATABASE_URL':app_url})
    secret('monitoring','service-config',{'GRAFANA_PASSWORD':cfg['grafana_password']})
    public={'jwt-public.pem':(path/'jwt-public.pem').read_text()}
    secret('security','jwt-public',public)
    secret('security','jwt-auth',dict(public,**{'jwt-private.pem':(path/'jwt-private.pem').read_text()}))
    secret('external','external-tls',{name:(path/name).read_text() for name in ['tls.crt','tls.key']})
    dns=get('-n','kube-system','get','service','kube-dns','-o','json')['spec']['clusterIP']
    config=(ROOT/'deploy'/'gateway'/'nginx.conf').read_text().replace('DNS_RESOLVER',dns).replace('GATEWAY_SECRET',cfg['gateway_key']).replace('/run/tls/ca.crt','/run/gateway/ca.crt')
    secret('security','gateway-config',{'nginx.conf':config,'ca.crt':(path/'ca.crt').read_text()})
    run('kubectl','apply','-f','deploy/k8s/15-rbac.yaml','-f','deploy/k8s/40-network-policies.yaml')
    api=get('get','service','kubernetes','-o','json')['spec']['clusterIP']
    endpoints=get('get','endpoints','kubernetes','-o','json')['subsets']
    to=[{'ipBlock':{'cidr':api+'/32'}}]
    ports={443}
    for subset in endpoints:
        to += [{'ipBlock':{'cidr':x['ip']+'/32'}} for x in subset['addresses']]
        ports.update(x['port'] for x in subset['ports'])
    for ns,name in [('security','security-service'),('monitoring','prometheus')]:
        apply({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'allow-apiserver-'+name,'namespace':ns},'spec':{'podSelector':{'matchLabels':{'app':name}},'policyTypes':['Egress'],'egress':[{'to':to,'ports':[{'protocol':'TCP','port':p} for p in sorted(ports)]}]}})
    run('kubectl','apply','-f','deploy/k8s/10-databases.yaml')
    for ns,name in [('security','security-db'),('app','app-db')]:run('kubectl','-n',ns,'rollout','status','statefulset/'+name,'--timeout=300s')
    run('kubectl','-n','security','delete','job','bootstrap','--ignore-not-found')
    run('kubectl','apply','-f','deploy/k8s/20-bootstrap.yaml')
    run('kubectl','-n','security','wait','--for=condition=complete','job/bootstrap','--timeout=300s')
    for file in ['30-microservices.yaml','31-gateway.yaml','32-external-fixture.yaml','50-monitoring.yaml']:run('kubectl','apply','-f','deploy/k8s/'+file)
    for ns,name in [('app','app-service'),('security','security-service'),('security','auth-service'),('security','log-service'),('security','gateway-service'),('external','external-service'),('monitoring','prometheus'),('monitoring','grafana')]:run('kubectl','-n',ns,'rollout','status','deployment/'+name,'--timeout=300s')
    print('All workloads are ready. Run: python -m scripts.smoke --cluster')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--provider',choices=['minikube','kind'],default='minikube');p.add_argument('--profile',default='ubi193');p.add_argument('--no-build',action='store_true');a=p.parse_args()
    deploy(a.provider,a.profile,not a.no_build)
