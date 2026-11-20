"""Verity's harness: a local web UI to start runs and watch the agent work.

    python -m verity serve      ->  http://127.0.0.1:8765

One run at a time (the agent has one browser). By default the server only
listens on this machine. To let other people reach it, start it with an access
code - everyone then signs in before they can see or run anything:

    python -m verity serve --access-code auto
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .agent import OUTPUT_DIR, run
from .excel_io import read_input, write_template
from .schema import INPUT_COLUMNS
from .track import STEPS, WAITING, Tracker

_HERE = Path(__file__).parent
INPUT_DIR = _HERE / "input"
_PREVIEW = ["s_no", "full_name", "license_type", "license_number", "agency_url"]


class RunState(Tracker):
    """Tracker that keeps the whole track in memory for the UI and saves it at the end."""

    def __init__(self, run_id, input_name, out_dir):
        super().__init__(echo=lambda *_: None)
        self.lock = threading.Lock()
        self.id, self.input, self.out_dir = run_id, input_name, out_dir
        self.status, self.error = "running", ""
        self.started, self.finished = datetime.now().strftime("%m/%d/%Y %H:%M:%S"), ""
        self.rows, self.events, self.files = [], [], {}
        self.live_ts = 0
        self.live_path = out_dir / "live.jpg"
        # Sheets come from other people here, so a "saved page" may only be one of Verity's own files.
        self.local_roots = [_HERE / "samples", OUTPUT_DIR]

    def _event(self, msg, row=None, level="info", step=None):
        self.events.append({"seq": len(self.events) + 1, "t": datetime.now().strftime("%H:%M:%S"),
                            "row": row, "level": level, "step": step, "msg": msg})

    def on_start(self, rows, out_dir):
        with self.lock:
            self.rows = [{"s_no": r["s_no"], "applicant": r["full_name"], "license_type": r["license_type"],
                          "license_number": r["license_number"], "url": r["agency_url"],
                          "steps": {k: {"state": "pending", "detail": ""} for k, _ in STEPS},
                          "status": "", "result": None} for r in rows]

    def on_log(self, msg, row, level):
        with self.lock:
            self._event(msg, row, level)

    def on_step(self, row, key, state, detail):
        with self.lock:
            self.rows[row]["steps"][key] = {"state": state, "detail": detail}
            if state != "skipped":
                label = dict(STEPS)[key]
                level = {"failed": "error", WAITING: "attention"}.get(state, "step")
                self._event(f"{label}: {detail}" if detail else f"{label}...", row, level, key)

    def on_shot(self):
        self.live_ts = int(time.time() * 1000)

    def on_row_result(self, row, result):
        with self.lock:
            self.rows[row]["status"] = result["status"]
            self.rows[row]["result"] = {k: v for k, v in result.items() if k != "raw"}

    def on_finish(self, xlsx, report, results):
        with self.lock:
            self.files = {"xlsx": xlsx.name, "report": report.name}

    def snapshot(self, since=0):
        with self.lock:
            return {"id": self.id, "input": self.input, "status": self.status, "error": self.error,
                    "started": self.started, "finished": self.finished, "rows": self.rows,
                    "files": self.files, "live": self.live_ts, "seq": len(self.events),
                    "events": self.events[since:]}

    def save(self):
        (self.out_dir / "run.json").write_text(json.dumps(self.snapshot(), ensure_ascii=False), encoding="utf-8")


class Harness:
    def __init__(self):
        self.current = None
        self.thread = None
        self.lock = threading.Lock()

    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, input_name, timeout_s):
        with self.lock:
            if self.busy():
                raise HTTPException(409, "A run is already in progress.")
            path = _input_path(input_name)
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = OUTPUT_DIR / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            state = RunState(run_id, path.name, out_dir)
            self.current = state
            self.thread = threading.Thread(target=self._work, args=(state, path, timeout_s), daemon=True)
            self.thread.start()
            return state

    @staticmethod
    def _work(state, path, timeout_s):
        try:
            run(path, state.out_dir, timeout_s, tracker=state)
            state.status = "stopped" if state.cancelled else "finished"
        except Exception as exc:
            state.status, state.error = "failed", str(exc).splitlines()[0] if str(exc) else repr(exc)
            state.on_log(f"Run failed: {state.error}", None, "error")
        state.finished = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
        try:
            state.save()
        except Exception:
            pass


def _input_path(name):
    if not re.fullmatch(r"[\w .()\-]+\.xlsx", name or ""):
        raise HTTPException(400, "Input must be an .xlsx file name.")
    path = INPUT_DIR / name
    if not path.exists():
        raise HTTPException(404, f"{name} not found.")
    return path


def _preview(path):
    rows = read_input(path)
    return {"name": path.name, "count": len(rows), "rows": [{k: r.get(k, "") for k in _PREVIEW} for r in rows]}


def _ensure_demo():
    from .__main__ import _demo_input
    INPUT_DIR.mkdir(exist_ok=True)
    _demo_input(INPUT_DIR / "demo_input.xlsx")


_LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Verity - Sign in</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#10141c;color:#e8ebf1;
  font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:16px}
form{background:#181e2a;border:1px solid #283041;border-radius:10px;padding:28px;width:100%;max-width:340px}
h1{margin:0 0 4px;font-size:22px} p{margin:0 0 18px;color:#98a2b3;font-size:14px}
label{display:block;font-size:13px;color:#98a2b3;margin-bottom:6px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:6px;border:1px solid #283041;background:#10141c;color:inherit;font:inherit}
button{margin-top:14px;width:100%;padding:11px;border:0;border-radius:6px;background:#3b5bdb;color:#fff;font:inherit;font-weight:600;cursor:pointer}
.err{color:#ff8f85;font-size:13.5px;margin:12px 0 0}
</style></head><body><form method="post" action="/login">
<h1>Verity</h1><p>License verification agent</p>
<label for="code">Access code</label><input id="code" name="code" type="password" autocomplete="current-password" autofocus required>
<button type="submit">Sign in</button>__ERROR__</form></body></html>"""


