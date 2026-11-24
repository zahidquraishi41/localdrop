import json
import queue
import random
import socket
import sys
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

peers = {}  # peer_id -> {"name": str, "queue": queue.Queue, "active": bool}
peers_lock = threading.Lock()

transfers = {}  # tid -> {"queue": Queue, "name": str, "size": int, "cancelled": bool, "from": str, "to": str}
transfers_lock = threading.Lock()

ADJECTIVES = [
    "Swift",
    "Silent",
    "Bright",
    "Cosmic",
    "Neon",
    "Calm",
    "Solar",
    "Silver",
    "Amber",
    "Arctic",
    "Velvet",
    "Crimson",
    "Golden",
    "Misty",
]
ANIMALS = [
    "Falcon",
    "Tiger",
    "Dolphin",
    "Fox",
    "Panda",
    "Lynx",
    "Owl",
    "Wolf",
    "Otter",
    "Hawk",
    "Raven",
    "Koala",
    "Badger",
    "Heron",
]

CHUNK_SIZE = 524288  # 512 KB chunks


def load_index_html() -> bytes:
    # 1. PyInstaller --onefile executable
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return (Path(meipass) / "index.html").read_bytes()

    # 2. Direct script execution (python3 server.py)
    html_path = Path(__file__).with_name("index.html")
    if html_path.is_file():
        return html_path.read_bytes()

    # 3. Portable zipapp archive (localdrop.pyz)
    archive_path = Path(__file__).parent
    if archive_path.is_file() and zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            return zf.read("index.html")

    raise FileNotFoundError("index.html not found")


def generate_name(used_names: set[str]) -> str:
    candidates = [
        f"{adj} {animal}"
        for adj in ADJECTIVES
        for animal in ANIMALS
        if f"{adj} {animal}" not in used_names
    ]
    if candidates:
        return random.choice(candidates)
    return (
        f"{random.choice(ADJECTIVES)} {random.choice(ANIMALS)} #{random.randint(2, 99)}"
    )


def send_to_peer(peer_id: str, payload_dict: dict) -> None:
    with peers_lock:
        info = peers.get(peer_id)
        if info and info["active"]:
            info["queue"].put(json.dumps(payload_dict))


def broadcast_peers() -> None:
    with peers_lock:
        peer_list = [
            {"id": pid, "name": info["name"]}
            for pid, info in peers.items()
            if info["active"]
        ]
        payload = json.dumps({"type": "peers", "peers": peer_list})
        for info in peers.values():
            if info["active"]:
                info["queue"].put(payload)


def abort_transfer(
    tid: str, notify_peer: str | None = None, reason: str = "Transfer cancelled."
) -> None:
    with transfers_lock:
        tx = transfers.pop(tid, None)
        if not tx or tx["cancelled"]:
            return
        tx["cancelled"] = True

    q = tx["queue"]
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break
    try:
        q.put_nowait(None)
    except queue.Full:
        pass

    if notify_peer:
        send_to_peer(
            notify_peer, {"type": "transfer-cancelled", "tid": tid, "reason": reason}
        )


