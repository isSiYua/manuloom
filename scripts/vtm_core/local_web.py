from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


MAX_REQUEST_BYTES = 16 * 1024
DEFAULT_PORT = 8766


def browser_token_path(home: Path | None = None) -> Path:
    configured = os.getenv("MANULOOM_BROWSER_TOKEN_FILE")
    if configured:
        return Path(configured).expanduser().resolve()
    return (home or Path.home()) / ".config" / "manuloom" / "browser-token"


def load_or_create_browser_token(path: Path | None = None) -> tuple[str, Path]:
    destination = (path or browser_token_path()).expanduser().resolve()
    if destination.is_file():
        token = destination.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token, destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.chmod(destination, 0o600)
    return token, destination


def validate_source_url(value: object) -> str:
    url = str(value or "").strip()
    if not url or len(url) > 4096:
        raise ValueError("请提交有效的来源链接")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持 http 或 https 来源链接")
    if parsed.username or parsed.password:
        raise ValueError("来源链接不能包含用户名或密码")
    return url


def allowed_extension_origin(origin: str) -> bool:
    return origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")


class LocalJobManager:
    def __init__(self, *, vault: Path, runner: Callable[[str, Path], dict[str, object]] | None = None):
        self.vault = vault.expanduser().resolve()
        self.runner = runner or self._run_pipeline
        self.jobs: dict[str, dict[str, object]] = {}
        self.lock = threading.Lock()

    def submit(self, url: str, title: str = "") -> dict[str, object]:
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        job: dict[str, object] = {
            "id": job_id,
            "url": validate_source_url(url),
            "title": str(title or "")[:300],
            "status": "queued",
            "submitted_at": now,
        }
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._execute, args=(job_id,), daemon=True).start()
        return dict(job)

    def get(self, job_id: str) -> dict[str, object] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def _execute(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "running"
            job["started_at"] = datetime.now(timezone.utc).isoformat()
            url = str(job["url"])
        try:
            result = self.runner(url, self.vault)
        except Exception as exc:
            with self.lock:
                job = self.jobs[job_id]
                job.update(
                    status="failed",
                    error=str(exc)[:1000],
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            return
        with self.lock:
            job = self.jobs[job_id]
            job.update(
                status="complete",
                result=result,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )

    @staticmethod
    def _run_pipeline(url: str, vault: Path) -> dict[str, object]:
        script = Path(__file__).resolve().parents[1] / "video_manuscript.py"
        completed = subprocess.run(
            [sys.executable, str(script), "run", "--url", url, "--vault", str(vault)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "处理失败"
            try:
                parsed = json.loads(detail.splitlines()[-1])
                detail = str(parsed.get("error") or detail)
            except (json.JSONDecodeError, AttributeError):
                pass
            raise RuntimeError(detail[-1000:])
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("本地处理完成，但没有返回有效结果 JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("本地处理返回了无效结果")
        return result


LOCAL_APP_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ManuLoom · 知织</title>
  <style>
    :root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#07111f;color:#e8f0ff}
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 20% 10%,#143153 0,transparent 36%),#07111f}
    main{width:min(680px,calc(100% - 40px));padding:36px;border:1px solid #28435f;border-radius:22px;background:#0d1a2a;box-shadow:0 24px 80px #0007}
    h1{margin:0 0 8px;font-size:34px}.sub{color:#9fb4ca;margin:0 0 28px}.field{display:grid;gap:7px;margin:15px 0}
    input,button{box-sizing:border-box;width:100%;font:inherit;border-radius:11px;padding:13px 14px}
    input{color:#eef6ff;background:#091522;border:1px solid #35516d}button{border:0;color:#06131f;background:#6ee7d2;font-weight:750;cursor:pointer}
    button:disabled{opacity:.55;cursor:wait}#status{min-height:24px;color:#b8cbe0;margin-top:18px;white-space:pre-wrap}.hint{font-size:13px;color:#7892ab}
    code{background:#14263a;padding:2px 6px;border-radius:5px}
  </style>
</head>
<body><main>
  <h1>ManuLoom · 知织</h1>
  <p class="sub">把公开视频或文章编织成详细、可追溯的 Obsidian 笔记。</p>
  <form id="form">
    <label class="field">来源链接<input id="url" type="url" required placeholder="https://..."></label>
    <label class="field">本机配对令牌<input id="token" type="password" required autocomplete="off" placeholder="scripts/manuloom browser-token"></label>
    <button id="submit">开始生成</button>
  </form>
  <div id="status"></div>
  <p class="hint">服务仅监听本机。模型密钥不会进入浏览器；配对令牌只保存在此浏览器的本地存储。</p>
  <script>
    const $=id=>document.getElementById(id), token=$('token'), status=$('status'), button=$('submit');
    token.value=localStorage.getItem('manuloom-token')||'';
    async function poll(id){
      const response=await fetch('/api/jobs/'+id,{headers:{Authorization:'Bearer '+token.value}});
      const job=await response.json();
      if(!response.ok) throw new Error(job.error||'无法读取任务');
      status.textContent='任务 '+id+'：'+job.status+(job.result?.note?'\n'+job.result.note:'')+(job.error?'\n'+job.error:'');
      if(job.status==='queued'||job.status==='running') setTimeout(()=>poll(id).catch(showError),2500); else button.disabled=false;
    }
    function showError(error){status.textContent='错误：'+error.message;button.disabled=false}
    $('form').addEventListener('submit',async event=>{
      event.preventDefault(); button.disabled=true; status.textContent='正在提交…'; localStorage.setItem('manuloom-token',token.value);
      try{
        const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token.value},body:JSON.stringify({url:$('url').value})});
        const job=await response.json(); if(!response.ok) throw new Error(job.error||'提交失败');
        status.textContent='已提交任务 '+job.id; poll(job.id).catch(showError);
      }catch(error){showError(error)}
    });
  </script>
</main></body></html>"""


def make_handler(*, token: str, manager: LocalJobManager):
    class LocalHandler(BaseHTTPRequestHandler):
        server_version = "ManuLoomLocal/1"

        def log_message(self, format: str, *args: object) -> None:
            sys.stderr.write("[manuloom-local] " + (format % args) + "\n")

        def _json(self, status: int, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin", "")
            if allowed_extension_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + token
            return hmac.compare_digest(supplied, expected)

        def _require_authorized(self) -> bool:
            origin = self.headers.get("Origin", "")
            if origin and not allowed_extension_origin(origin):
                parsed = urlparse(origin)
                if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                    self._json(403, {"error": "不允许的请求来源"})
                    return False
            if not self._authorized():
                self._json(401, {"error": "本机配对令牌无效"})
                return False
            return True

        def do_OPTIONS(self) -> None:  # noqa: N802
            origin = self.headers.get("Origin", "")
            if not allowed_extension_origin(origin):
                self._json(403, {"error": "不允许的请求来源"})
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                body = LOCAL_APP_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/health":
                self._json(200, {"status": "ok", "service": "manuloom-local"})
                return
            if self.path.startswith("/api/jobs/"):
                if not self._require_authorized():
                    return
                job = manager.get(self.path.rsplit("/", 1)[-1])
                self._json(200, job) if job else self._json(404, {"error": "任务不存在"})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/jobs":
                self._json(404, {"error": "not found"})
                return
            if not self._require_authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 1 or length > MAX_REQUEST_BYTES:
                self._json(413, {"error": "请求大小无效"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求必须是 JSON 对象")
                job = manager.submit(payload.get("url", ""), str(payload.get("title") or ""))
            except (json.JSONDecodeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(202, job)

    return LocalHandler


def create_local_server(*, port: int, token: str, manager: LocalJobManager) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("端口必须在 0 到 65535 之间")
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(token=token, manager=manager))


def serve_local(*, vault: Path, port: int = DEFAULT_PORT) -> None:
    token, token_file = load_or_create_browser_token()
    manager = LocalJobManager(vault=vault)
    server = create_local_server(port=port, token=token, manager=manager)
    print(f"ManuLoom local service: http://127.0.0.1:{port}", file=sys.stderr, flush=True)
    print(f"Pairing token: scripts/manuloom browser-token ({token_file})", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
