import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes, serialization

ROOT=Path(__file__).resolve().parents[1]
HOST='external-service.external.svc.cluster.local'

def private(key):
    return key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())

def prepare(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    config=path/'credentials.json'
    if config.exists(): return json.loads(config.read_text())
    data={name:secrets.token_urlsafe(24) for name in ['admin_password','auditor_password','db_root','db_security','db_auth','db_log','db_app','gateway_key','log_key','grafana_password']}
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    (path/'jwt-private.pem').write_bytes(private(key))
    (path/'jwt-public.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'UBI193 local CA')])
    now=datetime.now(timezone.utc)
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(ca_key,hashes.SHA256()))
    tls_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,HOST)])
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(tls_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=90)).add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST),x509.DNSName('localhost')]),critical=False).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).sign(ca_key,hashes.SHA256()))
    (path/'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (path/'tls.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (path/'tls.key').write_bytes(private(tls_key))
    config.write_text(json.dumps(data,indent=2),encoding='utf-8')
    return data

if __name__=='__main__':
    prepare(ROOT/'.local'/'cluster')
    print('Created or reused .local/cluster; no credentials are printed.')
