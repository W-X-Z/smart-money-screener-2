"""키움 REST API 최소 클라이언트 (시세 조회 전용)."""

import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

BASE_URL = "https://api.kiwoom.com"
ROOT = Path(__file__).resolve().parent.parent

# 시세성 조회 경로만 허용 (주문 /ordr, 계좌 /acnt 등은 호출 불가)
ALLOWED_PATHS = {"/api/dostk/stkinfo", "/api/dostk/mrkcond", "/api/dostk/rkinfo", "/api/dostk/frgnistt"}
CALL_INTERVAL = 0.2  # 전체 호출 간격 (초당 5회). 스레드 간 공유
RETRIES = 3


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    missing = [k for k in ("KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f".env 에 값이 비어 있습니다: {', '.join(missing)}")


def num(value) -> float:
    """키움 숫자 문자열 파싱: '+285500', '--421'(음수), '-0', '' 등."""
    s = str(value or "").strip().replace("+", "").replace(",", "")
    neg = s.startswith("-")
    s = s.lstrip("-")
    if not s:
        return 0.0
    v = float(s)
    return -v if neg else v


class Kiwoom:
    def __init__(self) -> None:
        load_env()
        resp = requests.post(
            f"{BASE_URL}/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "appkey": os.environ["KIWOOM_APP_KEY"],
                "secretkey": os.environ["KIWOOM_SECRET_KEY"],
            },
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("token"):
            sys.exit(f"토큰 발급 실패: {data.get('return_code')} {data.get('return_msg')}")
        self.token = data["token"]
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def call(self, path: str, api_id: str, body: dict, list_key: str, max_pages: int = 1) -> List[Dict]:
        """조회 후 list_key 의 행 목록을 반환. max_pages > 1 이면 연속조회."""
        assert path in ALLOWED_PATHS, f"허용되지 않은 경로: {path}"
        rows: List[Dict] = []
        cont_yn, next_key = "N", ""
        for _ in range(max_pages):
            resp, data = self._post_with_retry(path, api_id, body, cont_yn, next_key)
            rows.extend(data.get(list_key) or [])
            cont_yn = resp.headers.get("cont-yn", "N")
            next_key = resp.headers.get("next-key", "")
            if cont_yn != "Y":
                break
        return rows

    def _throttle(self) -> None:
        with self._lock:
            now = time.time()
            slot = max(now, self._next_slot)
            self._next_slot = slot + CALL_INTERVAL
        if slot > now:
            time.sleep(slot - now)

    def _post_with_retry(self, path, api_id, body, cont_yn, next_key):
        err = ""
        for attempt in range(RETRIES + 1):
            if attempt:
                time.sleep(2 ** attempt)  # 2, 4, 8초
            self._throttle()
            try:
                resp = requests.post(
                    f"{BASE_URL}{path}",
                    json=body,
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "authorization": f"Bearer {self.token}",
                        "api-id": api_id,
                        "cont-yn": cont_yn,
                        "next-key": next_key,
                    },
                    timeout=15,
                )
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                err = str(e)
                continue
            if data.get("return_code") == 0:
                return resp, data
            err = f"{data.get('return_code')} {data.get('return_msg')}"
        raise RuntimeError(f"{api_id} 실패: {err}")

    # --- 사용하는 API ---

    def intraday_investor(self, invsr: str, mrkt_tp: str = "000") -> List[Dict]:
        """ka10063 장중투자자별매매 (잠정). 금액 단위 백만원."""
        return self.call("/api/dostk/mrkcond", "ka10063", {
            "mrkt_tp": mrkt_tp, "amt_qty_tp": "1", "invsr": invsr,
            "frgn_all": "0", "smtm_netprps_tp": "0", "stex_tp": "1",
        }, "opmr_invsr_trde", max_pages=5)

    def intraday_top_pension(self, mrkt_tp: str) -> List[Dict]:
        """ka10065 장중투자자별매매상위 - 연기금(6000) 순매수. 수량 단위 주."""
        return self.call("/api/dostk/rkinfo", "ka10065", {
            "trde_tp": "1", "mrkt_tp": mrkt_tp, "orgn_tp": "6000",
        }, "opmr_invsr_trde_upper")

    def daily_investor(self, stk_cd: str, dt: str, pages: int = 1) -> List[Dict]:
        """ka10059 종목별투자자기관별 - dt 부터 과거로 일별 (페이지당 100행). 금액 단위 백만원."""
        return self.call("/api/dostk/stkinfo", "ka10059", {
            "dt": dt, "stk_cd": stk_cd, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000",
        }, "stk_invsr_orgn", max_pages=pages)

    def stock_list(self, mrkt_tp: str) -> List[Dict]:
        """ka10099 종목정보 리스트 (mrkt_tp: 0 코스피, 10 코스닥). 응답 목록 키를 자동으로 찾는다."""
        assert "/api/dostk/stkinfo" in ALLOWED_PATHS
        resp = requests.post(
            f"{BASE_URL}/api/dostk/stkinfo",
            json={"mrkt_tp": mrkt_tp},
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.token}",
                "api-id": "ka10099", "cont-yn": "N", "next-key": "",
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("return_code") != 0:
            raise RuntimeError(f"ka10099 실패: {data.get('return_code')} {data.get('return_msg')}")
        rows = next((v for v in data.values() if isinstance(v, list)), [])
        out = []
        for r in rows:
            code = r.get("code") or r.get("stk_cd")
            name = r.get("name") or r.get("stk_nm") or ""
            if code:
                out.append({"stk_cd": code, "stk_nm": name, "raw": r})
        return out
