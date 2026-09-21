"""Exercise the local HTTP listener's browser-asset connection burst."""

from __future__ import annotations

import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pilferedparrot.web_server import BrowserHTTPServer, IPv6ThreadingHTTPServer


class _ReadyHandler(BaseHTTPRequestHandler):
    """A small response handler used after clients have filled the listener."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler callback name
        body = b"ready"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class WindowsHTTPServerTests(unittest.TestCase):
    """Keep a fresh browser page from losing queued loopback asset requests."""

    BURST_CONNECTIONS = 16

    def test_listener_backlog_covers_a_browser_asset_burst_and_serves_it(self):
        server = BrowserHTTPServer(("127.0.0.1", 0), _ReadyHandler)
        clients = []
        thread = None
        try:
            for _ in range(self.BURST_CONNECTIONS):
                client = socket.create_connection(server.server_address, timeout=2)
                client.settimeout(4)
                clients.append(client)

            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            for client in clients:
                client.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            for client in clients:
                response = self._read_response(client)
                self.assertIn(b"HTTP/1.0 200 OK", response)
                self.assertIn(b"ready", response)
        finally:
            for client in clients:
                client.close()
            if thread is not None:
                server.shutdown()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), "loopback HTTP server did not stop")
            server.server_close()

    @staticmethod
    def _read_response(client):
        response = bytearray()
        while b"\r\n\r\n" not in response or not response.endswith(b"ready"):
            chunk = client.recv(1024)
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response)

    def test_ipv6_listener_inherits_the_browser_backlog(self):
        self.assertTrue(issubclass(BrowserHTTPServer, ThreadingHTTPServer))
        self.assertTrue(issubclass(IPv6ThreadingHTTPServer, BrowserHTTPServer))
        self.assertEqual(IPv6ThreadingHTTPServer.address_family, socket.AF_INET6)
        self.assertEqual(IPv6ThreadingHTTPServer.request_queue_size,
                         BrowserHTTPServer.request_queue_size)


if __name__ == "__main__":
    unittest.main()
