"""Zero-admin local PAC + HTTP/CONNECT split-routing bridge for HEMIS.

Keeps general browser traffic DIRECT over the primary high-speed Wi-Fi route
while tunneling only HEMIS hosts (hemis.namdtu.uz, student.namdtu.uz,
195.158.3.34) through an unblocked secondary local interface (e.g., Bluetooth PAN
or USB tethering) via source_address socket binding.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import select
import socket
import socketserver
import sys
import threading
import urllib.parse
from typing import Any

try:  # Windows-only registry integration; optional elsewhere
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

try:
    from hemis_oneid_auth import discover_local_ipv4s, prioritize_local_ipv4s
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hemis_oneid_auth import discover_local_ipv4s, prioritize_local_ipv4s

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8899
INTERNET_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
INTERNET_OPTION_REFRESH = 37
INTERNET_OPTION_SETTINGS_CHANGED = 39
HEMIS_HOSTS = ("hemis.namdtu.uz", "student.namdtu.uz", "195.158.3.34")


def _collect_configured_hosts(extra_hosts: list[str] | tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Combine default HEMIS hosts with any hosts from HEMIS_BASE_URL / HEMIS_PROXY_HOSTS / CLI."""
    items: list[str] = list(HEMIS_HOSTS)
    for env_key in ("HEMIS_BASE_URL", "HEMIS_STUDENT_URL"):
        val = os.environ.get(env_key, "").strip()
        if val:
            host = urllib.parse.urlsplit(val if "://" in val else f"https://{val}").hostname
            if host:
                items.append(host.lower())
    env_hosts = os.environ.get("HEMIS_PROXY_HOSTS", "")
    if env_hosts:
        items.extend(h.strip().lower() for h in env_hosts.split(",") if h.strip())
    if extra_hosts:
        items.extend(h.strip().lower() for h in extra_hosts if h and h.strip())
    seen: set[str] = set()
    ordered: list[str] = []
    for h in items:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return tuple(ordered)


def is_allowed_proxy_target(
    host: str,
    extra_hosts: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Return True if host is an allowed HEMIS, OneID, or loopback self-test destination."""
    h = host.strip().lower().rstrip(".")
    if not h:
        return False
    if h in ("127.0.0.1", "localhost", "::1"):
        return True
    allowed = _collect_configured_hosts(extra_hosts)
    for candidate in allowed:
        if h == candidate or h.endswith("." + candidate):
            return True
    if h.endswith(".uz") and (
        h.startswith("hemis.")
        or h.startswith("student.")
        or ".hemis." in h
        or ".student." in h
        or h == "egov.uz"
        or h.endswith(".egov.uz")
    ):
        return True
    return False


def build_pac_script(
    proxy_host: str = DEFAULT_HOST,
    proxy_port: int = DEFAULT_PORT,
    extra_hosts: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return a PAC script routing HEMIS portals (any hemis.*.uz / student.*.uz + configured hosts) via the local split-proxy."""
    configured = _collect_configured_hosts(extra_hosts)
    conditions = [
        '        shExpMatch(host, "hemis.*.uz")',
        '        shExpMatch(host, "*.hemis.*.uz")',
        '        shExpMatch(host, "student.*.uz")',
        '        shExpMatch(host, "*.student.*.uz")',
    ]
    for h in configured:
        conditions.append(f'        host === "{h}"')
        if not h[0].isdigit():
            conditions.append(f'        dnsDomainIs(host, ".{h}")')
    joined = " ||\n".join(conditions)
    return (
        "function FindProxyForURL(url, host) {\n"
        "    host = host.toLowerCase();\n"
        "    if (\n"
        f"{joined}\n"
        "    ) {\n"
        f'        return "PROXY {proxy_host}:{proxy_port}; DIRECT";\n'
        "    }\n"
        '    return "DIRECT";\n'
        "}\n"
    )


def _notify_wininet_settings_changed() -> None:
    """Notify Windows WinINet / Chrome that proxy settings changed."""
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except (OSError, AttributeError):
        pass


def enable_pac_registry(pac_url: str) -> None:
    """Set HKCU AutoConfigURL to pac_url without enabling global ProxyEnable (Windows only)."""
    if winreg is None:
        raise RuntimeError("PAC registry integration is Windows-only; set the PAC URL manually.")
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        INTERNET_SETTINGS_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, pac_url)
    _notify_wininet_settings_changed()


