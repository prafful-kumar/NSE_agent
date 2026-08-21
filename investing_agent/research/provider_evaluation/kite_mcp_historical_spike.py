from __future__ import annotations

"""One-off capability spike (NOT production code, NOT wired into Phase 6C):
does the official Kite MCP server (https://mcp.kite.trade/mcp) expose a
historical OHLC/candle tool, and does it cover a benchmark index (NIFTY 50)?

Auth: the browser-based OAuth2 + PKCE flow supported natively by the `mcp`
SDK (mcp.client.auth.oauth2.OAuthClientProvider), NOT a manually-copied
ZERODHA_ACCESS_TOKEN. This opens your default browser to kite.zerodha.com's
own login/authorization page; your Zerodha username/password/2FA are only
ever typed into that page, never seen or stored by this script.

The resulting OAuth token pair is persisted to a single local file under
.local/kite_mcp_spike/ (project-root-relative, gitignored, mode 0600, dir
mode 0700) so re-running this script reuses the session instead of
re-registering a fresh OAuth client with Kite on every run -- repeated
fresh registrations in quick succession is the suspected cause of the
ClosedResourceError/session-init flakiness seen during development. Token
contents are never printed or logged.

Run manually: `.venv312/bin/python -m investing_agent.research.provider_evaluation.kite_mcp_historical_spike`
"""

import asyncio
import contextlib
import json
import os
import stat
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

MCP_URL = "https://mcp.kite.trade/mcp"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"

_HISTORICAL_KEYWORDS = ["historical", "candle", "ohlc", "chart"]
_INSTRUMENT_SEARCH_KEYWORDS = ["search_instrument", "instrument"]
_TOOL_CALL_TIMEOUT_S = 25.0

_TOKEN_DIR = Path(__file__).resolve().parents[3] / ".local" / "kite_mcp_spike"
_TOKEN_FILE = _TOKEN_DIR / "session.json"


@dataclass
class FileTokenStorage:
    """Satisfies mcp.client.auth.oauth2.TokenStorage, persisting to a single
    local, gitignored, permission-restricted JSON file so repeated spike
    runs reuse one OAuth session instead of re-registering with Kite every
    time. Never prints token contents; caller is responsible for not
    logging the return values of get_tokens()/get_client_info()."""

    tokens: OAuthToken | None = None
    client_info: OAuthClientInformationFull | None = None

    def __post_init__(self) -> None:
        if _TOKEN_FILE.exists():
            try:
                data = json.loads(_TOKEN_FILE.read_text())
                if data.get("tokens"):
                    self.tokens = OAuthToken.model_validate(data["tokens"])
                if data.get("client_info"):
                    self.client_info = OAuthClientInformationFull.model_validate(data["client_info"])
            except Exception:  # noqa: BLE001 -- corrupt/partial cache, just start fresh
                self.tokens = None
                self.client_info = None

    def _persist(self) -> None:
        _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(_TOKEN_DIR, stat.S_IRWXU)  # 0700, owner-only
        payload = {
            "tokens": self.tokens.model_dump(mode="json") if self.tokens else None,
            "client_info": self.client_info.model_dump(mode="json") if self.client_info else None,
        }
        _TOKEN_FILE.write_text(json.dumps(payload))
        os.chmod(_TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600, owner-only

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens
        self._persist()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info
        self._persist()


@dataclass
class _CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    event: threading.Event = field(default_factory=threading.Event)


def _make_callback_server(result: _CallbackResult) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            parsed = urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            result.code = qs.get("code", [None])[0]
            result.state = qs.get("state", [None])[0]
            result.error = qs.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body>Kite authorization received. You can close this tab "
                b"and return to the terminal.</body></html>"
            )
            result.event.set()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # silence default stderr request logging

    return HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)


async def _redirect_handler(authorization_url: str) -> None:
    print(f"[{time.strftime('%X')}] redirect_handler reached", flush=True)
    print(f"\nOpening browser for Kite authorization:\n  {authorization_url}\n", flush=True)
    print("If it doesn't open automatically, paste that URL into a browser.", flush=True)
    webbrowser.open(authorization_url)


def _run_callback_server(result: _CallbackResult) -> HTTPServer:
    server = _make_callback_server(result)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


