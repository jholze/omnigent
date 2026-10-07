"""A fake Databricks workspace, over HTTPS, for driving the real ``databricks auth login``.

Serves the OIDC endpoints the CLI drives (discovery, ``/oidc/v1/authorize``
redirecting straight back to the CLI's ``redirect_uri`` with a code, and
``/oidc/v1/token``) plus the ``/api/2.0/omnigent`` mount, whose anonymous
``/v1/me`` answers the ``DatabricksRealm`` challenge and accepts minted bearers.
TLS comes from a throwaway CA minted in-process with ``cryptography`` and trusted
via ``SSL_CERT_FILE``/``REQUESTS_CA_BUNDLE``.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import secrets
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_REPO_ROOT = Path(__file__).resolve().parents[2]

WORKSPACE_ID = "1234567890123456"
USER_EMAIL = "e2e-user@example.test"
ACCESS_TOKEN_PREFIX = "fake-workspace-access-"
OMNIGENT_MOUNT_PATH = "/api/2.0/omnigent"
BROWSER_URL_FILE_ENV = "OMNI_E2E_BROWSER_URL_FILE"

# Ambient credentials/config that would leak into the CLI subprocess and
# defeat the fresh-login state (CI runners carry Databricks vars), plus proxy
# vars that would route the loopback mock through a proxy that can't reach it.
ENV_TO_CLEAR = (
    "DATABRICKS_HOST",
    "DATABRICKS_TOKEN",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_CONFIG_FILE",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OMNIGENT_DATA_DIR",
    "OMNIGENT_CONFIG_HOME",
    "OMNIGENT_REMOTE_AUTH_TOKEN",
    "OMNIGENT_DATABASE_URI",
    "OMNIGENT_RUNNER_TUNNEL_TOKEN",
    "OMNIGENT_RUNNER_ID",
    "OMNIGENT_HOST_ID",
    "OMNIGENT_HOST_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "BROWSER",
)


def _write_pem(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def make_ca_and_server_cert(directory: Path) -> tuple[Path, Path, Path]:
    """Generate a throwaway CA and a ``localhost``/``127.0.0.1`` leaf it signs.

    :returns: ``(ca_pem, cert_pem, key_pem)``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.UTC)
    not_before, not_after = now - dt.timedelta(minutes=5), now + dt.timedelta(days=2)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "omnigent-e2e-fake-databricks-ca")]
    )

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    pem = serialization.Encoding.PEM
    return (
        _write_pem(directory / "ca.pem", ca_cert.public_bytes(pem)),
        _write_pem(directory / "cert.pem", leaf_cert.public_bytes(pem)),
        _write_pem(
            directory / "key.pem",
            leaf_key.private_bytes(
                pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
        ),
    )


@dataclass
class RequestRecord:
    at: float
    method: str
    path: str
    has_auth: bool


@dataclass
class FakeDatabricksWorkspace:
    """A running fake workspace; use as a context manager.

    :param cert_dir: Where the CA/leaf PEMs are written.
    :param stall_token_exchange: Hold every ``/oidc/v1/token`` request open until
        :attr:`release_token_exchange` is set.
    """

    cert_dir: Path
    stall_token_exchange: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    requests: list[RequestRecord] = field(default_factory=list)
    authorize_requests: list[dict[str, str]] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    token_exchange_started: threading.Event = field(default_factory=threading.Event)
    release_token_exchange: threading.Event = field(default_factory=threading.Event)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    _minted: int = 0

    def __enter__(self) -> FakeDatabricksWorkspace:
        self.ca_pem, cert, key = make_ca_and_server_cert(self.cert_dir)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert), keyfile=str(key))
        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        server.daemon_threads = True
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.workspace = self  # type: ignore[attr-defined]
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release_token_exchange.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def workspace_url(self) -> str:
        return f"https://{self.host}:{self.port}"

    @property
    def issuer(self) -> str:
        return f"{self.workspace_url}/oidc"

    @property
    def omnigent_server_url(self) -> str:
        return f"{self.workspace_url}{OMNIGENT_MOUNT_PATH}"

    def mint_access_token(self) -> str:
        self._minted += 1
        return f"{ACCESS_TOKEN_PREFIX}{self._minted}-{secrets.token_hex(8)}"

    def login_env(
        self,
        home: Path,
        *,
        browser_shim_dir: Path,
        browser_url_file: Path,
    ) -> dict[str, str]:
        """Environment for an Omnigent/Databricks CLI subprocess logging in to this workspace.

        Isolated ``HOME``/state, no ambient credentials or proxies, the throwaway CA
        trusted by every client, and *browser_shim_dir* first on ``PATH`` so the
        CLI's ``xdg-open`` lands in :func:`write_browser_shim`'s recorder.
        """
        env = os.environ.copy()
        for key in ENV_TO_CLEAR:
            env.pop(key, None)
        for key in [k for k in env if k.startswith("OMNIGENT_RUNNER_ZYGOTE")]:
            env.pop(key, None)
        home = home.resolve()
        home.mkdir(parents=True, exist_ok=True)
        (home / ".omnigent").mkdir(exist_ok=True)
        ca = str(self.ca_pem.resolve())
        env.update(
            {
                "HOME": str(home),
                "OMNIGENT_DATA_DIR": str(home / ".omnigent"),
                "DATABRICKS_CONFIG_FILE": str(home / ".databrickscfg"),
                "SSL_CERT_FILE": ca,
                "REQUESTS_CA_BUNDLE": ca,
                "CURL_CA_BUNDLE": ca,
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "PATH": f"{browser_shim_dir.resolve()}{os.pathsep}{env.get('PATH', '')}",
                BROWSER_URL_FILE_ENV: str(browser_url_file.resolve()),
                "OMNIGENT_HOST_NO_OPEN": "1",
                "TERM": "xterm",
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(_REPO_ROOT),
                        str(_REPO_ROOT / "sdks" / "python-client"),
                        str(_REPO_ROOT / "sdks" / "ui"),
                        env.get("PYTHONPATH", ""),
                    ]
                ),
            }
        )
        return env


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def workspace(self) -> FakeDatabricksWorkspace:
        return self.server.workspace  # type: ignore[attr-defined]

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object, headers: dict[str, str] | None = None) -> None:
        self._send(
            status,
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", **(headers or {})},
        )

    def do_GET(self) -> None:
        self._dispatch(b"")

    def do_POST(self) -> None:
        self._dispatch(self._read_body())

    def _dispatch(self, body: bytes) -> None:
        ws = self.workspace
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        auth = self.headers.get("Authorization") or ""
        ws.requests.append(RequestRecord(time.monotonic(), self.command, self.path, bool(auth)))

        if path == "/.well-known/databricks-config":
            self._json(200, {"workspace_id": WORKSPACE_ID, "oidc_endpoint": ws.issuer})
        elif path == "/oidc/.well-known/oauth-authorization-server":
            self._json(
                200,
                {
                    "issuer": ws.issuer,
                    "authorization_endpoint": f"{ws.issuer}/v1/authorize",
                    "token_endpoint": f"{ws.issuer}/v1/token",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["all-apis", "offline_access", "sql"],
                    "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                },
            )
        elif path == "/oidc/v1/authorize":
            params = {k: v[0] for k, v in query.items()}
            ws.authorize_requests.append(params)
            redirect_uri = params.get("redirect_uri", "")
            code = secrets.token_urlsafe(24)
            joiner = "&" if "?" in redirect_uri else "?"
            location = (
                f"{redirect_uri}{joiner}code={code}"
                f"&iss={quote(ws.issuer, safe='')}"
                f"&state={quote(params.get('state', ''), safe='')}"
            )
            self._send(302, b"", {"Location": location, "Cache-Control": "no-store"})
        elif path == "/oidc/v1/token":
            form = {k: v[0] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
            ws.token_requests.append(form)
            ws.token_exchange_started.set()
            if ws.stall_token_exchange:
                ws.release_token_exchange.wait()
            self._json(
                200,
                {
                    "access_token": ws.mint_access_token(),
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": f"fake-refresh-{secrets.token_hex(8)}",
                    "scope": "all-apis offline_access",
                },
            )
        elif path.startswith(OMNIGENT_MOUNT_PATH):
            if not auth:
                self._json(
                    401,
                    {
                        "error_code": 401,
                        "message": (
                            "Credential was not sent or was of an unsupported type for this API."
                        ),
                    },
                    {"WWW-Authenticate": 'Bearer realm="DatabricksRealm"'},
                )
            elif not auth.startswith(f"Bearer {ACCESS_TOKEN_PREFIX}"):
                self._json(
                    403, {"error_code": 403, "message": "Invalid access token. [ReqId: fake]"}
                )
            elif path == f"{OMNIGENT_MOUNT_PATH}/v1/me":
                self._json(200, {"user_id": USER_EMAIL}, {"x-databricks-org-id": WORKSPACE_ID})
            else:
                self._json(404, {"detail": "Not Found"})
        elif path.startswith("/api/") and auth.startswith(f"Bearer {ACCESS_TOKEN_PREFIX}"):
            self._json(200, {"userName": USER_EMAIL, "id": "1"})
        else:
            self._json(404, {"error_code": "NOT_FOUND", "message": path})

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def write_browser_shim(directory: Path, url_file: Path | None = None) -> Path:
    """Install an ``xdg-open`` that records the URL instead of opening a browser.

    The Databricks CLI opens the authorize URL through ``xdg-open`` on Linux; the
    recorded URL is what a journey driver then navigates. The target file comes
    from ``OMNI_E2E_BROWSER_URL_FILE`` unless *url_file* pins it (for shells that
    do not inherit the driver's environment). Returns *directory*.
    """
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / "xdg-open"
    target = (
        f'"{url_file.resolve()}"' if url_file is not None else f'"${{{BROWSER_URL_FILE_ENV}:?}}"'
    )
    shim.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> {target}\nexit 0\n")
    shim.chmod(0o755)
    return directory


def wait_for_browser_url(url_file: Path, *, timeout: float) -> str:
    """Return the first URL the shim recorded, waiting up to *timeout* seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if url_file.exists():
            lines = [line for line in url_file.read_text().splitlines() if line.strip()]
            if lines:
                return lines[0]
        time.sleep(0.1)
    raise TimeoutError(f"no browser URL recorded in {url_file} within {timeout}s")


class BlackHoleListener:
    """Accepts TCP connections and never answers them.

    Stands in for a loopback port that something owns but nothing serves, so a
    browser hitting it sees a request that stays pending.
    """

    def __init__(self, address: str, port: int) -> None:
        self.address, self.port = address, port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((address, port))
        self._sock.listen(16)
        self._held: list[socket.socket] = []
        self.accepted = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.accepted += 1
            self._held.append(conn)

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        for conn in self._held:
            conn.close()
        self._thread.join(timeout=2)
