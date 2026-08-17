from __future__ import annotations

"""ZerodhaKiteMCPReader — live PortfolioReader via Kite MCP.

Uses langchain-mcp-adapters to connect to Zerodha's official Kite MCP server.
All operations are read-only.  Write/order tools are never called.

Authentication:
  ZERODHA_ACCESS_TOKEN is required.  The token expires daily at midnight IST.
  Regenerate it from kite.zerodha.com or via the login flow.

MCP tool discovery:
  On first use, tools are listed from the server and mapped to internal methods.
  Unknown tool names are logged as warnings; the method raises BrokerUnavailableError.

References:
  https://zerodha.com/products/mcp/
  https://kite.trade/docs/connect/v3/

Phase 2: implements get_holdings() and get_positions().
  get_margins() and get_quotes() are stubbed — add in Phase 3 if needed.
"""

import json
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from investing_agent.config.logging import get_logger
from investing_agent.config.settings import Settings
from investing_agent.gateway.portfolio_reader import (
    BrokerAuthError,
    BrokerRateLimitError,
    BrokerTimeoutError,
    BrokerUnavailableError,
    PortfolioReader,
)

log = get_logger(__name__)

# Known Kite MCP tool name patterns (case-insensitive search)
_HOLDINGS_KEYWORDS = ["holdings"]
_POSITIONS_KEYWORDS = ["positions"]
_MARGINS_KEYWORDS = ["margins"]
_QUOTE_KEYWORDS = ["quote", "ltp"]

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


def _find_tool(tools: list[Any], keywords: list[str]) -> Any | None:
    for tool in tools:
        name_lower = tool.name.lower()
        if any(kw in name_lower for kw in keywords):
            return tool
    return None


def _extract_data(result: Any) -> Any:
    """Extract the data payload from an MCP tool result.

    MCP returns a ToolMessage or string — normalise to Python dict/list.
    """
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"raw": result}
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list) and content:
            raw = content[0]
            if hasattr(raw, "text"):
                return json.loads(raw.text) if isinstance(raw.text, str) else raw.text
        if isinstance(content, str):
            return json.loads(content)
    return result


class ZerodhaKiteMCPReader(PortfolioReader):
    """Live PortfolioReader backed by Kite MCP.

    Instantiate with settings; the MCP connection is opened per-call
    using an async context manager (stateless, no persistent connection).
    """

    def __init__(self, settings: Settings) -> None:
        if not _MCP_AVAILABLE:
            raise ImportError("langchain-mcp-adapters is required.")
        if not settings.zerodha_access_token:
            raise BrokerAuthError(
                "ZERODHA_ACCESS_TOKEN is not set. "
                "Generate it from kite.zerodha.com."
            )
        self._mcp_url = settings.zerodha_mcp_url
        self._access_token = settings.zerodha_access_token.get_secret_value()

    def _mcp_config(self) -> dict[str, Any]:
        return {
            "kite": {
                "transport": "streamable_http",
                "url": self._mcp_url,
                "headers": {"Authorization": f"Bearer {self._access_token}"},
                "timeout": 30,
            }
        }

    def _handle_mcp_error(self, exc: Exception) -> None:
        msg = str(exc).lower()
        if "401" in msg or "403" in msg or "unauthorized" in msg or "token" in msg:
            raise BrokerAuthError(
                "Zerodha access token is invalid or expired. "
                "Regenerate at kite.zerodha.com."
            ) from exc
        if "429" in msg or "rate" in msg:
            raise BrokerRateLimitError("Kite API rate limit reached.") from exc
        if "timeout" in msg or "timed out" in msg:
            raise BrokerTimeoutError("Kite MCP request timed out.") from exc
        raise BrokerUnavailableError(
            f"Kite MCP unreachable: {exc}"
        ) from exc

    @retry(
        retry=retry_if_exception_type(BrokerUnavailableError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_holdings(self) -> dict[str, Any]:
        try:
            async with MultiServerMCPClient(self._mcp_config()) as client:
                tools = client.get_tools()
                log.info("kite_mcp.tools_discovered", count=len(tools),
                         names=[t.name for t in tools])

                tool = _find_tool(tools, _HOLDINGS_KEYWORDS)
                if tool is None:
                    raise BrokerUnavailableError(
                        f"No holdings tool found. Available: {[t.name for t in tools]}"
                    )

                log.info("kite_mcp.calling_tool", tool=tool.name, operation="get_holdings")
                result = await tool.ainvoke({})
                data = _extract_data(result)
                log.info("kite_mcp.holdings_received",
                         count=len(data.get("data", data) if isinstance(data, dict) else data))
                return data

        except (BrokerAuthError, BrokerRateLimitError, BrokerTimeoutError):
            raise
        except Exception as exc:
            self._handle_mcp_error(exc)
            raise  # unreachable, satisfies type checker

    @retry(
        retry=retry_if_exception_type(BrokerUnavailableError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_positions(self) -> dict[str, Any]:
        try:
            async with MultiServerMCPClient(self._mcp_config()) as client:
                tools = client.get_tools()
                tool = _find_tool(tools, _POSITIONS_KEYWORDS)
                if tool is None:
                    raise BrokerUnavailableError("No positions tool found.")
                result = await tool.ainvoke({})
                return _extract_data(result)
        except (BrokerAuthError, BrokerRateLimitError, BrokerTimeoutError):
            raise
        except Exception as exc:
            self._handle_mcp_error(exc)
            raise

    async def get_margins(self) -> dict[str, Any]:
        # Phase 2: stub — implement if margin monitoring is needed
        log.warning("kite_mcp.get_margins_not_implemented")
        return {"equity": {"enabled": True, "net": None}}

    async def get_quotes(self, instruments: list[str]) -> dict[str, Any]:
        # Phase 2: stub — implement for live price refresh
        log.warning("kite_mcp.get_quotes_not_implemented", instruments=instruments)
        return {}

    async def ping(self) -> bool:
        try:
            async with MultiServerMCPClient(self._mcp_config()) as client:
                tools = client.get_tools()
                return len(tools) > 0
        except Exception:
            return False
