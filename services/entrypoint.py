import importlib
import os

def create_app():
    role = os.environ["SERVICE"]
    if role not in {"security_service", "auth_service", "log_service", "app_service", "external_service"}:
        raise RuntimeError("Unknown SERVICE")
    return importlib.import_module(f"services.{role}.main").create_app()
