"""Vercel 함수: GET /api/flow?code=005930 -> 종목의 최근 연기금 일별 순매수(억)·종가.

KRX_ID / KRX_PW 는 Vercel 프로젝트 환경변수로 설정한다.
응답은 Vercel CDN 에 10분 캐시되어 같은 종목 재조회 시 KRX 를 다시 호출하지 않는다.
"""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _krx import KRX, KRXError  # noqa: E402

CODE = re.compile(r"^[0-9A-Z]{6}$")
_krx = None  # 웜 인스턴스에서 로그인 세션 재사용


class handler(BaseHTTPRequestHandler):  # noqa: N801 (Vercel 규약)
    def do_GET(self):  # noqa: N802
        global _krx
        code = parse_qs(urlparse(self.path).query).get("code", [""])[0]
        if not CODE.match(code):
            return self._json(400, {"error": "잘못된 종목코드"})
        try:
            if _krx is None:
                _krx = KRX()
            rows = _krx.pension_flow(code)
        except KRXError as e:
            return self._json(502, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self._json(200, {"code": code, "rows": rows}, cache=True)

    def _json(self, status, obj, cache=False):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, s-maxage=600, stale-while-revalidate=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)
