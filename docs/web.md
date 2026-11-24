# iyiCAM in a browser (`iyicam serve`)

The web app is the desktop program's interface (menus, tool bars, docks, the drawing) in a browser, and **the browser does
all the CAM work**: iyiCAM's own Python package runs inside the page, in a Web Worker, with [Pyodide](https://pyodide.org)
(numpy and pyclipper from Pyodide, ezdxf and svgelements from PyPI). It gives the same toolpaths and the same G-code text as
the desktop program (about half the speed). Nothing of a drawing is calculated on a server.

```
 browser (Chrome, Edge, Firefox, Safari: phone or desktop)
   page + engine (Pyodide in a Web Worker: iyicam.service.ops)         <- the CAM
        |  /app/..., /api/...
 iyicam serve   (static app, iyicam.zip, key, files API)               <- no CAM
        ^  the agent connects OUT (long poll), so it works behind NAT
 iyicam agent   (list / read / write files inside its roots)           <- no CAM
```

* **`iyicam serve`** serves the app (`iyicam/webapp/`) at `/app/`, the Python package for the worker (`/app/py/iyicam.zip`,
  built from the installed package without `gui`, `web`, `webapp`, `share`), signs people in with the **key** and passes file
  requests between the browser and the agent. With `--local` it shares folders of its own computer instead
  (`--root DIR`, repeatable).
* **`iyicam agent`** runs on the workshop PC and shares the folders it is given. Several browsers may use one agent.
* Without an agent and without `--local` the app still works: it opens and saves files of the device itself (file picker,
  download).

```bash
python -m pip install -e .                                    # no Qt needed for serve / agent
iyicam serve --local --root ~/jobs                            # one computer: http://localhost:8300/app/
iyicam serve --show-key                                       # the key to type in the page

iyicam serve --host 0.0.0.0                                   # a server (put HTTPS in front of it: --trust-proxy)
iyicam agent --server https://iyicam.example.com --key ABCDEF-... --root ~/jobs    # on the workshop PC
```

Options: `serve [--host --port --local --root DIR ... --pyodide DIR --show-key --new-key --trust-proxy --max-file-mb --quiet]`,
`agent --server URL --key KEY [--root DIR ...] [--name --max-file-mb --quiet]` (the key can also be in `IYICAM_KEY`).
`--pyodide DIR` serves a Pyodide 0.27.7 "full" folder (and the wheels of ezdxf, svgelements, pyparsing, typing_extensions,
fonttools put beside it) instead of the jsDelivr CDN and PyPI, for a workshop without internet.

**Security.** One key (24 letters and digits, `~/.iyicam/server.key`; `--new-key` replaces it and signs everybody out).
A browser signs in with it and gets a cookie (`HttpOnly`, `SameSite=Strict`); after ten wrong keys an address waits.
The agent uses the same key as a bearer token, so over the internet use HTTPS (the agent warns about plain HTTP). Files are
reached only through the roots: virtual paths (`jobs/a.icam`), no `..`, no hidden names, no link that leads out, only the kinds
of file the app uses are read, programs and scripts are never written, 64 MB at most. The pages are cross-origin isolated
(COOP/COEP) with a CSP; the server does no CAM and keeps no file.

Details: the HTTP API, the agent protocol, cancelling, loading time and the self-hosted Pyodide are in
[webapp/server.md](webapp/server.md); the interface in [webapp/](webapp/) (`CONTRACT.md`). The operations the engine
answers are those of [service.md](service.md).