def disable_pac_registry() -> bool:
    """Remove HKCU AutoConfigURL if present and notify Windows."""
    if winreg is None:
        raise RuntimeError("PAC registry integration is Windows-only.")
    removed = False
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        INTERNET_SETTINGS_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        try:
            winreg.DeleteValue(key, "AutoConfigURL")
            removed = True
        except FileNotFoundError:
            removed = False
    _notify_wininet_settings_changed()
    return removed


_prioritize_source_candidates = prioritize_local_ipv4s


class SourceSelector:
    """Tracks and probes the active unblocked outbound IPv4 source interface."""

    def __init__(
        self,
        explicit_source_ip: str | None = None,
        connect_timeout: float = 4.0,
        allow_fallback_on_explicit_error: bool = True,
    ) -> None:
        self.explicit_source_ip = explicit_source_ip
        self.connect_timeout = connect_timeout
        self.allow_fallback_on_explicit_error = allow_fallback_on_explicit_error
        self._lock = threading.Lock()
        self._active_source_ip: str | None = explicit_source_ip

    @property
    def active_source_ip(self) -> str | None:
        with self._lock:
            return self._active_source_ip

    def connect(self, host: str, port: int) -> tuple[socket.socket, str | None]:
        """Connect TCP socket to (host, port) bound to an unblocked local IPv4 source."""
        tried_ips: set[str] = set()

        if self.explicit_source_ip:
            tried_ips.add(self.explicit_source_ip)
            try:
                sock = socket.create_connection(
                    (host, port),
                    timeout=self.connect_timeout,
                    source_address=(self.explicit_source_ip, 0),
                )
                return sock, self.explicit_source_ip
            except OSError:
                if not self.allow_fallback_on_explicit_error:
                    raise
                with self._lock:
                    if self._active_source_ip == self.explicit_source_ip:
                        self._active_source_ip = None

        cached = self.active_source_ip
        if cached and cached not in tried_ips:
            tried_ips.add(cached)
            try:
                sock = socket.create_connection(
                    (host, port),
                    timeout=self.connect_timeout,
                    source_address=(cached, 0),
                )
                return sock, cached
            except OSError:
                with self._lock:
                    if self._active_source_ip == cached:
                        self._active_source_ip = None

        candidates = prioritize_local_ipv4s(discover_local_ipv4s())
        for candidate_ip in candidates:
            if candidate_ip in tried_ips:
                continue
            try:
                sock = socket.create_connection(
                    (host, port),
                    timeout=self.connect_timeout,
                    source_address=(candidate_ip, 0),
                )
                with self._lock:
                    self._active_source_ip = candidate_ip
                return sock, candidate_ip
            except OSError:
                continue

        # Final fallback to OS default route if no explicit interface succeeded
        sock = socket.create_connection((host, port), timeout=self.connect_timeout)
        with self._lock:
            self._active_source_ip = None
        return sock, None


def _parse_host_port(authority: str, default_port: int = 443) -> tuple[str, int]:
    """Extract (host, port) from host[:port] or [ipv6]:port."""
    authority = authority.strip()
    if authority.startswith("["):
        end_bracket = authority.find("]")
        if end_bracket == -1:
            raise ValueError(f"Malformed IPv6 authority: {authority}")
        host = authority[1:end_bracket]
        rest = authority[end_bracket + 1 :]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, default_port
    if ":" in authority:
        host_part, port_part = authority.rsplit(":", 1)
        if port_part.isdigit():
            return host_part, int(port_part)
    return authority, default_port


def _read_http_head(sock: socket.socket, max_bytes: int = 65536) -> tuple[bytes, bytes]:
    """Read raw HTTP request headers up to CRLFCRLF and return (head, trailing_bytes)."""
    buf = bytearray()
    while len(buf) < max_bytes:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        sep_idx = buf.find(b"\r\n\r\n")
        if sep_idx != -1:
            split_at = sep_idx + 4
            return bytes(buf[:split_at]), bytes(buf[split_at:])
    return bytes(buf), b""


