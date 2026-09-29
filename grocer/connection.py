"""Guarded MCP session to an official platform server.

Both servers can place real orders, so every tool call goes through `GuardedSession`:

- read-only tools are allowed (name looks read-only AND the server marks it read-only);
- a short, explicit list of cart tools is allowed so live bills can be quoted -- and
  Zepto's create_order only as a preview, with confirmOrder hard-checked to be False;
- everything else (checkout, payment, confirm, addresses, coupons, ...) is refused.
"""

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from grocer.auth import oauth_provider


@dataclass(frozen=True)
class ServerSpec:
    platform: str
    url: str
    scope: str | None
    auth_server: str | None = None  # only when the server's own discovery is broken


SERVERS = {
    # tools:write is needed to fill the cart for a live bill quote.
    "zepto": ServerSpec("zepto", "https://mcp.zepto.co.in/mcp", "tools:read tools:write"),
    "instamart": ServerSpec("instamart", "https://mcp.swiggy.com/im", "mcp:tools", "https://mcp.swiggy.com/auth"),
}

# A tool is callable only if it starts with a read verb and contains no write verb.
_READ_VERBS = {"get", "list", "search", "fetch", "view", "show", "find", "track", "your"}
_WRITE_VERBS = {
    "add", "update", "remove", "delete", "clear", "create", "set", "select", "apply",
    "checkout", "confirm", "place", "pay", "cancel", "reorder", "report",
}


class GuardViolation(RuntimeError):
    pass


def _tokens(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if t]


def is_read_only(tool_name: str) -> bool:
    toks = _tokens(tool_name)
    return bool(toks) and toks[0] in _READ_VERBS and not _WRITE_VERBS.intersection(toks)


# Tools that only set which store/address this MCP session prices against. They don't
# touch the cart or orders, but the name check would otherwise block them.
_SESSION_CONTEXT_TOOLS = {"zepto": {"select_saved_address"}}


def _zepto_preview_only(args: dict) -> bool:
    # create_order places a COD order when confirmOrder is true. Only an explicit False,
    # with no tip / wallet / other options, is accepted.
    return args.get("confirmOrder") is False and set(args) <= {"confirmOrder", "userAddressId"}


# Cart tools allowed so a live bill can be quoted: tool -> argument check.
_QUOTE_TOOLS = {
    "zepto": {"update_cart": lambda a: True, "create_order": _zepto_preview_only},
    "instamart": {"update_cart": lambda a: True, "clear_cart": lambda a: not a},
}


class GuardedSession:
    def __init__(self, platform: str, client: Client):
        self.platform = platform
        self._client = client
        self._server_read_only: dict[str, bool] | None = None
        self._in_flight = anyio.CapacityLimiter(MAX_IN_FLIGHT)
        self.retries = 0  # "Too Many Requests" answers seen (shown in the run's timings)

    async def list_tools(self) -> list[Any]:
        return (await self._client.list_tools()).tools

    async def _allowed(self, tool: str, args: dict) -> bool:
        if tool in _SESSION_CONTEXT_TOOLS.get(self.platform, ()):
            return True
        check = _QUOTE_TOOLS.get(self.platform, {}).get(tool)
        if check is not None:
            return check(args)
        if not is_read_only(tool):
            return False
        if self._server_read_only is None:
            self._server_read_only = {
                t.name: t.annotations.read_only_hint for t in await self.list_tools() if t.annotations is not None
            }
        # Where the server annotates a tool, it must also agree that it is read-only.
        return self._server_read_only.get(tool) is not False

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Call an allowed tool and return its payload as parsed JSON (or text)."""
        args = args or {}
        if not await self._allowed(tool, args):
            raise GuardViolation(f"refusing to call {self.platform}.{tool} with {sorted(args)}")
        for wait in RETRY_WAITS:  # the apps rate-limit bursts; a refused request didn't happen, so retry
            async with self._in_flight:
                result = await self._client.call_tool(tool, args)
            if not (result.is_error and _RATE_LIMITED.search(_text(result))):
                break
            self.retries += 1
            await anyio.sleep(wait)  # outside the limiter, so the slot is free while we wait
        if result.is_error:
            raise RuntimeError(f"{self.platform}.{tool} failed: {_text(result)}")
        if result.structured_content is not None:
            return result.structured_content
        text = _text(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


MAX_IN_FLIGHT = 4  # requests open at once to one app -- every call, so no burst can exceed it
RETRY_WAITS = (1, 2, 4, 0)  # seconds to wait after each "Too Many Requests"; last attempt gives up
_RATE_LIMITED = re.compile(r"too many requests|rate.?limit|\b429\b", re.IGNORECASE)


def _text(result: Any) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


@asynccontextmanager
async def connect(platform: str):
    spec = SERVERS[platform]
    auth = oauth_provider(spec.platform, spec.url, spec.scope, spec.auth_server)
    async with httpx2.AsyncClient(auth=auth, timeout=httpx2.Timeout(30, read=120), follow_redirects=True) as http:
        transport = streamable_http_client(spec.url, http_client=http)
        async with Client(transport, mode="legacy", cache=None) as client:
            yield GuardedSession(platform, client)
