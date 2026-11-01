import os
import ipaddress
from pathlib import Path
import httpx


class KubernetesIdentity:
    """Resolve the observed peer IP; never accept a caller's service name."""

    def __call__(self, peer_ip):
        ipaddress.ip_address(peer_ip)
        base = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        token = (base / "token").read_text().strip()
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        with httpx.Client(
            verify=str(base / "ca.crt"), timeout=3, trust_env=False
        ) as client:
            response = client.get(
                f"https://{host}:{port}/api/v1/namespaces/app/pods",
                params={
                    "fieldSelector": f"status.podIP={peer_ip}",
                    "labelSelector": "app=app-service",
                },
                headers={"Authorization": "Bearer " + token},
            )
            response.raise_for_status()
        pods = response.json()["items"]
        if len(pods) != 1 or pods[0]["metadata"].get("deletionTimestamp"):
            return None
        return "app-service"