def _relay_bidirectional(
    client_sock: socket.socket,
    upstream_sock: socket.socket,
    idle_timeout: float = 120.0,
) -> None:
    """Relay TCP bytes symmetrically (preserving TCP half-close until both sides finish)."""
    client_sock.settimeout(None)
    upstream_sock.settimeout(None)
    sockets = [client_sock, upstream_sock]
    while sockets:
        readable, _, exceptional = select.select(sockets, [], sockets, idle_timeout)
        if exceptional or not readable:
            break
        for src in readable:
            dst = upstream_sock if src is client_sock else client_sock
            try:
                data = src.recv(65536)
            except OSError:
                return
            if not data:
                if src in sockets:
                    sockets.remove(src)
                if dst in sockets:
                    try:
                        dst.shutdown(socket.SHUT_WR)
                    except OSError:
                        return
                else:
                    return
                continue
            try:
                dst.sendall(data)
            except OSError:
                return


class SplitProxyRequestHandler(socketserver.BaseRequestHandler):
    """Handles PAC serving, HTTPS CONNECT tunnels, and plain HTTP proxy requests."""

    server: SplitProxyServer

    def handle(self) -> None:
        client_sock: socket.socket = self.request
        client_sock.settimeout(15.0)
        try:
            head, trailing = _read_http_head(client_sock)
        except OSError:
            return
        if not head:
            return

        lines = head.decode("latin-1", errors="replace").split("\r\n")
        request_line = lines[0].strip()
        parts = request_line.split()
        if len(parts) < 3:
            self._send_error(400, "Bad Request")
            return

        method, target, version = parts[0].upper(), parts[1], parts[2]
        parsed_target = urllib.parse.urlsplit(target)
        path = parsed_target.path or target

        if method in ("GET", "HEAD") and path in ("/proxy.pac", "/wpad.dat"):
            self._serve_pac(head_only=(method == "HEAD"))
            return

        if method == "CONNECT":
            try:
                target_host, target_port = _parse_host_port(target, default_port=443)
            except ValueError:
                self._send_error(400, "Bad Authority")
                return
            self._handle_connect(client_sock, target_host, target_port, trailing)
            return

        if parsed_target.scheme in ("http", "ws") and parsed_target.netloc:
            try:
                target_host, target_port = _parse_host_port(parsed_target.netloc, default_port=80)
            except ValueError:
                self._send_error(400, "Bad Authority")
                return
            self._handle_plain_http(
                client_sock, method, parsed_target, version, lines[1:], trailing, target_host, target_port
            )
            return

        self._send_error(404, "Not Found")

    def _send_error(self, code: int, reason: str) -> None:
        payload = (
            f"HTTP/1.1 {code} {reason}\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        try:
            self.request.sendall(payload)
        except OSError:
            pass

    def _serve_pac(self, head_only: bool = False) -> None:
        pac_bytes = build_pac_script(
            self.server.proxy_host,
            self.server.proxy_port,
            extra_hosts=self.server.extra_hosts,
        ).encode("utf-8")
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/x-ns-proxy-autoconfig; charset=utf-8\r\n"
            "Cache-Control: no-cache, no-store, must-revalidate\r\n"
            f"Content-Length: {len(pac_bytes)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        try:
            self.request.sendall(headers if head_only else headers + pac_bytes)
        except OSError:
            pass

    def _handle_connect(
        self,
        client_sock: socket.socket,
        target_host: str,
        target_port: int,
        trailing: bytes,
    ) -> None:
        if self.server.restrict_targets and not is_allowed_proxy_target(
            target_host, self.server.extra_hosts
        ):
            self._send_error(403, "Forbidden Target Host")
            return
        try:
            upstream, _used_ip = self.server.source_selector.connect(target_host, target_port)
        except OSError:
            self._send_error(502, "Bad Gateway")
            return

        try:
            client_sock.sendall(
                b"HTTP/1.1 200 Connection Established\r\n"
                b"Proxy-Agent: hemis-split-proxy\r\n\r\n"
            )
            if trailing:
                upstream.sendall(trailing)
            _relay_bidirectional(client_sock, upstream)
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _handle_plain_http(
        self,
        client_sock: socket.socket,
        method: str,
        parsed_target: urllib.parse.SplitResult,
        version: str,
        raw_headers: list[str],
        trailing: bytes,
        target_host: str,
        target_port: int,
    ) -> None:
        if self.server.restrict_targets and not is_allowed_proxy_target(
            target_host, self.server.extra_hosts
        ):
            self._send_error(403, "Forbidden Target Host")
            return
        try:
            upstream, _used_ip = self.server.source_selector.connect(target_host, target_port)
        except OSError:
            self._send_error(502, "Bad Gateway")
            return

        origin_path = parsed_target.path or "/"
        if parsed_target.query:
            origin_path = f"{origin_path}?{parsed_target.query}"

        filtered_headers: list[str] = []
        for hdr in raw_headers:
            if not hdr:
                continue
            name = hdr.split(":", 1)[0].strip().lower()
            if name in ("proxy-connection", "proxy-authorization", "connection", "keep-alive"):
                continue
            filtered_headers.append(hdr)
        filtered_headers.append("Connection: close")

        rebuilt = (
            f"{method} {origin_path} {version}\r\n"
            + "\r\n".join(filtered_headers)
            + "\r\n\r\n"
        ).encode("latin-1")

        try:
            upstream.sendall(rebuilt)
            if trailing:
                upstream.sendall(trailing)
            _relay_bidirectional(client_sock, upstream)
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass


class SplitProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Multi-threaded local PAC & split-routing proxy server."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        source_selector: SourceSelector,
        extra_hosts: tuple[str, ...] = (),
        restrict_targets: bool = True,
    ) -> None:
        self.source_selector = source_selector
        self.proxy_host, self.proxy_port = server_address
        self.extra_hosts = extra_hosts
        self.restrict_targets = restrict_targets
        super().__init__(server_address, SplitProxyRequestHandler)


