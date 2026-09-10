"""
HTTP request tool, web fetch, and web search integration.
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── SSRF protection ──────────────────────────────────────────────────────
# The agent fetches agent-chosen URLs, so every outbound request must prove
# its destination is a public internet host. We (1) reject non-http(s)
# schemes, (2) resolve the hostname and reject any non-public IP
# (private/loopback/link-local/multicast/reserved/unspecified, including
# IPv4-mapped IPv6 like ::ffff:127.0.0.1), (3) re-validate on every redirect,
# and (4) PIN the validated IP for the actual connection so a DNS-rebinding
# race between check and connect can't swap in an internal address.


def _assert_public_url(url: str) -> list[str]:
    """Validate `url` and return its resolved public IPs. Raises ValueError."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme {parsed.scheme!r}: only http/https are allowed")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for {host!r}: {e}") from e
    ips: list[str] = []
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        addrs = [ip]
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            addrs.append(ip.ipv4_mapped)  # ::ffff:10.0.0.1 must not smuggle private v4
        for addr in addrs:
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_multicast
                or addr.is_reserved
                or addr.is_unspecified
            ):
                raise ValueError(f"Blocked SSRF target: {host!r} resolves to {ip_str}")
        ips.append(ip_str)
    if not ips:
        raise ValueError(f"Could not resolve {host!r} to a public IP")
    return ips


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Dials a pre-validated IP while keeping the hostname for Host/SNI."""

    def __init__(self, host, pinned_ip, *args, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS variant: pinned IP for TCP, real hostname for SNI + cert check."""

    def __init__(self, host, pinned_ip, *args, **kwargs):
        super().__init__(host, *args, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _SSRFHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        pinned = _assert_public_url(req.full_url)[0]
        return self.do_open(_PinnedHTTPConnection, req, pinned_ip=pinned)


class _SSRFHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        pinned = _assert_public_url(req.full_url)[0]
        return self.do_open(_PinnedHTTPSConnection, req, pinned_ip=pinned)


class _SSRFRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_public_url(newurl)  # raises before we follow a redirect to 169.254.x.x
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_opener() -> urllib.request.OpenerDirector:
    # Explicit empty ProxyHandler: env proxies must not bypass the IP checks.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SSRFHTTPHandler,
        _SSRFHTTPSHandler,
        _SSRFRedirectHandler,
    )


