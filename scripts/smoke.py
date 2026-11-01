import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
import httpx
from scripts.prepare import ROOT, HOST


def verify(root, cluster=False):
    credentials = json.loads((Path(root) / "credentials.json").read_text())
    checks = []
    completed = False

    def check(name, actual, expected):
        checks.append(
            {
                "name": name,
                "actual": actual,
                "expected": expected,
                "passed": actual == expected,
            }
        )
        print(name + ": " + str(actual))
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected}, got {actual}")

    try:
        with httpx.Client(timeout=10, trust_env=False) as c:
            app = "http://127.0.0.1:18000"
            auth = "http://127.0.0.1:18082"
            log = "http://127.0.0.1:18083"
            security = "http://127.0.0.1:18081"
            check(
                "app database",
                c.get(app + "/api/v1/records").status_code,
                200,
            )
            tokens = {}
            for role in ["admin", "auditor"]:
                r = c.post(
                    auth + "/api/v1/token",
                    json={
                        "username": role,
                        "password": credentials[role + "_password"],
                    },
                )
                check("login " + role, r.status_code, 200)
                tokens[role] = r.json()["access_token"]

            def send(**data):
                return c.post(
                    app + "/api/v1/send",
                    json={"data": {"message": "synthetic hello"}, **data},
                )

            time.sleep(
                11
            )  # Previous runs may have consumed the shared window.
            r = send()
            check("ALLOW including TLS upstream", r.status_code, 200)
            check("external received body", r.json()["bytes"] > 0, True)
            check(
                "R2 forbidden destination",
                send(host="attacker.example").status_code,
                403,
            )
            check(
                "R5 forbidden declared type",
                send(media="application/octet-stream").status_code,
                403,
            )
            check(
                "R3 byte budget",
                send(data={"payload": "x" * 5000}).status_code,
                403,
            )
            check("R1 temporary source block", send().status_code, 403)
            time.sleep(11)
            for i in range(3):
                check(
                    "request quota allowed " + str(i + 1),
                    send().status_code,
                    200,
                )
            check("R4 request budget", send().status_code, 403)
            policy = {
                "hosts": [HOST],
                "types": ["application/json"],
                "max_bytes": 4096,
                "max_requests": 3,
                "window_seconds": 10,
                "block_seconds": 5,
            }
            check(
                "auditor cannot change rules",
                c.put(
                    auth + "/api/v1/rules/app-service",
                    json=policy,
                    headers={"Authorization": "Bearer " + tokens["auditor"]},
                ).status_code,
                403,
            )
            check(
                "admin may update rules",
                c.put(
                    auth + "/api/v1/rules/app-service",
                    json=policy,
                    headers={"Authorization": "Bearer " + tokens["admin"]},
                ).status_code,
                200,
            )
            seen = set()
            for _ in range(30):
                r = c.get(
                    log + "/api/v1/events",
                    headers={"Authorization": "Bearer " + tokens["auditor"]},
                )
                r.raise_for_status()
                seen = {x["rule"] for x in r.json()}
                if {"ALLOW", "R1", "R2", "R3", "R4", "R5"} <= seen:
                    break
                time.sleep(0.5)
            check(
                "persistent events for R1-R5",
                {"ALLOW", "R1", "R2", "R3", "R4", "R5"} <= seen,
                True,
            )
            metrics = c.get(security + "/metrics")
            check("Prometheus metrics", metrics.status_code, 200)
            check(
                "blocked counter exported",
                "egress_blocked_total" in metrics.text,
                True,
            )
            if cluster:
                code = """import socket,httpx
socket.getaddrinfo("external-service.external.svc.cluster.local",443)
try:
 r=httpx.post("https://external-service.external.svc.cluster.local/echo",json={"data":"synthetic"},verify=False,timeout=3,trust_env=False)
 print("BYPASS",r.status_code)
 raise SystemExit(1)
except (httpx.ConnectTimeout,httpx.ConnectError):
 print("BLOCKED")
"""
                result = subprocess.run(
                    [
                        "kubectl",
                        "-n",
                        "app",
                        "exec",
                        "deployment/app-service",
                        "--",
                        "python",
                        "-c",
                        code,
                    ],
                    capture_output=True,
                    text=True,
                )
                check(
                    "NetworkPolicy denies direct app egress",
                    result.returncode == 0 and "BLOCKED" in result.stdout,
                    True,
                )
                prom = c.get("http://127.0.0.1:19090/api/v1/targets").json()
                targets = [
                    x
                    for x in prom["data"]["activeTargets"]
                    if x["labels"].get("job") == "security-pods"
                ]
                for _ in range(20):
                    if len(targets) == 2 and all(
                        x["health"] == "up" for x in targets
                    ):
                        break
                    time.sleep(1)
                    targets = c.get(
                        "http://127.0.0.1:19090/api/v1/targets"
                    ).json()["data"]["activeTargets"]
                check(
                    "both security replicas scraped",
                    len(targets) == 2
                    and all(x["health"] == "up" for x in targets),
                    True,
                )
            completed = True
    finally:
        out = ROOT / "test-results"
        out.mkdir(exist_ok=True)
        (
            out / ("cluster-smoke.json" if cluster else "local-smoke.json")
        ).write_text(
            json.dumps(
                {
                    "profile": (
                        "Kubernetes/PostgreSQL/NGINX/Calico"
                        if cluster
                        else "Windows or Linux/SQLite/NGINX/HTTP/TLS"
                    ),
                    "checks": checks,
                    "passed": completed
                    and bool(checks)
                    and all(x["passed"] for x in checks),
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def cluster():
    processes = []
    files = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        for ns, name, local, remote in [
            ("app", "app-service", 18000, 8000),
            ("security", "auth-service", 18082, 8082),
            ("security", "log-service", 18083, 8083),
            ("security", "security-service", 18081, 8081),
            ("monitoring", "prometheus", 19090, 9090),
        ]:
            (ROOT / "test-results").mkdir(exist_ok=True)
            f = open(
                ROOT / "test-results" / ("forward-" + name + ".log"), "w"
            )
            files.append(f)
            processes.append(
                subprocess.Popen(
                    [
                        "kubectl",
                        "-n",
                        ns,
                        "port-forward",
                        "--address",
                        "127.0.0.1",
                        "service/" + name,
                        f"{local}:{remote}",
                    ],
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            )
        from scripts.run_local import wait

        for port in [18000, 18081, 18082, 18083]:
            wait(f"http://127.0.0.1:{port}/healthz")
        wait("http://127.0.0.1:19090/-/ready")
        verify(ROOT / ".local" / "cluster", True)
    finally:
        for p in processes:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in files:
            f.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cluster", action="store_true", required=True)
    p.parse_args()
    cluster()