def _install_access_code(app, code):
    """Put the whole harness behind a shared access code (required whenever it is reachable by others)."""
    secret = secrets.token_bytes(32)
    session = hmac.new(secret, code.encode(), hashlib.sha256).hexdigest()
    failures = {}  # client address -> times of recent wrong codes

    def page(error="", status=200):
        html = _LOGIN_PAGE.replace("__ERROR__", f'<div class="err">{error}</div>' if error else "")
        return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/healthz") or hmac.compare_digest(request.cookies.get("verity_session", ""), session):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in required."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    def login_form():
        return page()

    @app.post("/login")
    async def login(request: Request):
        # Behind a host's proxy every visitor shares one socket address; the proxy appends the real one.
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        client = request.headers.get("cf-connecting-ip") or forwarded or             (request.client.host if request.client else "?")
        recent = [t for t in failures.get(client, []) if time.time() - t < 600]
        if len(recent) >= 8:
            return page("Too many attempts. Try again in a few minutes.", 429)
        given = parse_qs((await request.body()).decode("utf-8", "replace")).get("code", [""])[0]
        if hmac.compare_digest(given.encode(), code.encode()):
            failures.pop(client, None)
            response = RedirectResponse("/", status_code=303)
            secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
            response.set_cookie("verity_session", session, httponly=True, samesite="lax", secure=secure,
                                max_age=12 * 3600)
            return response
        failures[client] = recent + [time.time()]
        await asyncio.sleep(1)
        return page("That access code is not correct.", 401)


