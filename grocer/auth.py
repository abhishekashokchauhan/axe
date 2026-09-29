"""OAuth for the official MCP servers: browser login (phone + OTP) and on-disk tokens.

Tokens live in ~/.grocer/<platform>.json (mode 0600), outside the project, so they
are never committed. Delete that file to log out.
"""

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anyio
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

CALLBACK_PORT = 8765
# Zepto's registration endpoint accepts localhost but rejects 127.0.0.1 redirects.
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
TOKEN_DIR = Path(os.environ.get("GROCER_HOME", Path.home() / ".grocer"))


class FileTokenStorage:
    def __init__(self, platform: str, scope: str | None = None):
        self.path = TOKEN_DIR / f"{platform}.json"
        self.scope = scope

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def _save(self, data: dict) -> None:
        TOKEN_DIR.mkdir(mode=0o700, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._load()
        t = data.get("tokens")
        if not t:
            return None
        # A login made while asking for fewer scopes than we now need (e.g. the read-only
        # Zepto login from before cart quotes) is treated as absent, so the next run logs in
        # again. Compare with what we *requested*: Zepto reports granting only "tools:read"
        # even when it honours tools:write, so the granted scope can't be trusted.
        requested = data.get("requested_scope", t.get("scope"))
        if self.scope and not set(self.scope.split()) <= set((requested or "").split()):
            return None
        return OAuthToken.model_validate(t)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        data["requested_scope"] = self.scope
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        c = self._load().get("client")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)


async def _open_browser(url: str) -> None:
    print(f"\nOpening browser to log in. If it does not open, visit:\n  {url}\n")
    webbrowser.open(url)


def _wait_for_code(timeout: float = 300) -> AuthorizationCodeResult:
    """Serve one request on the loopback redirect URI and return its query params."""
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
            ok = "code" in result
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Logged in. You can close this tab." if ok else f"Login failed: {result}"
            self.wfile.write(f"<p>{msg}</p>".encode())
            done.set()

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", CALLBACK_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if not done.wait(timeout):
            raise TimeoutError("Timed out waiting for the login redirect")
    finally:
        server.shutdown()
        server.server_close()
    if "code" not in result:
        raise RuntimeError(f"Authorization failed: {result}")
    return AuthorizationCodeResult(code=result["code"], state=result.get("state"), iss=result.get("iss"))


async def _callback() -> AuthorizationCodeResult:
    return await anyio.to_thread.run_sync(_wait_for_code)


def oauth_provider(platform: str, server_url: str, scope: str | None, auth_server: str | None = None) -> OAuthClientProvider:
    metadata = OAuthClientMetadata(
        client_name="grocer (personal price comparison)",
        redirect_uris=[REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=scope,
    )
    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=FileTokenStorage(platform, scope),
        redirect_handler=_open_browser,
        callback_handler=_callback,
    )
    if auth_server:
        # Swiggy's 401 points at a resource-metadata URL that serves an HTML 404, so the SDK
        # falls back to the bare origin as issuer, which mismatches the "/auth" issuer in its
        # metadata. Presetting the authorization server makes discovery use the RFC 8414 path
        # (/.well-known/oauth-authorization-server/auth), whose issuer matches.
        provider.context.auth_server_url = auth_server
    return provider
