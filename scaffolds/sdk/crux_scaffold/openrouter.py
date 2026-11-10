"""Shared BYOK provider routes and a loopback adapter for native coding harnesses."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx

OPENROUTER_PROVIDER = 'google-ai-studio'
OPENROUTER_PROVIDERS = {'google': OPENROUTER_PROVIDER, 'anthropic': 'anthropic', 'openai': 'openai'}
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
HOP_HEADERS = {'host', 'content-length', 'connection', 'transfer-encoding', 'keep-alive',
               'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'upgrade'}


def is_openrouter(base_url: str | None) -> bool:
    return bool(base_url and urlsplit(base_url).hostname == 'openrouter.ai')


def routed_body(model: str, body: dict) -> dict:
    """Restrict research models to their BYOK provider; workspace BYOK-only controls are also required."""
    provider = OPENROUTER_PROVIDERS.get(model.lstrip('~').split('/', 1)[0])
    if provider is None:
        return body
    return {**body, 'provider': {**(body.get('provider') or {}),
                               'only': [provider], 'allow_fallbacks': False}}


@asynccontextmanager
async def routing_proxy(*, base_url: str = OPENROUTER_BASE_URL, port: int = 0,
                        transport: httpx.BaseTransport | None = None, read_timeout: float = 300):
    """Stream native HTTP requests through the same policy. No bodies or credentials are logged.

    Bind only to loopback, forward only inference paths to the fixed upstream, and leave errors/retries
    to the coding harness. The caller owns this adapter for exactly as long as its native client.
    """
    with httpx.Client(transport=transport, timeout=httpx.Timeout(read_timeout, connect=10), follow_redirects=False) as client:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.connection.settimeout(30)
                path = '/messages' if self.path == '/v1/messages' else self.path
                if path not in {'/responses', '/responses/compact', '/chat/completions', '/messages'}:
                    self.send_error(404)
                    return
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
                    self.send_error(415)
                    return
                try:
                    size = int(self.headers['Content-Length'])
                    if size < 0:
                        raise ValueError
                    body = json.loads(self.rfile.read(size))
                    body = routed_body(body.get('model', ''), body)
                except (ValueError, TypeError, AttributeError):
                    self.send_error(400)
                    return
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
                sent = False
                try:
                    with client.stream('POST', base_url.rstrip('/') + path, headers=headers, json=body) as response:
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in HOP_HEADERS:
                                self.send_header(key, value)
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        sent = True
                        for chunk in response.iter_raw():
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (httpx.HTTPError, OSError):
                    if not sent:
                        self.send_error(502, 'Upstream connection failed')
                finally:
                    self.close_connection = True

        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()


def main():
    """Standalone adapter for ACP boxes; the SDK embeds it in each coding client's lifecycle."""
    import argparse
    import signal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()

    async def serve():
        stopped = asyncio.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
        async with routing_proxy(port=args.port):
            await stopped.wait()

    asyncio.run(serve())


if __name__ == '__main__':
    main()
