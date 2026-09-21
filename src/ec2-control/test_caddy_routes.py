"""Exercise the generated access policy through a real Caddy process."""

import http.client
import http.server
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time


class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *_args):
        pass


def check_routes():
    caddy = os.environ.get("CADDY_BIN", "caddy")
    denied_source = os.environ.get("TEST_DENIED_SOURCE", "127.0.0.2")
    backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    try:
        for public in ("true", "false"):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, TLS_HOSTNAME=f"http://127.0.0.1:{port}",
                       AGENTRQ_PORT=str(backend.server_port), TLS_EMAIL="",
                       SLACK_PUBLIC_CALLBACKS=public,
                       TLS_ALLOWED_CIDRS="192.0.2.10/32")
            config = subprocess.check_output(
                ["bash", str(Path(__file__).with_name("render-caddyfile.sh"))], env=env
            )
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "Caddyfile"
                path.write_bytes(b"{\n admin off\n}\n" + config)
                with (Path(directory) / "caddy.log").open("w+") as log:
                    process = subprocess.Popen(
                        [caddy, "run", "--config", str(path), "--adapter", "caddyfile"],
                        stdout=log, stderr=log,
                    )
                    try:
                        for _ in range(100):
                            try:
                                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                                    break
                            except OSError:
                                if process.poll() is not None:
                                    log.seek(0)
                                    raise RuntimeError(log.read())
                                time.sleep(0.05)
                        else:
                            raise RuntimeError("Caddy did not start")

                        def request(method, route, source, expected, headers=None):
                            connection = http.client.HTTPConnection(
                                "127.0.0.1", port, timeout=3, source_address=(source, 0)
                            )
                            try:
                                connection.request(method, route, headers=headers or {})
                                response = connection.getresponse()
                                assert response.status == expected, (
                                    public, method, route, source, response.status, expected
                                )
                                response.read()
                            finally:
                                connection.close()

                        denied = 403 if public == "true" else 204
                        for route in ("/", "/api/v1/workspaces", "/mcp/test", "/slack/oauth/callback"):
                            request("GET", route, "127.0.0.1", 204)
                            request("GET", route, denied_source, denied)
                        for route in ("/slack/events", "/slack/commands", "/slack/interactions"):
                            request("POST", route, denied_source, 204)
                            request("GET", route, denied_source, denied)
                            request("POST", route + "/", denied_source, denied)
                            request("POST", route + "/extra", denied_source, denied)
                        request("GET", "/", denied_source, denied, {
                            "X-Forwarded-For": "127.0.0.1",
                            "X-Real-IP": "127.0.0.1",
                            "Forwarded": "for=127.0.0.1",
                        })
                        print(f"PASS: public callbacks={public}, operator access, exact routes, methods, spoofed IP")
                    finally:
                        process.terminate()
                        process.wait(timeout=5)
    finally:
        backend.shutdown()
        backend.server_close()


if __name__ == "__main__":
    check_routes()
