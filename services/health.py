import asyncio
from .clients import get_clk_client, get_autogui_client, get_osso_client
from . import state as svc_state


async def check_all_services() -> dict:
    results = {}
    clients = {
        "clk": get_clk_client(),
        "autogui": get_autogui_client(),
        "osso": get_osso_client(),
    }

    async def check(name, client):
        if not svc_state.is_enabled(name):
            results[name] = {"ok": True, "enabled": False}
            return
        try:
            h = await client.health()
            results[name] = {"ok": True, "enabled": True, "detail": h}
        except Exception as e:
            results[name] = {"ok": False, "enabled": True, "error": str(e)}

    await asyncio.gather(*[check(n, c) for n, c in clients.items()])
    return results
