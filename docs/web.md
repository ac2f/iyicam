# iyiCAM in a browser (`iyicam serve`)

A small web server with a page and a **key of its own**. It is a program by itself: CNC-Assistant is not needed. A
drawing can be opened from the phone and the G-code downloaded to it, and the page can also **browse, open and save
files of the computer the server runs on**, inside the folders you name ([below](#files-of-the-computer)). Everything
is the [service](service.md) behind a web page.

When the server is **not** the computer with the drawings and the machine's folders (a small server on the internet,
the workshop PC at home), put a **hub** on the server and an **agent** on the workshop PC: the page is the same, but
the engine and the files are those of the PC ([a hub and an agent](#a-hub-and-an-agent-the-server-somewhere-else-the-files-here)).

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
| `--root FOLDER` | a folder in which the page may open and save files; repeat it for more (default: `IYICAM_ROOTS`, else the home folder) |
| `--no-files` | the page cannot open or save files of this computer at all (it can still upload and download) |
| `--hub` | a hub: no engine and no files here, agents on other computers have them ([below](#a-hub-and-an-agent-the-server-somewhere-else-the-files-here)) |
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

## Files of the computer

Besides uploading from the phone, the page can look into the folders of the computer the server runs on: **Open from
the computer…** (Project tab) opens a drawing or a project from there, **Save to the computer…** writes the project as
`.icam` (the same file again with *Save*, and the page notices when somebody changed the file meanwhile), and on the
Output tab the G-code files and the exports can be written into a folder instead of being downloaded. A name that is
taken is never replaced without asking. The file window has three modes (open, save as, choose a folder) and remembers
the last folder.

It is meant for the computer in the workshop: the drawings are on it, and the G-code goes where the machine's program
reads it. What it may touch is limited on purpose:

* only **inside the roots**: the folders named with `--root` (repeat it), or `IYICAM_ROOTS` (separated like `PATH`:
  `;` on Windows, `:` elsewhere), or, with neither, the home folder of the user the server runs as. A path is turned into
  its real path first, so a `..` or a symbolic link cannot lead out;
* never anything below a **hidden** name (starting with a dot: `.ssh`, `.iyicam`), and never the folder in which iyiCAM
  keeps its own state (the key, the user's posts and tools) — unless a root was put *inside* it on purpose, which is
  what the Linux installation does;
* only known kinds of files: drawings and projects (`.icam .dxf .svg .plt .hpgl .hpg .cdr`) are listed and opened;
  `.icam`, `.dxf`, `.svg`, `.png`, the G-code extensions (`.nc .tap .ngc .cnc .gcode .mpf .txt`) and the extension of any
  post are written. **Never** a program or a script (`.exe .bat .ps1 .py .sh .js …`), and a file name is only a name,
  never a path;
* a file that exists is replaced only when the page saw its latest state (a hash of it) or the person says so; every
  write is made whole or not at all;
* `--no-files` turns all of this off. Whoever has the key can reach the files of the roots, so keep the roots to the
  folders that are meant for this.

On a Windows computer at home the default (the home folder) is what the CNC-Assistant agent does too. For a server name
the folders: `iyicam serve --root D:\Cam --root D:\Gcode`.

The operations are `status`, `list`, `open`, `save` and `save_files` under `/api/` (after the login, like the others).
They are the same, with the same answers, as the ones CNC-Assistant's mobile page calls on its agent
(`/api/cam/list` and so on), so a page written for one works with the other; the code is this program's own, and the
two projects share nothing but that interface (no code, no dependency). `status` answers
`{"files": true, "roots": [...], "limits": {...}}` (`"files": false` with `--no-files`); `list` takes `{"path": ...}`
(without it: the roots) and answers the folders and files of that folder with a `parent` to go up; `open` takes `path`
(and `thickness`, or `project` to add a drawing to a project) and answers like `import` plus `path`, `hash`, `kind`;
`save` takes `path`, `project` and `base_hash` (or `overwrite`); `save_files` takes `folder` and `files`
(`{name, text}` or `{name, data_b64}`). Errors: `path_not_allowed` (HTTP 403), `folder_not_found` and `file_not_found`
(404), `file_exists` and `file_changed` (409), `file_too_large` (413), `unsupported_file`, `invalid_name`, `invalid_path`
(400), `files_disabled` (403). Tests: `tests/test_files.py`.

## A hub and an agent (the server somewhere else, the files here)

`iyicam serve` works with the files of the computer it runs on. A server on the internet is not that computer: the
drawings are on the workshop PC and the G-code has to go where the machine's program reads it. So the work is split
in two programs of iyiCAM (nothing of CNC-Assistant is needed):

```
phone ──HTTPS──▶  hub (iyicam serve --hub)  ◀──HTTPS, outward──  agent (iyicam agent) on the workshop PC
                  the page, the keys, the queue                    the engine and the files of that PC
```

* the **hub** runs on the server. It has no engine and no disk of its own: it serves the page, checks the keys and
  passes each call of the page on to the agent;
* the **agent** runs on the PC, in the background. It connects *outward* to the hub (so nothing has to reach into the
  workshop: no open port, no tunnel), waits for calls, does them with the engine and the files of the PC and sends the
  answers back. The answers are the same as `iyicam serve` gives (the folders it may touch are the same rules as
  [above](#files-of-the-computer): `--root`, never a hidden folder, never a program).

Two keys, never mixed up:

| | made by | kept in | what it does |
|---|---|---|---|
| **server key** | the hub, at its first start | `server.key` of the hub (`sudo cat /var/lib/iyicam/server.key`) | only lets an **agent** register; it is typed once on the PC, **never on the page**, and it does not sign a browser in |
| **client key** | the agent, on the PC | `agent.json` of the PC (`iyicam agent --show-key`) | what the **page** asks for; it belongs to that PC and opens that PC only |

**On the server** (Linux, as in [A server](#a-server-linux-systemd-and-https)):

```bash
git pull && sudo IYICAM_HUB=on bash packaging/linux/install.sh
sudo cat /var/lib/iyicam/server.key        # the server key, for the agent
```

**On the PC** (Windows; the script makes its own `.venv`, needs the 64-bit Python 3.11+ of python.org):

```
packaging\agent_windows.bat -ServerUrl https://hub.example.com -ServerKey ABCDEF-GHJKMN-PQRSTU-VWXYZ2
```

This keeps the settings, checks them at the hub (nothing is kept when the hub does not take them), starts the agent
without a window and prints the **client key**. The phone opens the address of the hub and signs in with that client
key; the file window of the page then shows the folders of the PC. Later:

```
packaging\agent_windows.bat             starts it if it is not running; shows whether it is connected, and the client key
packaging\agent_windows.bat -Stop       stops it
packaging\agent_windows.bat -Restart    stops it and starts it again
packaging\agent_windows.bat -NewKey     a new client key (the old one stops working at once, signed-in phones are signed out)
```

Anywhere else (Linux or macOS PC): `iyicam agent --server-url https://hub.example.com --server-key KEY`, which runs in
the console (put it in a service of your own to keep it in the background).

| `iyicam agent` | |
|---|---|
| `--server-url URL`, `--server-key KEY` | the hub and its server key (kept in `agent.json`, so later runs need nothing; the key may also come from `IYICAM_SERVER_KEY`) |
| `--setup` | keep the settings, register at the hub (this checks the address and the key), print the client key and stop |
| `--show-key` | print the client key and stop |
| `--new-key` | a new client key, registered at the hub; the old one stops working at once (a running agent needs no restart) |
| `--root FOLDER` | a folder of this PC the page may open and save files in (repeat it; kept; default `IYICAM_ROOTS`, else the home folder) |
| `--no-files` | the page cannot open or save files of this PC (kept) |
| `--name NAME` | the name of this PC as the hub lists it (default the computer name) |

What to know:

* the agent has no window; it writes what it does to `agent.log` and its state to `agent.state.json` in `~/.iyicam`
  (`IYICAM_HOME`), never a key. A phone that signs in while the agent is not running is told so ("the agent is not
  connected") and can try again when it is; the hub counts an agent as gone when it has not been heard of for 60 s;
* the settings file `agent.json` holds the server key and the client key (readable by the user only); the hub keeps
  only a digest of each client key (`agents.json`), so a copy of the hub's state does not open any PC;
* a restart of the hub ends the sessions (a phone signs in again, with the same client key: the hub remembers its
  agents) and the agents come back on their own; a new server key (`iyicam serve --new-key`) has to be given to the
  agents (`iyicam agent --server-key NEW`);
* one call waits at most 5 minutes for its answer, a drawing travels through the hub like through `iyicam serve`
  (`--max-upload-mb`, default 64), and an answer larger than that is refused with a message instead of lost;
* the page, the proxy (`--trust-proxy`, HTTPS) and the protections against wrong keys are those of the plain server; the
  agent's own calls need the server key and are slowed down the same way after 10 wrong ones.

For programs: the agent's calls are `POST /agent/register` (`agent_id`, `token` = the client key, `name`), `POST
/agent/poll` (`agent_id`; held open for up to 25 s; answers `{"job": {"id", "op", "payload"}}` or `{}`) and `POST
/agent/reply` (`agent_id`, `job_id`, `status`, `out`), all JSON with the header `X-Server-Key`. Errors: `bad_server_key`
(403), `invalid_agent` / `invalid_client_key` (400), `key_taken` (409), `unknown_agent` (409: register again). A browser's
call that the hub cannot pass on answers `agent_offline` (503), `agent_timeout` (504) or `too_busy` (503). Tests:
`tests/test_hub.py`.

## The key and what protects it

(For a plain `iyicam serve`; a hub has the two keys above.)

* The key is in `~/.iyicam/server.key`, made at the first start: 24 letters and digits (about 120 bits), without
  `0 O 1 I`. Dashes, spaces and case do not matter when it is typed.
* The page asks for it once. The server answers with a session cookie (12 hours, `HttpOnly`, `SameSite=Strict`, and
  `Secure` behind an HTTPS proxy). Without it nothing but the page itself is reachable.
* An address that gives 10 wrong keys within 5 minutes waits (HTTP 429, `Retry-After`).
* Only the operations of the service and the file operations can be called (`/api/<operation>`); the page itself is
  served from a fixed list of files, never from a path of the request. The disk is reachable only through the file
  operations and only inside the roots (above); `--no-files` closes that. A request that a page of another site sent is
  refused.
* The server speaks **plain HTTP**. On a home network that is acceptable; the key and the drawings can be read by anyone
  who can listen on that network. For anything else put HTTPS in front (below).
* Whoever has the key can use the engine of this computer (CPU time, memory) and the files in the roots (not others).
  Keep it private; a new key (`--new-key`) is one command.

## A server (Linux, systemd and HTTPS)

The engine needs no files of the computer (the project travels as data), so the service can run on **any** machine, a
small Linux server for example, and be reached from anywhere. It is its own service: it needs nothing of the
CNC-Assistant, and it can run on the same server next to it (other port, other host name).

```bash
git clone <this repository> /opt/iyicam        # not under /root or /home: the service user must read it
sudo bash /opt/iyicam/packaging/linux/install.sh
```

`packaging/linux/install.sh` finds Python 3.11 or newer (Debian 12 and Ubuntu 24.04 have it; the script installs it
with apt when it is missing), makes the service user `iyicam` and a venv in the checkout, installs iyiCAM (core only,
no Qt), and installs and starts the systemd service **`iyicam-web`**, which also starts at boot. It is safe to run
again: after a `git pull` the same command updates it. Settings are environment variables:
`sudo IYICAM_PORT=8301 bash ...` (port, default 8300), `IYICAM_HOST` (address, default `127.0.0.1`), `PYTHON`
(for example `python3.12`), `IYICAM_ROOTS` (the folders of [the files of the computer](#files-of-the-computer),
separated by `:`, default `/var/lib/iyicam/files`, which the installer makes), `IYICAM_FILES=off` (no file access at
all) and `IYICAM_HUB=on` (a [hub](#a-hub-and-an-agent-the-server-somewhere-else-the-files-here) instead of a server
with files of its own: the engine and the files are those of the agents).

The service runs as the user `iyicam` with the system read-only to it, so a folder of your own in `IYICAM_ROOTS` must
be writable by that user (`chown iyicam: /srv/cam`, or `setfacl -m u:iyicam:rwx /srv/cam`; the installer makes a missing
one and warns about one that cannot be written) and it must not be under `/home` or `/root` (the service cannot see
those; `systemctl edit iyicam-web` with `ProtectHome=no` lifts that). Another folder can be added later by editing
`IYICAM_ROOTS` and `ReadWritePaths` in the unit, or simply by running the installer again with the new list.

| | |
|---|---|
| the key | `sudo cat /var/lib/iyicam/server.key` (made at the first start; this is the key typed on the page) |
| a new key | `sudo -u iyicam IYICAM_HOME=/var/lib/iyicam /opt/iyicam/venv/bin/iyicam serve --new-key` (signed-in browsers are signed out at once) |
| the files of the page | in `/var/lib/iyicam/files` (or your `IYICAM_ROOTS`); take G-code from there, or let the machine's folder be a root |
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