def generate_qr_matrix(text: str) -> list[list[int]]:
    """Build a padded QR code matrix (Versions 1–4, ECC-L, Byte mode)."""
    raw = text.encode()
    specs = [
        (1, 17, 19, 7),
        (2, 32, 34, 10),
        (3, 53, 55, 15),
        (4, 78, 80, 20),
    ]
    for ver, max_len, data_cw, ec_cw in specs:
        if len(raw) <= max_len:
            break
    else:
        return []

    # 1. Encode Byte-mode bitstream + padding
    bits = f"0100{len(raw):08b}" + "".join(f"{b:08b}" for b in raw)
    bits += "0" * min(4, data_cw * 8 - len(bits))
    bits += "0" * ((8 - len(bits) % 8) % 8)
    codewords = [int(bits[i : i + 8], 2) for i in range(0, len(bits), 8)]
    pad_bytes = [0xEC, 0x11]
    while len(codewords) < data_cw:
        codewords.append(pad_bytes[len(codewords) % 2])

    # 2. Reed-Solomon error correction over GF(256)
    gf_exp = [1] * 512
    gf_log = [0] * 256
    x = 1
    for i in range(1, 255):
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
        gf_exp[i] = x
        gf_log[x] = i
    for i in range(255, 512):
        gf_exp[i] = gf_exp[i - 255]

    def gf_mul(a: int, b: int) -> int:
        return 0 if (a == 0 or b == 0) else gf_exp[gf_log[a] + gf_log[b]]

    gen = [1]
    for i in range(ec_cw):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= gf_mul(c, gf_exp[i])
        gen = nxt

    rem = codewords + [0] * ec_cw
    for i in range(data_cw):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= gf_mul(gen[j], coef)
    all_cw = codewords + rem[data_cw:]

    # 3. Build QR matrix & place function patterns
    size = 17 + 4 * ver
    grid: list[list[int]] = [[0] * size for _ in range(size)]
    locked = [[False] * size for _ in range(size)]

    def set_mod(r: int, c: int, val: int) -> None:
        if 0 <= r < size and 0 <= c < size:
            grid[r][c] = val
            locked[r][c] = True

    for fr, fc in [(0, 0), (0, size - 7), (size - 7, 0)]:
        for r in range(-1, 8):
            for c in range(-1, 8):
                is_dark = (
                    0 <= r <= 6
                    and 0 <= c <= 6
                    and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                )
                set_mod(fr + r, fc + c, 1 if is_dark else 0)

    if ver >= 2:
        ac = size - 7
        for r in range(-2, 3):
            for c in range(-2, 3):
                set_mod(ac + r, ac + c, 1 if max(abs(r), abs(c)) != 1 else 0)

    for i in range(8, size - 8):
        set_mod(6, i, 1 if i % 2 == 0 else 0)
        set_mod(i, 6, 1 if i % 2 == 0 else 0)

    set_mod(size - 8, 8, 1)

    for i in range(9):
        locked[8][i] = True
        locked[i][8] = True
    for i in range(8):
        locked[8][size - 1 - i] = True
        if i < 7:
            locked[size - 1 - i][8] = True

    # 4. Zigzag data placement + Mask 0 ((r + c) % 2 == 0)
    bit_stream = "".join(f"{cw:08b}" for cw in all_cw)
    idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not locked[r][c]:
                    b = int(bit_stream[idx]) if idx < len(bit_stream) else 0
                    idx += 1
                    grid[r][c] = b ^ (1 if (r + c) % 2 == 0 else 0)
        upward = not upward
        col -= 2

    # 5. Write Format Info (ECC-L + Mask 0 = 0x77C4)
    fmt = 0x77C4
    fmt_bits = [(fmt >> (14 - i)) & 1 for i in range(15)]
    tl_coords = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    br_coords = [
        (size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
        (size - 5, 8), (size - 6, 8), (size - 7, 8),
        (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
        (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1),
    ]
    for i, bit in enumerate(fmt_bits):
        r1, c1 = tl_coords[i]
        r2, c2 = br_coords[i]
        grid[r1][c1] = bit
        grid[r2][c2] = bit

    # Add 2-module white quiet zone
    q = 2
    padded = [[0] * (size + 2 * q) for _ in range(size + 2 * q)]
    for r in range(size):
        for c in range(size):
            padded[r + q][c + q] = grid[r][c]
    return padded


def print_qr(text: str) -> None:
    padded = generate_qr_matrix(text)
    if not padded:
        return
    chars = {(0, 0): "█", (0, 1): "▀", (1, 0): "▄", (1, 1): " "}
    for r in range(0, len(padded), 2):
        top = padded[r]
        bot = padded[r + 1] if r + 1 < len(padded) else [0] * len(padded)
        print("  " + "".join(chars[(t, b)] for t, b in zip(top, bot)))


def render_qr_svg(text: str) -> bytes:
    padded = generate_qr_matrix(text)
    dim = len(padded)
    rects = [
        f'<rect x="{c}" y="{r}" width="1" height="1"/>'
        for r, row in enumerate(padded)
        for c, val in enumerate(row)
        if val == 1
    ]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{dim}" height="{dim}" fill="#ffffff"/>'
        f'<g fill="#0f172a">{"".join(rects)}</g>'
        f"</svg>"
    )
    return svg.encode()


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception: # noqa
        return "127.0.0.1"
    finally:
        s.close()


class LocalDropHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    wbufsize = 524288  # Buffer 512 KB before flushing to the OS TCP stack

    def log_message(self, format: str, *args) -> None:
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def finish(self) -> None:
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _server_url(self) -> str:
        addr = self.server.server_address
        port = addr[1] if isinstance(addr, tuple) else 3000
        return f"http://{get_local_ip()}:{port}"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        # 1. Serve Web UI (Never cache)
        if parsed.path == "/":
            content = load_index_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header(
                "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
            )
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)

        # 2. Vector QR Code for Quick Mobile Scan
        elif parsed.path == "/qr.svg":
            svg_bytes = render_qr_svg(self._server_url())
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(svg_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(svg_bytes)

        # 3. SSE Stream for Peer Discovery & Transfer Notifications
        elif parsed.path == "/events":
            params = urllib.parse.parse_qs(parsed.query)
            peer_id = params.get("id", [None])[0]
            if not peer_id:
                self.send_error(400, "Missing peer id")
                return

            msg_queue: queue.Queue[str | None] = queue.Queue()
            session = {"name": "", "queue": msg_queue, "active": True}

            with peers_lock:
                old = peers.get(peer_id)
                if old:
                    old["active"] = False
                    old["queue"].put(None)
                    session["name"] = old["name"]
                else:
                    used = {info["name"] for info in peers.values() if info["active"]}
                    session["name"] = generate_name(used)
                peers[peer_id] = session

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            msg_queue.put(
                json.dumps(
                    {
                        "type": "welcome",
                        "id": peer_id,
                        "name": session["name"],
                        "url": self._server_url(),
                    }
                )
            )
            broadcast_peers()

            try:
                while session["active"]:
                    try:
                        data = msg_queue.get(timeout=2)
                        if data is None:
                            break
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                should_broadcast = False
                with peers_lock:
                    if peers.get(peer_id) is session:
                        peers.pop(peer_id, None)
                        should_broadcast = True
                if should_broadcast:
                    broadcast_peers()

        # 4. Direct HTTP Download Pipe (Receiver -> Android Download Manager / Browser)
        elif parsed.path == "/download":
            params = urllib.parse.parse_qs(parsed.query)
            tid = params.get("tid", [None])[0]
            if not tid:
                self.send_error(400, "Missing transfer id")
                return

            with transfers_lock:
                tx = transfers.get(tid)

            if not tx or tx["cancelled"]:
                self.send_error(404, "Transfer not found or cancelled")
                return

            safe_ascii = tx["name"].encode("ascii", "ignore").decode() or "download"
            encoded_name = urllib.parse.quote(tx["name"])

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{encoded_name}',
            )
            if tx["size"] > 0:
                self.send_header("Content-Length", str(tx["size"]))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            q = tx["queue"]
            bytes_sent = 0
            try:
                while True:
                    if tx["cancelled"]:
                        self.close_connection = True
                        break
                    try:
                        chunk = q.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    if chunk is None:
                        break

                    self.wfile.write(chunk)
                    bytes_sent += len(chunk)

                if not tx["cancelled"]:
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
                abort_transfer(
                    tid, notify_peer=tx["from"], reason="Receiver cancelled download."
                )
            finally:
                with transfers_lock:
                    transfers.pop(tid, None)
                if tx["cancelled"] or bytes_sent != tx["size"]:
                    self.close_connection = True
                else:
                    send_to_peer(
                        tx["to"],
                        {"type": "transfer-done", "tid": tid, "name": tx["name"]},
                    )

        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        # 1. Direct HTTP Upload Pipe (Sender -> Server Queue -> Receiver)
        if parsed.path == "/upload":
            params = urllib.parse.parse_qs(parsed.query)
            tid = params.get("tid", [None])[0]
            sender_id = params.get("from", [None])[0]
            target_id = params.get("to", [None])[0]
            filename = params.get("name", ["file"])[0]
            file_size = int(self.headers.get("Content-Length", 0))

            if not tid or not sender_id or not target_id:
                self.send_error(400, "Missing transfer parameters")
                return

            with peers_lock:
                if target_id not in peers or sender_id not in peers:
                    self.send_error(404, "Target device disconnected")
                    return
                sender_name = peers[sender_id]["name"]

            tx = {
                "queue": queue.Queue(maxsize=64),
                "name": filename,
                "size": file_size,
                "cancelled": False,
                "from": sender_id,
                "to": target_id,
            }
            with transfers_lock:
                transfers[tid] = tx

            # Tell receiver's browser to start native HTTP download immediately
            send_to_peer(
                target_id,
                {
                    "type": "incoming-file",
                    "tid": tid,
                    "name": filename,
                    "size": file_size,
                    "senderName": sender_name,
                },
            )

            remaining = file_size
            try:
                while remaining > 0:
                    if tx["cancelled"]:
                        self.close_connection = True
                        return

                    to_read = min(CHUNK_SIZE, remaining)
                    chunk = self.rfile.read(to_read)
                    if not chunk:
                        abort_transfer(
                            tid,
                            notify_peer=target_id,
                            reason=f"{sender_name} cancelled.",
                        )
                        self.close_connection = True
                        return

                    remaining -= len(chunk)

                    while not tx["cancelled"]:
                        try:
                            tx["queue"].put(chunk, timeout=0.5)
                            break
                        except queue.Full:
                            continue

                if not tx["cancelled"]:
                    tx["queue"].put(None)  # EOF sentinel
                    resp = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
            except (BrokenPipeError, ConnectionResetError):
                abort_transfer(
                    tid, notify_peer=target_id, reason=f"{sender_name} disconnected."
                )
                self.close_connection = True

        # 2. Cancel Endpoint
        elif parsed.path == "/cancel":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            tid = body.get("tid")
            who = body.get("by")

            if tid:
                with transfers_lock:
                    tx = transfers.get(tid)

                if tx:
                    other_peer = tx["to"] if who == tx["from"] else tx["from"]
                    abort_transfer(
                        tid,
                        notify_peer=other_peer,
                        reason="Transfer cancelled by other device.",
                    )

            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        else:
            self.send_error(404)


if __name__ == "__main__":
    PORT = 3000
    hostname = socket.gethostname()
    local_url = f"http://{get_local_ip()}:{PORT}"
    server = ThreadingHTTPServer(("0.0.0.0", PORT), LocalDropHandler)

    print("\nLocalDrop (Direct HTTP Streaming) is running!")
    print(f"Open on this PC:       http://localhost:{PORT}")
    print(f"Open on other devices: {local_url}  (or http://{hostname}.local:{PORT})\n")
    print_qr(local_url)
    print("\nScan the QR code above with your phone camera to connect.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
