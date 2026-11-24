# LocalDrop

> Fast, zero-dependency local file sharing over Wi-Fi or mobile hotspot using Python and direct HTTP streaming—with instant QR pairing and no browser permission prompts.

LocalDrop lets you seamlessly transfer files between laptops, desktops, and mobile devices (Android/iOS) over your local network at full hardware speeds. No internet access, external services, user accounts, browser extensions, or mobile apps required.

---

## Key Features

- **Zero External Dependencies:** Built entirely with Python 3's standard library (`http.server`, `socket`, `threading`, `zipapp`) and lightweight native Web APIs.
- **Direct HTTP Streaming:** Files stream directly from the sender's browser into the receiver's native Download Manager via 512 KB memory buffers, bypassing memory limits on large multi-gigabyte transfers.
- **Instant QR Pairing:** Renders a clean Unicode QR code directly in the terminal and a crisp vector SVG QR code in the Web UI for instant phone camera scanning without typing changing IP addresses.
- **Permission-Friendly Mobile UX:** Uses standard HTML5 file pickers without triggering Android camera or microphone permission intents.
- **Bi-Directional Cancellation:** Cancel active transfers at any time from either sender or receiver with clean TCP connection teardowns.
- **Single-File Portable Executable:** Can be run from raw source code or packaged into a single executable `.pyz` ZIP archive for drag-and-drop portability.

---

## Requirements

- **Host PC:** Python 3.10+ (Linux, macOS, or Windows)
- **Receiving / Sending Devices:** Any modern web browser (Chrome, Brave, Firefox, Safari) on the same Wi-Fi network or hotspot.

---

## Quick Start

### Running from Source

1. **Clone the repository:**
   ```bash
   git clone https://github.com/<your-username>/localdrop.git
   cd localdrop
   ```

2. **Start the server:**
   ```bash
   python3 server.py
   ```

3. **Connect devices:**
   Scan the terminal QR code using your smartphone camera or navigate to `http://<your-local-ip>:3000` in your browser.

---

### Building & Running a Portable `.pyz` Archive

Package `server.py` and `index.html` into a single standalone executable archive:

```bash
mkdir build
cp server.py build/__main__.py
cp index.html build/index.html
python3 -m zipapp build -o localdrop.pyz -p "/usr/bin/env python3"
chmod +x localdrop.pyz
```

- **Linux / macOS:** `./localdrop.pyz`
- **Windows:** Double-click `localdrop.pyz` or run `py localdrop.pyz`

---

## ⚡ Performance & Transfer Speed Tips

To achieve maximum transfer speeds (often **30–80+ MB/s** depending on your network card):

- **Switch Phone Hotspot to 5 GHz:** 
  If sharing via a mobile hotspot, change your AP Band setting from **2.4 GHz** to **5 GHz** (*Settings → Portable Hotspot → Set up hotspot → Select AP Band → 5 GHz*). This eliminates the ~5–10 MB/s bottleneck of 2.4 GHz Wi-Fi.
- **Line-of-Sight Proximity:** 
  5 GHz signals have high bandwidth but shorter range and higher wall attenuation. Keep devices in the same room while sending large files.
- **Direct PC-to-Phone Speed:** 
  LocalDrop uses 512 KB I/O socket buffering and direct kernel streaming so Python isn't a bottleneck—your Wi-Fi hardware and storage writes are the only speed constraints.

---

## Firewall Setup

Ensure TCP port `3000` is open on the host machine:

- **Linux (UFW):**
  ```bash
  sudo ufw allow 3000/tcp
  ```
- **Windows:** Allow Python through **Windows Defender Firewall** for **Private networks** when prompted on first launch.

---

## How It Works

1. **Peer Discovery (`GET /events`):** Active tabs connect via Server-Sent Events (SSE) and receive auto-assigned identifiers (e.g., *Swift Falcon*) while broadcasting presence across all connected peers.
2. **Direct Pipe Upload (`POST /upload`):** The sender streams binary data via `XMLHttpRequest` directly into an in-memory queue on the server.
3. **Native Download (`GET /download`):** The receiving device initiates a native download via an invisible target `<iframe>`, feeding data straight to device storage as chunks arrive.

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