def create_app(access_code=None):
    app = FastAPI(title="Verity harness", docs_url=None, redoc_url=None, openapi_url=None)
    harness = Harness()
    if access_code:
        _install_access_code(app, access_code)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}
    OUTPUT_DIR.mkdir(exist_ok=True)
    _ensure_demo()

    @app.get("/")
    def index():
        return FileResponse(_HERE / "harness.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/meta")
    def meta():
        return {"steps": STEPS, "columns": [h for _, h, _ in INPUT_COLUMNS]}

    @app.get("/api/inputs")
    def inputs():
        files = sorted(INPUT_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files if not p.name.startswith(("~$", "_"))]

    @app.get("/api/inputs/{name}")
    def input_preview(name: str):
        try:
            return _preview(_input_path(name))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/inputs")
    async def upload(request: Request, name: str):
        name = re.sub(r"[^\w .()\-]", "_", Path(name).name)
        if not name.lower().endswith(".xlsx"):
            raise HTTPException(400, "Please upload an .xlsx workbook.")
        body = await request.body()
        if not body or len(body) > 20_000_000:
            raise HTTPException(400, "The file is empty or larger than 20 MB.")
        INPUT_DIR.mkdir(exist_ok=True)
        path = INPUT_DIR / name
        path.write_bytes(body)
        try:
            return _preview(path)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"Could not read that workbook: {exc}")

    @app.get("/api/template")
    def template():
        path = INPUT_DIR / "_verity_template.xlsx"
        write_template(path)
        return FileResponse(path, filename="verity_input_template.xlsx")

    @app.post("/api/run")
    async def start(request: Request):
        body = await request.json()
        timeout_s = max(30, min(int(body.get("timeout", 180)), 900))
        return {"id": harness.start(body.get("input", ""), timeout_s).id}

    @app.post("/api/stop")
    def stop():
        if harness.current and harness.busy():
            harness.current.cancelled = True
            harness.current.on_log("Stop requested - finishing the current step.", None, "attention")
        return {"ok": True}

    @app.get("/api/state")
    def state(since: int = 0):
        cur = harness.current
        return JSONResponse({"busy": harness.busy(), "run": cur.snapshot(since) if cur else None},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/history")
    def history():
        out = []
        for folder in sorted(OUTPUT_DIR.iterdir(), reverse=True):
            meta_file = folder / "run.json"
            if not meta_file.exists():
                continue
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            counts = {}
            for r in data.get("rows", []):
                counts[r.get("status") or "-"] = counts.get(r.get("status") or "-", 0) + 1
            out.append({"id": data["id"], "input": data["input"], "started": data["started"],
                        "status": data["status"], "applicants": len(data.get("rows", [])), "counts": counts})
        return out[:50]

    @app.get("/api/history/{run_id}")
    def history_run(run_id: str):
        if not re.fullmatch(r"[\w\-]+", run_id) or not (OUTPUT_DIR / run_id / "run.json").exists():
            raise HTTPException(404, "Run not found.")
        return json.loads((OUTPUT_DIR / run_id / "run.json").read_text(encoding="utf-8"))

    app.mount("/files", StaticFiles(directory=OUTPUT_DIR), name="files")
    return app


def serve(port=8765, open_browser=True, host="127.0.0.1", access_code=None):
    import uvicorn
    code = access_code or os.environ.get("VERITY_ACCESS_CODE") or ""
    exposed = host not in ("127.0.0.1", "localhost", "::1")
    if code == "auto" or (exposed and not code):  # never reachable by others without a code
        code = secrets.token_urlsafe(9)
        print(f"Access code for this session: {code}")
    url = f"http://127.0.0.1:{port}"
    print(f"Verity harness running at {url}  (Ctrl+C to stop)" + ("  - sign-in required" if code else ""))
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(code or None), host=host, port=port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


def share(port=8765):
    """Serve the harness behind an access code and open a public link to it (Cloudflare quick tunnel).

    The link forwards to this machine, so it works only while this command is
    running. The access code is kept in verity/.access_code and reused.
    """
    import shutil
    import subprocess
    import sys

    exe = shutil.which("cloudflared") or str(_HERE / ".tools" / "cloudflared" / "cloudflared.exe")
    if not Path(exe).exists():
        sys.exit("cloudflared was not found. Install it (https://github.com/cloudflare/cloudflared/releases) "
                 "or place cloudflared.exe in verity/.tools/cloudflared/.")
    code_file, url_file = _HERE / ".access_code", _HERE / ".share_url"
    if not code_file.exists() or not code_file.read_text(encoding="utf-8").strip():
        code_file.write_text(secrets.token_urlsafe(9), encoding="utf-8")
    code = code_file.read_text(encoding="utf-8").strip()
    url_file.unlink(missing_ok=True)

    tunnel = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")

    def watch():
        for line in tunnel.stderr:
            found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if found and not url_file.exists():
                url_file.write_text(found.group(0), encoding="utf-8")
                print(f"\nPublic link : {found.group(0)}\nAccess code : {code}\n"
                      "The link works only while this window stays open.\n", flush=True)

    threading.Thread(target=watch, daemon=True).start()
    awake = _stay_awake(True)
    if awake:
        print("Keeping this PC from idle-sleeping while the link is open (closing the lid still sleeps).")
    try:
        serve(port, False, "127.0.0.1", code)
    finally:
        _stay_awake(False)
        tunnel.terminate()
        url_file.unlink(missing_ok=True)


def _stay_awake(on):
    """Ask Windows not to idle-sleep while the link is shared: a sleeping PC drops the public address.

    This is a per-process request (as a video player makes); it changes no power settings and ends
    when the command exits.
    """
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False