def run_self_test() -> dict[str, Any]:
    """Verify PAC serving, CONNECT tunneling, HTTP forwarding, host guard, and stale source_ip recovery offline."""
    pac = build_pac_script("127.0.0.1", 8899, extra_hosts=["hemis.custom.uz"])
    assert 'host === "hemis.namdtu.uz"' in pac
    assert 'shExpMatch(host, "hemis.*.uz")' in pac
    assert 'host === "hemis.custom.uz"' in pac
    assert 'return "PROXY 127.0.0.1:8899; DIRECT";' in pac
    assert 'return "DIRECT";' in pac
    assert is_allowed_proxy_target("hemis.tatu.uz") is True
    assert is_allowed_proxy_target("student.buxdu.uz") is True
    assert is_allowed_proxy_target("sso.egov.uz") is True
    assert is_allowed_proxy_target("example.com") is False
    assert is_allowed_proxy_target("custom.uni.uz", extra_hosts=("custom.uni.uz",)) is True
    assert prioritize_local_ipv4s(["172.16.0.1", "192.168.0.10", "10.10.0.2"]) == [
        "10.10.0.2",
        "192.168.0.10",
        "172.16.0.1",
    ]

    class _EchoHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            data = self.request.recv(4096)
            if data:
                self.request.sendall(b"ECHO:" + data)

    class _EchoServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with _EchoServer(("127.0.0.1", 0), _EchoHandler) as echo_srv:
        echo_port = echo_srv.server_address[1]
        threading.Thread(target=echo_srv.serve_forever, daemon=True).start()

        selector = SourceSelector(explicit_source_ip="127.0.0.1", connect_timeout=2.0)
        with SplitProxyServer(("127.0.0.1", 0), selector) as proxy_srv:
            proxy_port = proxy_srv.server_address[1]
            threading.Thread(target=proxy_srv.serve_forever, daemon=True).start()

            # Check 2: GET and HEAD /proxy.pac
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=3.0) as cli:
                cli.sendall(b"GET /proxy.pac HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                resp = cli.recv(4096).decode("utf-8", errors="replace")
                assert "200 OK" in resp
                assert "FindProxyForURL" in resp

            with socket.create_connection(("127.0.0.1", proxy_port), timeout=3.0) as cli:
                cli.sendall(b"HEAD /proxy.pac HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                resp_head = cli.recv(4096).decode("utf-8", errors="replace")
                assert "200 OK" in resp_head
                assert "FindProxyForURL" not in resp_head

            # Check 3: CONNECT tunnel bidirectional relay
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=3.0) as cli:
                cli.sendall(
                    f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: 127.0.0.1:{echo_port}\r\n\r\n".encode(
                        "ascii"
                    )
                )
                head, trailing = _read_http_head(cli)
                assert b"200 Connection Established" in head
                assert trailing == b""
                cli.sendall(b"hello-hemis")
                echoed = cli.recv(1024)
                assert echoed == b"ECHO:hello-hemis", echoed

            # Check 4: Plain HTTP proxy forwarding strips hop-by-hop Proxy-Connection & handles SHUT_WR
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=3.0) as cli:
                cli.sendall(
                    (
                        f"GET http://127.0.0.1:{echo_port}/test?a=1 HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{echo_port}\r\n"
                        "Proxy-Connection: keep-alive\r\n"
                        "X-Test-Header: hemis\r\n\r\n"
                    ).encode("ascii")
                )
                cli.shutdown(socket.SHUT_WR)
                fwd_resp = cli.recv(4096).decode("ascii", errors="replace")
                assert fwd_resp.startswith("ECHO:GET /test?a=1 HTTP/1.1\r\n"), fwd_resp
                assert "Proxy-Connection" not in fwd_resp
                assert "Connection: close" in fwd_resp
                assert "X-Test-Header: hemis" in fwd_resp

            # Check 5: Stale cached or disconnected explicit source_ip auto-recovers
            orig_discover = discover_local_ipv4s
            try:
                globals()["discover_local_ipv4s"] = lambda: []
                stale_explicit = SourceSelector(
                    explicit_source_ip="192.0.2.1",
                    connect_timeout=1.0,
                    allow_fallback_on_explicit_error=True,
                )
                with stale_explicit.connect("127.0.0.1", echo_port)[0] as s1:
                    s1.sendall(b"recovered")
                    assert s1.recv(1024) == b"ECHO:recovered"
                assert stale_explicit.active_source_ip is None

                stale_cached = SourceSelector(explicit_source_ip=None, connect_timeout=1.0)
                stale_cached._active_source_ip = "192.0.2.1"
                sock2, used_ip = stale_cached.connect("127.0.0.1", echo_port)
                with sock2:
                    assert used_ip is None
                    assert stale_cached.active_source_ip is None
                    sock2.sendall(b"cached-recovered")
                    assert sock2.recv(1024) == b"ECHO:cached-recovered"
            finally:
                globals()["discover_local_ipv4s"] = orig_discover

            # Check 6: Target host allowlist blocks arbitrary non-HEMIS domains (403 Forbidden)
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=3.0) as cli:
                cli.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
                forbidden_head, _ = _read_http_head(cli)
                assert b"403 Forbidden" in forbidden_head, forbidden_head

            proxy_srv.shutdown()
        echo_srv.shutdown()

    return {
        "self_test_passed": True,
        "checks_passed": 6,
        "details": [
            "pac_script_multi_uni_and_candidate_ordering",
            "pac_http_serving",
            "connect_tunnel_bidirectional_relay",
            "plain_http_proxy_forwarding",
            "stale_source_ip_auto_recovery",
            "target_host_allowlist_guard",
        ],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local PAC + CONNECT split-routing bridge for HEMIS portals"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default: 8899)")
    parser.add_argument(
        "--source-ip",
        default=os.environ.get("HEMIS_SOURCE_IP") or None,
        help="Explicit outbound IPv4 address (auto-discovers Bluetooth PAN / 10.* if omitted)",
    )
    parser.add_argument(
        "--hosts",
        default="",
        help="Comma-separated additional HEMIS hostnames/IPs to allow and include in PAC",
    )
    parser.add_argument(
        "--enable-pac",
        action="store_true",
        help="Register HKCU AutoConfigURL for this proxy before serving",
    )
    parser.add_argument(
        "--disable-pac",
        action="store_true",
        help="Remove HKCU AutoConfigURL and exit",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline PAC + CONNECT tunnel self-test and exit",
    )
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.self_test:
        import json

        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0

    if args.disable_pac:
        removed = disable_pac_registry()
        print(f"AutoConfigURL disabled (existed={removed}).")
        return 0

    extra_hosts = tuple(h.strip().lower() for h in args.hosts.split(",") if h.strip())
    pac_url = f"http://{args.host}:{args.port}/proxy.pac"
    if args.enable_pac:
        enable_pac_registry(pac_url)
        print(f"Registered AutoConfigURL={pac_url}")

    selector = SourceSelector(explicit_source_ip=args.source_ip)
    with SplitProxyServer((args.host, args.port), selector, extra_hosts=extra_hosts) as server:
        print(f"HEMIS split proxy listening on http://{args.host}:{args.port} (PAC: {pac_url})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