def _require_http_scheme(url: str) -> None:
    """Reject non-HTTP(S) URLs so urlopen can't reach file:/ or custom schemes.

    Scheme-only fast path; the safe opener performs the full SSRF
    (DNS + IP + redirect) validation when the request is actually sent.
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme {scheme!r}: only http/https are allowed")


class HttpTool(ToolBase):
    name = "Http"
    description = "Make an HTTP request to any URL. Supports GET, POST, PUT, PATCH, DELETE with JSON body and auth."
    aliases = ["Fetch", "Curl"]
    parameters = {
        "method": {"type": "string", "description": "HTTP method (default: GET)"},
        "url": {"type": "string", "description": "Full URL including scheme (https://...)"},
        "headers": {
            "type": "object",
            "description": "Additional request headers as key-value pairs",
        },
        "json_body": {"type": "object", "description": "Request body as a JSON object"},
        "bearer_token": {"type": "string", "description": "Bearer token for Authorization header"},
        "timeout_sec": {
            "type": "integer",
            "description": "Request timeout in seconds (default: 30)",
        },
    }

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        json_body: dict | None = None,
        bearer_token: str = "",
        timeout_sec: int = 30,
    ) -> ToolOutput:
        timeout = min(timeout_sec, 120)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._do_request, url, method, headers, json_body, bearer_token, timeout
                ),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            return ToolOutput(text="HTTP request timed out after " + str(timeout) + "s", error=True)
        except Exception as e:
            return ToolOutput(text=f"HTTP error: {e}", error=True)

    def _do_request(
        self,
        url: str,
        method: str,
        headers: dict | None,
        json_body: dict | None,
        bearer_token: str,
        timeout: int,
    ) -> ToolOutput:
        try:
            scheme = urllib.parse.urlparse(url).scheme.lower()
            if bearer_token and scheme != "https":
                return ToolOutput(
                    text="Refused: bearer tokens are only sent over HTTPS",
                    error=True,
                )
            req_headers = headers or {}
            if bearer_token:
                req_headers["Authorization"] = f"Bearer {bearer_token}"

            data = None
            if json_body:
                data = json.dumps(json_body).encode("utf-8")
                req_headers.setdefault("Content-Type", "application/json")

            _require_http_scheme(url)
            req = urllib.request.Request(url, data=data, method=method.upper())
            for k, v in req_headers.items():
                req.add_header(k, v)

            with _safe_opener().open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.status

            # Pretty print JSON
            try:
                parsed = json.loads(body)
                if isinstance(parsed, (dict, list)):
                    body = json.dumps(parsed, indent=2)
            except json.JSONDecodeError:
                pass

            if len(body) > 8000:
                body = body[:8000] + "\n... [truncated]"

            return ToolOutput(
                text=body,
                title="HTTP " + method.upper() + " " + url + " -> " + str(status),
                metadata={"status_code": status, "method": method, "url": url},
                error=status >= 400,
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:2000]
            return ToolOutput(
                text=body,
                title="HTTP " + method.upper() + " " + url + " -> " + str(e.code),
                metadata={"status_code": e.code},
                error=True,
            )
        except Exception as e:
            return ToolOutput(text=f"HTTP error: {e}", error=True)


class WebFetchTool(ToolBase):
    name = "WebFetch"
    description = "Fetch content from a URL and extract its text content."
    aliases = ["FetchWeb", "ReadUrl"]
    parameters = {
        "url": {"type": "string", "description": "The URL to fetch"},
    }

    async def execute(self, url: str) -> ToolOutput:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._do_fetch, url),
                timeout=35,
            )
        except asyncio.TimeoutError:
            return ToolOutput(text="Fetch timed out after 30s", error=True)
        except Exception as e:
            return ToolOutput(text=f"Fetch error: {e}", error=True)

    @staticmethod
    def _do_fetch(url: str) -> ToolOutput:
        try:
            _require_http_scheme(url)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 CodingAgent/2.0"})
            with _safe_opener().open(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Simple HTML text extraction
            import html as html_mod
            import re

            # Remove scripts, styles, head
            html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<head[^>]*>.*?</head>", "", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)

            # Strip tags
            text = re.sub(r"<[^>]+>", " ", html)
            text = html_mod.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            text = re.sub(r"\n\s*\n", "\n", text)

            if len(text) > 10000:
                text = text[:10000] + "\n... [truncated]"

            return ToolOutput(
                text=text,
                title="Fetched " + url,
                metadata={"url": url, "chars": len(text)},
            )
        except urllib.error.HTTPError as e:
            return ToolOutput(text=f"HTTP {e.code}: {e.reason}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Fetch error: {e}", error=True)


class WebSearchTool(ToolBase):
    name = "WebSearch"
    description = "Search the web and get results. Uses DuckDuckGo's free API."
    aliases = ["Search", "Google"]
    parameters = {
        "query": {"type": "string", "description": "The search query"},
    }

    async def execute(self, query: str) -> ToolOutput:
        # Try the official duckduckgo_search library first, then fall back to scraping
        try:
            return await asyncio.wait_for(self._search_ddg_library(query), timeout=20)
        except Exception as e1:
            try:
                return await asyncio.wait_for(self._search_ddg_html(query), timeout=20)
            except Exception as e2:
                return ToolOutput(
                    text=f"Search error (library: {e1})\n(fallback: {e2})\n\nTry a different query or use WebFetch directly.",
                    error=True,
                )

    async def _search_ddg_library(self, query: str) -> ToolOutput:
        """Search using the official ddgs library (most reliable)."""
        import asyncio

        def _do_search():
            results = []
            with DDGS() as ddgs:
                for i, r in enumerate(ddgs.text(query, max_results=10)):
                    title = r.get("title", "").strip()
                    href = r.get("href", "")
                    body = r.get("body", "").strip()
                    results.append(f"  [{i + 1}] {title}\n      {href}\n      {body}")
            return results

        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # fallback to old package name

        results = await asyncio.to_thread(_do_search)

        output = (
            f"Results for: {query}\n\n" + "\n\n".join(results)
            if results
            else f"No results for: {query}"
        )

        return ToolOutput(
            text=output,
            title=f"Search: {query}",
            metadata={"query": query, "results": len(results)},
        )

    async def _search_ddg_html(self, query: str) -> ToolOutput:
        """Fallback: scrape DuckDuckGo's HTML search page."""
        import asyncio

        def _do_search():
            import html as html_mod
            import re
            import urllib.parse

            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )
            with _safe_opener().open(req, timeout=15) as resp:
                html_text = resp.read().decode("utf-8", errors="replace")

            # Extract results via HTML scraping
            results = []
            link_pattern = re.compile(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                re.DOTALL | re.IGNORECASE,
            )
            snippet_pattern = re.compile(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                re.DOTALL,
            )

            links = link_pattern.findall(html_text)
            snippets = snippet_pattern.findall(html_text)

            for i, (href, title) in enumerate(links[:10]):
                title = re.sub(r"<[^>]+>", "", title).strip()
                title = html_mod.unescape(title)
                snippet = ""
                if i < len(snippets):
                    snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
                    snippet = html_mod.unescape(snippet)
                results.append(f"  [{i + 1}] {title}\n      {href}\n      {snippet}")

            return results

        results = await asyncio.to_thread(_do_search)

        output = (
            f"Results for: {query}\n\n" + "\n\n".join(results)
            if results
            else f"No results for: {query}"
        )

        return ToolOutput(
            text=output,
            title=f"Search: {query}",
            metadata={"query": query, "results": len(results)},
        )


register_tool(HttpTool())
register_tool(WebFetchTool())
register_tool(WebSearchTool())
