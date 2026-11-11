# iyiCAM in a browser (`iyicam serve`)

A small web server with a page and a **key of its own**. It is a program by itself: CNC-Assistant is not needed, and it
needs none of the files of the computer it runs on (the drawing is opened from the phone and the G-code is downloaded
to it). Everything is the [service](service.md) behind a web page.

```bash
python -m pip install -e .    # in a checkout of this repository; no Qt needed
iyicam serve --host 0.0.0.0   # the computer and every phone on the same Wi-Fi
```

(On Windows `packaging\web_windows.bat` does both. For a Linux server see [A server](#a-server-linux-systemd-and-https).)

```
iyiCAM web service on port 8300
  this computer:        http://localhost:8300
  phone on the same Wi-Fi: http://192.168.1.20:8300
  key file: C:\Users\you\.iyicam\server.key
  key:      ABCDEF-GHJKMN-PQRSTU-VWXYZ2
```

Open the address on the phone and type the key. The first time Windows asks whether Python may talk on the network:
allow it for *private* networks.

| | |
|---|---|
| `iyicam serve` | listens on this computer only (`127.0.0.1`), port 8300 |
| `--host 0.0.0.0` | on every network of the computer (what a phone needs) |
| `--port N` | another port (0 = any free one) |
| `iyicam serve --show-key` | prints the key and stops |
| `iyicam serve --new-key` | makes a new key; **every signed-in browser is signed out at once**, also in a running server |
| `--trust-proxy` | behind a reverse proxy (below) |
| `--max-upload-mb N` | largest request, default 64 (a drawing travels as base64) |
| `--quiet` | no line per request |

User posts, tools and plugins are read from `~/.iyicam` (`IYICAM_HOME`), as in the program; `--no-plugins` or
`IYICAM_SERVICE_PLUGINS=0` leaves plugins out.

## What the page does

1. **Project** — open a drawing (`.icam`, DXF, SVG, HPGL, CorelDRAW `.cdr`, whatever the importers read), see the report
   of what was read, the sheets, the material (thickness, safe Z, plunge clearance, Z zero, XY origin) and the layers.
   *Save as .icam* downloads the project; it opens again in the desktop program or here.
2. **Drawing** — the sheet and its vectors, pan with one finger, zoom with two (or the wheel); after *Calculate* the
   toolpaths are drawn over it, coloured by depth, with the rapid moves dashed and drilled holes as dots.
3. **Toolpaths** — add, edit, switch off or delete toolpaths. The form is built from the engine's own description of the
   operation (profile, pocket, drill, engrave, V-carve, plugin operations too), so it offers exactly the settings the
   desktop program has.
4. **Output** — *Calculate* (time, length and warnings per toolpath), *Write G-code* for the post you pick (view it or
   download it), *Simulation* (the material-removal picture of the sheet) and *Export* (DXF, SVG, `.icam`).

The project lives in the browser (and is kept in its local storage, so a reload does not lose it); the server remembers
nothing between requests. What is not in the page yet — moving, rotating or editing vectors, nesting — is there in the
service (`command`, `nest`) and in the desktop program.

## The key and what protects it

* The key is in `~/.iyicam/server.key`, made at the first start: 24 letters and digits (about 120 bits), without
  `0 O 1 I`. Dashes, spaces and case do not matter when it is typed.
* The page asks for it once. The server answers with a session cookie (12 hours, `HttpOnly`, `SameSite=Strict`, and
  `Secure` behind an HTTPS proxy). Without it nothing but the page itself is reachable.
* An address that gives 10 wrong keys within 5 minutes waits (HTTP 429, `Retry-After`).
* Only the operations of the service can be called (`/api/<operation>`); nothing on the computer's disk is served, and
  no path from a request is ever turned into a file name. A request that a page of another site sent is refused.
* The server speaks **plain HTTP**. On a home network that is acceptable; the key and the drawings can be read by anyone
  who can listen on that network. For anything else put HTTPS in front (below).
* Whoever has the key can use the engine of this computer (CPU time, memory), not its files. Keep it private; a new key
  (`--new-key`) is one command.

## A server (Linux, systemd and HTTPS)

The engine needs no files of the computer, so the service can run on **any** machine, a small Linux server for
example, and be reached from anywhere. It is its own service: it needs nothing of the CNC-Assistant, and it can run
on the same server next to it (other port, other host name).

```bash
git clone <this repository> /opt/iyicam        # not under /root or /home: the service user must read it
sudo bash /opt/iyicam/packaging/linux/install.sh
```

`packaging/linux/install.sh` finds Python 3.11 or newer (Debian 12 and Ubuntu 24.04 have it; the script installs it
with apt when it is missing), makes the service user `iyicam` and a venv in the checkout, installs iyiCAM (core only,
no Qt), and installs and starts the systemd service **`iyicam-web`**, which also starts at boot. It is safe to run
again: after a `git pull` the same command updates it. Settings are environment variables:
`sudo IYICAM_PORT=8301 bash ...` (port, default 8300), `IYICAM_HOST` (address, default `127.0.0.1`), `PYTHON`
(for example `python3.12`).

| | |
|---|---|
| the key | `sudo cat /var/lib/iyicam/server.key` (made at the first start; this is the key typed on the page) |
| a new key | `sudo -u iyicam IYICAM_HOME=/var/lib/iyicam /opt/iyicam/venv/bin/iyicam serve --new-key` (signed-in browsers are signed out at once) |
| status, log | `systemctl status iyicam-web`, `journalctl -u iyicam-web -f` |
| more memory | the service is limited to 2 GB; `systemctl edit iyicam-web` and set `MemoryMax=` |
| remove | `sudo bash packaging/linux/uninstall.sh` (`--full` also deletes the key, the venv and the user) |

By default the service listens on `127.0.0.1:8300` **only** and runs with `--trust-proxy`; a reverse proxy in front adds
HTTPS (the page needs a **host name of its own**, `iyicam.example.com` for example, because it uses root paths). Ready
to copy, with the headers set the way `--trust-proxy` needs them:

* `packaging/linux/Caddyfile.example` — Caddy (the shortest: certificate included)
* `packaging/linux/nginx.conf.example` — nginx (+ certbot)
* `packaging/linux/haproxy.cfg.example` — HAProxy (a host rule and a backend for an existing frontend)

`--trust-proxy` makes the server believe `X-Forwarded-For` / `X-Forwarded-Proto`, so use it only when the server can be
reached through the proxy alone (it listens on `127.0.0.1`). Without it a client could name any address to escape the
limit on wrong keys. For that reason the installer leaves `--trust-proxy` off when `IYICAM_HOST` is another address
(`0.0.0.0`, say), and then the service speaks plain HTTP on that network.

When something is wrong: `curl http://127.0.0.1:8300/health` answers `ok` on the server itself; through the proxy
`curl https://iyicam.example.com/health` does too. A page that loads but answers "the request came from another site"
(403 `bad_origin`) when the key is typed means the proxy replaced the `Host` header: the page's address and the `Host`
the service sees must be the same (nginx needs `proxy_set_header Host $host;`, as the example has).

## For programs

The same answers as `ServiceHost.handle`, as HTTP: `POST /api/<operation>` with the payload as a JSON object
(`Content-Type: application/json`) after `POST /api/login` `{"key": "..."}`; the session cookie goes with every call.
A service error is `{"error": code, "message": ...}` with status 400 (404 for an unknown operation or job, 409 for a
job that has not finished, 503 for `too_busy`). `GET /health` answers `ok` without a key (for a proxy or a monitor).
Tests: `tests/test_server.py`.
