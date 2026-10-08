"""Exercise the bundled API client against a real, freshly set-up Quartermaster server.

Usage: python scripts/e2e_client.py http://localhost:8098
The server must be new (this script runs /api/setup). Only aiohttp is needed;
Home Assistant isn't imported.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from typing import Any
import uuid

import aiohttp

# Load the api package on its own so the integration's __init__ (and so Home
# Assistant) isn't imported.
_API_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quartermaster" / "api"
_spec = importlib.util.spec_from_file_location(
    "qm_api", _API_DIR / "__init__.py", submodule_search_locations=[str(_API_DIR)]
)
assert _spec is not None
assert _spec.loader is not None
api = importlib.util.module_from_spec(_spec)
sys.modules["qm_api"] = api
_spec.loader.exec_module(api)


def check(cond: bool, what: str) -> None:
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        raise SystemExit(1)


async def post(session: aiohttp.ClientSession, url: str, path: str, token: str | None, body: dict[str, Any]) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.post(f"{url}{path}", json=body, headers=headers) as resp:
        data = await resp.json()
        if resp.status >= 400:
            raise RuntimeError(f"{path}: {resp.status} {data}")
        return data


async def sync(session: aiohttp.ClientSession, url: str, token: str, commands: list[tuple[str, dict[str, Any]]]) -> Any:
    res = await post(
        session,
        url,
        "/api/sync",
        token,
        {
            "protocol": 1,
            "client_version": "0.1.0",
            "device_id": "e2e",
            "epoch": None,
            "cursor": 0,
            "commands": [{"id": str(uuid.uuid4()), "type": t, "at": None, "payload": p} for t, p in commands],
        },
    )
    if res.get("upgrade_required"):
        raise RuntimeError("sync: upgrade_required")
    for result in res.get("results", []):
        if result.get("status") != "applied":
            raise RuntimeError(f"sync rejected: {result}")
    return res


async def main(url: str) -> None:
    async with aiohttp.ClientSession() as session:
        admin = (
            await post(
                session,
                url,
                "/api/setup",
                None,
                {"household": "Maple Street", "name": "Alex", "username": "alex", "password": "correct horse"},
            )
        )["token"]
        ha_token = (await post(session, url, "/api/tokens", admin, {"kind": "ha", "name": "Home Assistant"}))["token"]
        await post(session, url, "/api/users", admin, {"name": "Sam", "username": "sam", "password": "battery staple"})
        sam = (await post(session, url, "/api/login", None, {"username": "sam", "password": "battery staple"}))["token"]
        await sync(session, url, admin, [("store.create", {"id": str(uuid.uuid4()), "name": "Costco"})])

        try:
            await api.QuartermasterClient(session, url, "qm_nope").async_get_items()
            check(False, "bad token rejected")
        except api.QuartermasterAuthError:
            check(True, "bad token -> QuartermasterAuthError")
        try:
            await api.QuartermasterClient(session, url, admin).async_get_items()
            check(False, "device token rejected")
        except api.QuartermasterAuthError:
            check(True, "device token on /api/ha -> QuartermasterAuthError")
        try:
            await api.QuartermasterClient(session, "http://127.0.0.1:1", ha_token).async_get_items()
            check(False, "unreachable server")
        except api.QuartermasterConnectionError:
            check(True, "unreachable server -> QuartermasterConnectionError")

        qm = api.QuartermasterClient(session, url + "/", ha_token)
        status = await qm.async_get_status()
        check(status.household_name == "Maple Street" and not status.setup_required, f"status: {status}")
        check(bool(status.server_id) and status.ha_api == 1, "status reports server_id and ha_api 1")
        check(await qm.async_get_items() == [], "empty list")
        presence = await qm.async_get_presence()
        check(len(presence) == 2 and not any(p.shopping for p in presence), "presence: two members, nobody shopping")

        events: list[Any] = []
        connected = asyncio.Event()

        async def listen() -> None:
            async for event in qm.async_stream_events():
                events.append(event)
                if event.event == "changed":
                    connected.set()

        listener = asyncio.create_task(listen())
        await asyncio.wait_for(connected.wait(), 5)
        check(True, "event stream connected and sent the initial `changed`")

        async with session.post(
            f"{url}/api/trips/arrive", json={"store": "Costco"}, headers={"Authorization": f"Bearer {sam}"}
        ) as resp:
            trip = await resp.json()
        check("trip_id" in trip, "Sam arrives at Costco")
        await asyncio.sleep(0.3)
        check(sum(p.shopping for p in await qm.async_get_presence()) == 1, "presence: one member shopping")

        # Without the dictionary loaded, "milk, eggs and bread" stays one item; add them separately.
        added = await qm.async_add_item("milk", None, api.Via("satellite", "Kitchen"))
        check(len(added.items) == 1, f"add -> {[i.summary for i in added.items]}")
        for text in ("eggs", "bread"):
            await qm.async_add_item(text)
        again = await qm.async_add_item("2 dozen eggs", None, api.Via("user", "Alex"))
        check(again.possible_duplicate, "adding eggs again -> possible_duplicate")
        items = await qm.async_get_items()
        print("     items:", [(i.summary, i.description, str(i.status)) for i in items])

        target = next(i for i in items if i.summary.lower().startswith("milk"))
        check((await qm.async_update_item(target.uid, summary="Oat milk")).summary == "Oat milk", "rename")
        done = await qm.async_update_item(
            target.uid, status=api.ItemStatus.COMPLETED, via=api.Via("satellite", "Kitchen")
        )
        check(done.status is api.ItemStatus.COMPLETED, "check off")
        undone = await qm.async_update_item(target.uid, status=api.ItemStatus.NEEDS_ACTION)
        check(undone.status is api.ItemStatus.NEEDS_ACTION, "uncheck (voids the Home Assistant purchase)")
        await qm.async_delete_item(target.uid)
        check(all(i.uid != target.uid for i in await qm.async_get_items()), "delete an open item cancels it")
        try:
            await qm.async_update_item(target.uid, summary="ghost")
            check(False, "update a removed item")
        except api.QuartermasterNotFoundError:
            check(True, "update a removed item -> QuartermasterNotFoundError")
        try:
            await qm.async_update_item(str(uuid.uuid4()), summary="ghost")
            check(False, "update an unknown item")
        except api.QuartermasterNotFoundError:
            check(True, "update an unknown item -> QuartermasterNotFoundError")

        bread = next(i for i in await qm.async_get_items() if "bread" in i.summary.lower())
        await qm.async_update_item(bread.uid, status=api.ItemStatus.COMPLETED)
        await qm.async_delete_item(bread.uid)
        check(all(i.uid != bread.uid for i in await qm.async_get_items()), "delete a bought item clears it")
        try:
            await qm.async_add_item("   ")
            check(False, "blank add rejected")
        except api.QuartermasterRequestError as err:
            check(err.status == 400, f"blank add -> QuartermasterRequestError 400 ({err.message})")

        await sync(session, url, sam, [("trip.end", {"id": trip["trip_id"]})])
        await asyncio.sleep(0.5)
        listener.cancel()

        kinds = [e.event for e in events]
        print("     events:", kinds)
        check("trip_started" in kinds and "trip_ended" in kinds, "trip_started and trip_ended received")
        check("presence" in kinds, "presence events received")
        requests = [e.data for e in events if e.event == "request_added"]
        check(len(requests) >= 2, f"request_added x{len(requests)}")
        first = requests[0]
        check(first["actor"] == {**first["actor"], "name": "Kitchen", "kind": "satellite"}, f"actor: {first['actor']}")
        check(any(s["name"] == "Sam" and s["store"] == "Costco" for s in first["shoppers"]), "shoppers include Sam")
        by_user = next(r for r in requests if r["actor"]["name"] == "Alex")
        check(by_user["actor"]["kind"] == "ha", "user via -> actor kind ha")
        check(any(r["possible_duplicate"] for r in requests), "request_added carries possible_duplicate")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8098"))
