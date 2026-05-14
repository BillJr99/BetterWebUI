import os
from dataclasses import dataclass


@dataclass
class ServiceEndpoint:
    name: str
    base_url: str
    timeout: float = 30.0
    health_path: str = "/api/healthz"


def get_services() -> dict[str, ServiceEndpoint]:
    return {
        "clk": ServiceEndpoint(
            name="CognitiveLoopKernel",
            base_url=os.environ.get("CLK_BASE_URL", "http://localhost:8001"),
            timeout=300.0,  # research loops can be long
            health_path="/api/healthz",
        ),
        "autogui": ServiceEndpoint(
            name="AutoGUI",
            base_url=os.environ.get("AUTOGUI_BASE_URL", "http://localhost:8002"),
            timeout=300.0,
            health_path="/api/healthz",
        ),
        "osso": ServiceEndpoint(
            name="OSScreenObserver",
            base_url=os.environ.get("OSSO_BASE_URL", "http://localhost:5001"),
            timeout=30.0,
            health_path="/api/healthz",
        ),
    }
