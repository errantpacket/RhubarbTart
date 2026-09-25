# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Serve one file to a VM over Tart's host-only bridge, then exit.

Why not Packer's HTTP server: the Tart plugin runs `tart ip` to find the host address
before typing boot_command, and a Linux guest sitting at GRUB has no IP yet. So we bind
our own server — only on the vmnet host address (never the LAN), waiting for the bridge
to appear once the VM starts — and hard-code that address in boot_command.

  serve_preseed.py <file> <bind-ip> <port> [--timeout SECONDS]
"""

import argparse
import http.server
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("ip")
    ap.add_argument("port", type=int)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()
    body = open(args.file, "rb").read()
    deadline = time.time() + args.timeout

    class Handler(http.server.BaseHTTPRequestHandler):
        served = False

        def do_GET(self):
            if self.path != "/preseed.cfg":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            Handler.served = True

        def log_message(self, fmt, *a):
            print(f"[serve-preseed] {self.client_address[0]} {fmt % a}", file=sys.stderr)

    # The bridge (e.g. bridge100 with 192.168.64.1) only exists once a VM is running.
    while True:
        try:
            srv = http.server.HTTPServer((args.ip, args.port), Handler)
            break
        except OSError:
            if time.time() > deadline:
                sys.exit(f"[serve-preseed] {args.ip} never came up")
            time.sleep(0.5)
    srv.timeout = 1
    print(f"[serve-preseed] serving on http://{args.ip}:{args.port}/preseed.cfg", file=sys.stderr)
    while not Handler.served and time.time() < deadline:
        srv.handle_request()
    # Linger briefly in case the installer retries, then stop serving the file.
    end = time.time() + 30
    while time.time() < end:
        srv.handle_request()
    srv.server_close()


if __name__ == "__main__":
    main()
