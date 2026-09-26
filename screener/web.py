"""시그널 웹 UI 로컬 서버. 배포(Vercel)와 같은 화면을 띄우되 종목 수급은 키움에서 조회한다.

사용법:
    python -m screener.web            # http://localhost:8000
    python -m screener.web --port 8080
"""

import argparse
import json
import re
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from screener import store
from screener.kiwoom import ROOT, Kiwoom, num

PUBLIC_DIR = ROOT / "public"
FLOW_TTL = 600  # 종목 수급 캐시 유지 시간 (초). 장중 오늘 값이 바뀌므로 짧게 둔다
CODE = re.compile(r"^[0-9A-Z]{6}$")

_api = None
_api_lock = threading.Lock()


def kiwoom() -> Kiwoom:
    global _api
    with _api_lock:
        if _api is None:
            _api = Kiwoom()
        return _api


def pension_flow(code: str) -> list:
    """최근 약 100거래일 연기금 순매수(억)와 종가. 오름차순."""
    today = date.today().strftime("%Y%m%d")
    cache = store.CACHE_DIR / f"{code}_{today}_web.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < FLOW_TTL:
        rows = json.loads(cache.read_text())
    else:
        rows = kiwoom().daily_investor(code, today)
        store.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows, ensure_ascii=False))
    rows = sorted({r["dt"]: r for r in rows}.values(), key=lambda r: r["dt"])
    return [{"dt": r["dt"], "pension": round(num(r["penfnd_etc"]) / 100, 2), "close": abs(num(r["cur_prc"]))}
            for r in rows]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        try:
            if url.path == "/":
                self._send(200, (PUBLIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/data/signals.json":
                body = store.SIGNALS_PATH.read_bytes() if store.SIGNALS_PATH.exists() else b"[]"
                self._send(200, body, "application/json; charset=utf-8")
            elif url.path.startswith("/data/flow/") and url.path.endswith(".json"):
                code = url.path[len("/data/flow/"):-len(".json")]
                if not CODE.match(code):
                    return self._json(400, {"error": "잘못된 종목코드"})
                path = PUBLIC_DIR / "data" / "flow" / f"{code}.json"
                if path.exists():
                    self._send(200, path.read_bytes(), "application/json; charset=utf-8")
                else:  # 로컬에서는 파일이 없으면 키움으로 바로 조회
                    self._json(200, {"code": code, "rows": pension_flow(code)})
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:  # 화면에 오류를 보여주기 위해 JSON 으로 반환
            self._json(500, {"error": str(e)})

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 요청 로그 생략


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"시그널 웹 UI: {url}  (종료: Ctrl+C)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