async def _callback_handler_factory() -> tuple[callable, HTTPServer]:
    result = _CallbackResult()
    server = _run_callback_server(result)

    async def _callback_handler() -> tuple[str, str | None]:
        print(f"[{time.strftime('%X')}] callback_handler: waiting for browser redirect on "
              f"localhost:{CALLBACK_PORT}{CALLBACK_PATH} ...", flush=True)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, result.event.wait)
        print(f"[{time.strftime('%X')}] callback_handler: redirect received", flush=True)
        server.shutdown()
        if result.error:
            raise RuntimeError(f"Kite authorization denied/error: {result.error}")
        if not result.code:
            raise RuntimeError("No authorization code received on callback.")
        return result.code, result.state

    return _callback_handler, server


def _find_tools(tools: list, keywords: list[str]) -> list:
    return [t for t in tools if any(kw in t.name.lower() for kw in keywords)]


async def main() -> None:
    callback_handler, _server = await _callback_handler_factory()

    client_metadata = OAuthClientMetadata(
        redirect_uris=[f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="investing-agent-kite-mcp-spike",
    )

    auth = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=client_metadata,
        storage=FileTokenStorage(),
        redirect_handler=_redirect_handler,
        callback_handler=callback_handler,
        timeout=300.0,
    )

    connections = {
        "kite": {
            "transport": "streamable_http",
            "url": MCP_URL,
            "auth": auth,
            "timeout": 60,
        }
    }

    client = MultiServerMCPClient(connections)  # type: ignore[arg-type]

    print(f"[{time.strftime('%X')}] MultiServerMCPClient constructed. Calling get_tools() "
          f"(OAuth discovery starts here; browser should open within a few seconds)...", flush=True)
    tools = None
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            print(f"[{time.strftime('%X')}] get_tools() attempt {attempt}/3 ...", flush=True)
            tools = await asyncio.wait_for(client.get_tools(), timeout=90.0)
            break
        except TimeoutError:
            print(f"[{time.strftime('%X')}] TIMEOUT on attempt {attempt} "
                  f"(hung before reaching redirect_handler if that line never printed above).",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"[{time.strftime('%X')}] attempt {attempt} raised: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(3)
    if tools is None:
        print(f"\nAll {attempt} attempts failed. Last error: {last_exc}", flush=True)
        return
    print(f"[{time.strftime('%X')}] get_tools() returned.", flush=True)
    print(f"\n{len(tools)} tools discovered:\n", flush=True)
    by_name = {t.name: t for t in tools}
    for t in tools:
        print(f"  - {t.name}: {t.description}", flush=True)
        try:
            print(f"      args_schema: {t.args_schema}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"      (could not read args_schema: {exc})", flush=True)

    async def _call(tool, args: dict) -> object:
        return await asyncio.wait_for(tool.ainvoke(args), timeout=_TOOL_CALL_TIMEOUT_S)

    # Resolve instrument_token via get_ltp/get_quotes (both accept
    # "EXCHANGE:tradingsymbol" strings and echo back instrument_token in
    # their response payload per the Kite Connect quote response shape) --
    # simplest path that doesn't depend on a dedicated search tool existing.
    token_by_symbol: dict[str, int] = {}
    lookup_tool = by_name.get("get_ltp") or by_name.get("get_quotes") or by_name.get("get_ohlc")
    if lookup_tool is not None:
        for label, instrument in (
            ("BEL", "NSE:BEL"), ("NIFTY 50", "NSE:NIFTY 50"), ("CDSL", "NSE:CDSL"),
        ):
            print(f"\n--- Resolving instrument_token for {instrument} via {lookup_tool.name} ---", flush=True)
            try:
                result = await _call(lookup_tool, {"instruments": [instrument]})
                print(f"  -> {result}", flush=True)
                data = result if isinstance(result, dict) else {}
                inner = data.get("data") if isinstance(data.get("data"), dict) else data
                entry = inner.get(instrument)
                if isinstance(entry, dict) and "instrument_token" in entry:
                    token_by_symbol[label] = entry["instrument_token"]
                    print(f"  resolved instrument_token={entry['instrument_token']}", flush=True)
            except (TimeoutError, Exception) as exc:  # noqa: BLE001
                print(f"  -> FAILED/TIMEOUT: {exc}", flush=True)
    else:
        print("\nNo get_ltp/get_quotes/get_ohlc tool found to resolve instrument_token.", flush=True)

    candidates = _find_tools(tools, _HISTORICAL_KEYWORDS)
    print(f"\nHistorical/candle-keyword tool candidates: {[t.name for t in candidates]}", flush=True)

    if not candidates:
        print("\nNo historical/candle tool found among exposed MCP tools. Stopping here.", flush=True)
        return

    for tool in candidates:
        for label in ("BEL", "NIFTY 50"):
            print(f"\n--- Attempting {tool.name} for {label} / daily / 2025-01-01..2025-03-31 ---", flush=True)
            token = token_by_symbol.get(label)
            if token is None:
                print(f"  skipped: no resolved instrument_token for {label}", flush=True)
                continue
            args = {
                "instrument_token": token,
                "from_date": "2025-01-01 00:00:00",
                "to_date": "2025-03-31 23:59:59",
                "interval": "day",
            }
            try:
                result = await _call(tool, args)
                print(f"  args={args}\n  -> SUCCESS: {result}", flush=True)
            except (TimeoutError, Exception) as exc:  # noqa: BLE001
                print(f"  args={args}\n  -> FAILED/TIMEOUT: {exc}", flush=True)

    # Corporate-action adjusted-vs-raw price check: CDSL had a real,
    # web-verified 1:1 bonus (doubles shares) with record date 2024-08-24
    # (see project memory / Phase 6B corporate_actions seeding). Fetch daily
    # candles spanning that date and compare the pre/post average close --
    # a ~2x drop means raw/unadjusted prices, near-continuity means adjusted.
    cdsl_token = token_by_symbol.get("CDSL")
    adjusted_status = "UNKNOWN"
    if candidates and cdsl_token is not None:
        tool = candidates[0]
        print(f"\n--- Corporate-action check: {tool.name} for CDSL / daily / "
              f"2024-07-24..2024-09-24 (bonus record date 2024-08-24) ---", flush=True)
        args = {
            "instrument_token": cdsl_token,
            "from_date": "2024-07-24 00:00:00",
            "to_date": "2024-09-24 23:59:59",
            "interval": "day",
        }
        try:
            result = await _call(tool, args)
            print(f"  args={args}\n  -> SUCCESS: {result}", flush=True)
            candles = _extract_candles(result)
            if candles:
                adjusted_status = _classify_adjusted_status(candles, "2024-08-24")
            print(f"  ADJUSTED_PRICE_STATUS: {adjusted_status}", flush=True)
        except (TimeoutError, Exception) as exc:  # noqa: BLE001
            print(f"  args={args}\n  -> FAILED/TIMEOUT: {exc}", flush=True)
    else:
        print("\nSkipping corporate-action adjusted-price check: no historical tool "
              "and/or no resolved CDSL instrument_token.", flush=True)
        print("  ADJUSTED_PRICE_STATUS: UNKNOWN (assume RAW/unadjusted for safety)", flush=True)


def _extract_candles(result: object) -> list[list] | None:
    """Kite's historical-data response is normally {"data": {"candles": [[ts, o, h, l, c, v], ...]}}
    (per Kite Connect docs); tolerate a couple of shape variants defensively."""
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict) and isinstance(data.get("candles"), list):
            return data["candles"]
        if isinstance(result.get("candles"), list):
            return result["candles"]
    return None


def _classify_adjusted_status(candles: list[list], record_date: str) -> str:
    """candle[0] is a timestamp string, candle[4] is close. Compares mean
    close in the 5 trading rows before vs after record_date."""
    try:
        before = [c[4] for c in candles if str(c[0]) < record_date]
        after = [c[4] for c in candles if str(c[0]) >= record_date]
        if len(before) < 2 or len(after) < 2:
            return "UNKNOWN (assume RAW/unadjusted for safety) -- too few rows around record date"
        pre_avg = sum(before[-5:]) / len(before[-5:])
        post_avg = sum(after[:5]) / len(after[:5])
        ratio = pre_avg / post_avg if post_avg else 0
        print(f"  pre-record avg close={pre_avg:.2f}  post-record avg close={post_avg:.2f}  ratio={ratio:.2f}",
              flush=True)
        if 1.7 <= ratio <= 2.3:
            return "RAW/UNADJUSTED (price roughly halved across the bonus record date)"
        if 0.85 <= ratio <= 1.15:
            return "ADJUSTED (price continuous across the bonus record date)"
        return f"UNKNOWN (assume RAW/unadjusted for safety) -- ratio={ratio:.2f} not conclusive"
    except Exception as exc:  # noqa: BLE001
        return f"UNKNOWN (assume RAW/unadjusted for safety) -- classification error: {exc}"


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
