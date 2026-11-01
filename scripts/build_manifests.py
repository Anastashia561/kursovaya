"""Generate reviewable non-secret YAML; credentials are created at deployment."""
import json
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'deploy'/'k8s'
def write(name,documents):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(yaml.safe_dump_all(documents,sort_keys=False,allow_unicode=True),encoding='utf-8')
def meta(name,namespace): return {'name':name,'namespace':namespace}
def service(name,ns,port,target=None,headless=False):
    spec={'selector':{'app':name},'ports':[{'name':'http' if port!=5432 else 'db','port':port,'targetPort':target or port}]}
    if headless:spec['clusterIP']='None'
    return {'apiVersion':'v1','kind':'Service','metadata':meta(name,ns),'spec':spec}
def secret_env(name,key,secret='service-config'):
    return {'name':name,'valueFrom':{'secretKeyRef':{'name':secret,'key':key}}}
def env(name,value):return {'name':name,'value':str(value)}
def mount_secret(name,path):return {'name':name,'mountPath':path,'readOnly':True}
def deploy(name,ns,port,role,replicas=1,extras=None,volumes=None,mounts=None,sa=None):
    c={'name':name,'image':'ubi193:dev','imagePullPolicy':'IfNotPresent','ports':[{'containerPort':port}],
       'env':[env('SERVICE',role),env('PORT',port)],
       'resources':{'requests':{'cpu':'50m','memory':'96Mi'},'limits':{'cpu':'500m','memory':'256Mi'}},
       'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
       'volumeMounts':[{'name':'tmp','mountPath':'/tmp'}]+(mounts or [])}
    c['env']+=extras or []
    for probe in ['startupProbe','readinessProbe','livenessProbe']:
        c[probe]={'httpGet':{'path':'/readyz' if probe=='readinessProbe' and role!='external_service' else '/healthz','port':port},'periodSeconds':5,'failureThreshold':30 if probe=='startupProbe' else 3}
    spec={'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'runAsGroup':10001,'fsGroup':10001,'seccompProfile':{'type':'RuntimeDefault'}},
          'automountServiceAccountToken':bool(sa),'containers':[c], 'volumes':[{'name':'tmp','emptyDir':{}}]+(volumes or [])}
    if sa:spec['serviceAccountName']=sa
    return {'apiVersion':'apps/v1','kind':'Deployment','metadata':meta(name,ns),
            'spec':{'replicas':replicas,'selector':{'matchLabels':{'app':name}},'template':{'metadata':{'labels':{'app':name}},'spec':spec}}}
def database(name,ns,db):
    return {'apiVersion':'apps/v1','kind':'StatefulSet','metadata':meta(name,ns),'spec':{'serviceName':name,'replicas':1,
     'selector':{'matchLabels':{'app':name}},'template':{'metadata':{'labels':{'app':name}},'spec':{'automountServiceAccountToken':False,
     'containers':[{'name':'postgres','image':'postgres:16.8-bookworm','env':[env('POSTGRES_DB',db),env('POSTGRES_USER','postgres'),secret_env('POSTGRES_PASSWORD','DB_PASSWORD')],
     'ports':[{'containerPort':5432}],'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'512Mi'}},
     'volumeMounts':[{'name':'data','mountPath':'/var/lib/postgresql/data'}],
     'readinessProbe':{'exec':{'command':['pg_isready','-U','postgres','-d',db]},'initialDelaySeconds':5,'periodSeconds':5}}]}},
     'volumeClaimTemplates':[{'metadata':{'name':'data'},'spec':{'accessModes':['ReadWriteOnce'],'resources':{'requests':{'storage':'1Gi'}}}}]}}
def peer(ns,app):return {'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':ns}},'podSelector':{'matchLabels':{'app':app}}}
def policy(name,ns,app,direction,entries):
    return {'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':meta(name,ns),
            'spec':{'podSelector':{'matchLabels':{'app':app}},'policyTypes':[direction],direction.lower():entries}}
def connection(ns1,app1,ns2,app2,port):
    ports=[{'protocol':'TCP','port':port}]
    return [policy(f'egress-{app1}-{app2}',ns1,app1,'Egress',[{'to':[peer(ns2,app2)],'ports':ports}]),
            policy(f'ingress-{app1}-{app2}',ns2,app2,'Ingress',[{'from':[peer(ns1,app1)],'ports':ports}])]

def main():
    namespaces=['app','security','monitoring','external']
    write('00-namespaces.yaml',[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':n}} for n in namespaces])
    write('10-databases.yaml',[service('security-db','security',5432,headless=True),database('security-db','security','security'),service('app-db','app',5432,headless=True),database('app-db','app','application')])
    roles=[{'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('security-service','security')},
      {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':meta('identify-source','app'),'rules':[{'apiGroups':[''],'resources':['pods'],'verbs':['list']}]},
      {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':meta('identify-source','app'),'subjects':[{'kind':'ServiceAccount','name':'security-service','namespace':'security'}],'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'identify-source'}},
      {'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('prometheus','monitoring')},
      {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':meta('scrape-pods','security'),'rules':[{'apiGroups':[''],'resources':['pods'],'verbs':['get','list','watch']}]},
      {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':meta('scrape-pods','security'),'subjects':[{'kind':'ServiceAccount','name':'prometheus','namespace':'monitoring'}],'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'scrape-pods'}}]
    write('15-rbac.yaml',roles)
    public=[{'name':'jwt-public','secret':{'secretName':'jwt-public'}}]
    publicmount=[mount_secret('jwt-public','/run/secrets')]
    docs=[]
    configs=[('security-service','security',8081,'security_service',2,'SECURITY_DATABASE_URL'),('auth-service','security',8082,'auth_service',1,'AUTH_DATABASE_URL'),('log-service','security',8083,'log_service',1,'LOG_DATABASE_URL'),('app-service','app',8000,'app_service',1,'APP_DATABASE_URL')]
    for name,ns,port,role,reps,dbkey in configs:
        extra=[secret_env('DATABASE_URL',dbkey)]
        vol=public if ns=='security' else []
        mounts=publicmount if ns=='security' else []
        if role=='security_service':extra += [secret_env('GATEWAY_KEY','GATEWAY_KEY'),secret_env('LOG_KEY','LOG_KEY')]
        if role=='log_service':extra += [secret_env('LOG_KEY','LOG_KEY')]
        if role=='auth_service':vol=[{'name':'jwt-public','secret':{'secretName':'jwt-auth'}}]
        docs += [service(name,ns,port),deploy(name,ns,port,role,reps,extra,vol,mounts,'security-service' if role=='security_service' else None)]
    write('30-microservices.yaml',docs)
    gateway=deploy('gateway-service','security',8080,'gateway_service',2,[],[{'name':'gateway-config','secret':{'secretName':'gateway-config'}}],[mount_secret('gateway-config','/run/gateway')])
    g=gateway['spec']['template']['spec']['containers'][0]
    g.update({'image':'nginx:1.28.0-bookworm','command':['nginx','-c','/run/gateway/nginx.conf','-g','daemon off;'],'env':[]})
    for probe in ['startupProbe','livenessProbe','readinessProbe']:g[probe]['httpGet']['path']='/healthz'
    write('31-gateway.yaml',[service('gateway-service','security',8080),gateway])
    ext=deploy('external-service','external',8443,'external_service',1,[],[{'name':'tls','secret':{'secretName':'external-tls'}}],[mount_secret('tls','/run/tls')])
    ext['spec']['template']['spec']['containers'][0]['command']=['uvicorn','services.external_service.main:create_app','--factory','--host','0.0.0.0','--port','8443','--ssl-certfile','/run/tls/tls.crt','--ssl-keyfile','/run/tls/tls.key']
    for probe in ['startupProbe','livenessProbe','readinessProbe']:ext['spec']['template']['spec']['containers'][0][probe]['httpGet']['scheme']='HTTPS'
    write('32-external-fixture.yaml',[service('external-service','external',443,8443),ext])
    bootstrap=deploy('bootstrap','security',8081,'security_service',1,[secret_env('BOOTSTRAP_DATABASE_URL','BOOTSTRAP_DATABASE_URL'),secret_env('APP_DATABASE_URL','APP_DATABASE_URL'),secret_env('ADMIN_PASSWORD','ADMIN_PASSWORD'),secret_env('AUDITOR_PASSWORD','AUDITOR_PASSWORD'),secret_env('SVC_SECURITY_PASSWORD','SVC_SECURITY_PASSWORD'),secret_env('SVC_AUTH_PASSWORD','SVC_AUTH_PASSWORD'),secret_env('SVC_LOG_PASSWORD','SVC_LOG_PASSWORD')])
    job={'apiVersion':'batch/v1','kind':'Job','metadata':meta('bootstrap','security'),'spec':{'backoffLimit':3,'template':bootstrap['spec']['template']}}
    pod=job['spec']['template']['spec'];pod['restartPolicy']='OnFailure';pod['containers'][0]['command']=['python','-m','scripts.bootstrap']
    for p in ['startupProbe','readinessProbe','livenessProbe']:pod['containers'][0].pop(p)
    write('20-bootstrap.yaml',[job])
    policies=[]
    for ns in namespaces:
        policies.append({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':meta('default-deny',ns),'spec':{'podSelector':{},'policyTypes':['Ingress','Egress']}})
        policies.append({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':meta('allow-dns',ns),'spec':{'podSelector':{},'policyTypes':['Egress'],'egress':[{'to':[peer('kube-system','kube-dns')],'ports':[{'protocol':p,'port':53} for p in ['TCP','UDP']]}]}})
        # CoreDNS is normally selected by k8s-app, not app.
        policies[-1]['spec']['egress'][0]['to'][0]['podSelector']={'matchLabels':{'k8s-app':'kube-dns'}}
    for link in [('app','app-service','security','gateway-service',8080),('app','app-service','app','app-db',5432),('security','gateway-service','security','security-service',8081),('security','security-service','security','security-db',5432),('security','auth-service','security','security-db',5432),('security','log-service','security','security-db',5432),('security','security-service','security','log-service',8083),('security','auth-service','security','security-service',8081),('security','gateway-service','external','external-service',8443),('monitoring','prometheus','security','security-service',8081),('monitoring','grafana','monitoring','prometheus',9090),('security','bootstrap','security','security-db',5432),('security','bootstrap','app','app-db',5432)]:
        policies+=connection(*link)
    # Both service and pod ports are listed for CNI implementations applying
    # egress policy before or after Service destination NAT.
    for p in policies:
        if p['metadata']['name']=='egress-gateway-service-external-service':p['spec']['egress'][0]['ports'].append({'protocol':'TCP','port':443})
    write('40-network-policies.yaml',policies)
    prometheus={'global':{'scrape_interval':'5s','evaluation_interval':'5s'},'rule_files':['/etc/prometheus/alerts.yml'],
      'scrape_configs':[{'job_name':'security-pods','kubernetes_sd_configs':[{'role':'pod','namespaces':{'names':['security']}}],
       'relabel_configs':[{'source_labels':['__meta_kubernetes_pod_label_app'],'action':'keep','regex':'security-service'},
                         {'source_labels':['__meta_kubernetes_pod_container_port_number'],'action':'keep','regex':'8081'}]}]}
    alerts={'groups':[{'name':'egress','rules':[{'alert':'EgressDenied','expr':'sum(increase(egress_blocked_total[1m])) > 0','for':'0s','labels':{'severity':'warning'},'annotations':{'summary':'Outgoing request denied by R1–R5'}}]}]}
    datasource={'apiVersion':1,'datasources':[{'name':'Prometheus','uid':'prometheus','type':'prometheus','url':'http://prometheus.monitoring.svc.cluster.local:9090','access':'proxy','isDefault':True}]}
    provider={'apiVersion':1,'providers':[{'name':'ubi193','type':'file','options':{'path':'/var/lib/grafana/dashboards'}}]}
    dashboard={'uid':'ubi193','title':'UBI193 Egress','schemaVersion':39,'version':1,'refresh':'5s','panels':[]}
    for i,(title,expr) in enumerate([('Requests','sum(rate(egress_requests_total[1m]))'),('Denied by rule','sum by(rule)(increase(egress_blocked_total[1m]))'),('Reserved body bytes','sum(rate(egress_bytes_total[1m]))'),('Check p95','histogram_quantile(0.95,sum by(le)(rate(egress_check_seconds_bucket[1m])))')]):
        dashboard['panels'].append({'id':i+1,'type':'timeseries','title':title,'datasource':{'type':'prometheus','uid':'prometheus'},'gridPos':{'x':(i%2)*12,'y':(i//2)*8,'w':12,'h':8},'targets':[{'refId':'A','expr':expr}]})
    (ROOT/'deploy'/'monitoring').mkdir(exist_ok=True)
    for file,data in [('prometheus.yml',prometheus),('alerts.yml',alerts),('datasources.yml',datasource),('dashboards.yml',provider)]:
        (ROOT/'deploy'/'monitoring'/file).write_text(yaml.safe_dump(data,sort_keys=False),encoding='utf-8')
    (ROOT/'deploy'/'monitoring'/'dashboard.json').write_text(json.dumps(dashboard,indent=2),encoding='utf-8')
    cm={'apiVersion':'v1','kind':'ConfigMap','metadata':meta('monitoring-config','monitoring'),'data':{file:(ROOT/'deploy'/'monitoring'/file).read_text(encoding='utf-8') for file in ['prometheus.yml','alerts.yml','datasources.yml','dashboards.yml','dashboard.json']}}
    prom=deploy('prometheus','monitoring',9090,'prometheus',1,[],[{'name':'config','configMap':{'name':'monitoring-config'}}],[{'name':'config','mountPath':'/etc/prometheus'},{'name':'tmp','mountPath':'/prometheus'}],'prometheus')
    pc=prom['spec']['template']['spec']['containers'][0];pc.update(image='prom/prometheus:v3.2.1',command=['/bin/prometheus','--config.file=/etc/prometheus/prometheus.yml','--storage.tsdb.path=/prometheus'],env=[])
    for probe in ['startupProbe','livenessProbe','readinessProbe']:pc[probe]['httpGet']['path']='/-/ready'
    graf=deploy('grafana','monitoring',3000,'grafana',1,[env('GF_SECURITY_ADMIN_USER','admin'),secret_env('GF_SECURITY_ADMIN_PASSWORD','GRAFANA_PASSWORD')],[{'name':'config','configMap':{'name':'monitoring-config'}}],[])
    gc=graf['spec']['template']['spec']['containers'][0];gc.update(image='grafana/grafana:11.6.0',command=['/run.sh'])
    gc['env'] += [env('GF_LOG_MODE','console'),env('GF_PATHS_LOGS','/var/lib/grafana/logs')]
    gc['volumeMounts']=[{'name':'tmp','mountPath':'/var/lib/grafana'}, {'name':'tmp','mountPath':'/tmp'}, {'name':'config','mountPath':'/etc/grafana/provisioning/datasources/default.yml','subPath':'datasources.yml'},{'name':'config','mountPath':'/etc/grafana/provisioning/dashboards/default.yml','subPath':'dashboards.yml'},{'name':'config','mountPath':'/var/lib/grafana/dashboards/ubi193.json','subPath':'dashboard.json'}]
    for probe in ['startupProbe','livenessProbe','readinessProbe']:gc[probe]['httpGet']['path']='/api/health'
    write('50-monitoring.yaml',[cm,service('prometheus','monitoring',9090),prom,service('grafana','monitoring',3000),graf])
    print('Generated non-secret Kubernetes and monitoring configuration.')

if __name__=='__main__':main()
