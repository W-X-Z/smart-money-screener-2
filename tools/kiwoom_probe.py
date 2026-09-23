"""키움 REST API 응답 구조 탐색 스크립트.

연기금 수급을 어떤 API로 받을 수 있는지 확인하기 위해 후보 API를 호출하고
원본 응답을 probe_output/ 에 저장한다. 시세 조회만 호출하며 주문·계좌 API는 호출하지 않는다.

사용법:
    cp .env.example .env   # 키 입력
    python tools/kiwoom_probe.py            # 기본 종목 005930
    python tools/kiwoom_probe.py 247540     # 종목 지정
"""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE_URL = "https://api.kiwoom.com"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "probe_output"

# 시세성 조회 경로만 허용 (주문 /ordr, 계좌 /acnt 등은 호출 불가)
ALLOWED_PATHS = {"/api/dostk/stkinfo", "/api/dostk/mrkcond", "/api/dostk/rkinfo", "/api/dostk/frgnistt"}


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def issue_token() -> str:
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
    token = data.get("token")
    if not token:
        sys.exit(f"토큰 발급 실패: return_code={data.get('return_code')} {data.get('return_msg')}")
    return token


def call(token: str, path: str, api_id: str, body: dict) -> dict:
    assert path in ALLOWED_PATHS, f"허용되지 않은 경로: {path}"
    resp = requests.post(
        f"{BASE_URL}{path}",
        json=body,
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": "N",
            "next-key": "",
        },
        timeout=15,
    )
    return {"http_status": resp.status_code, "request": body, "response": _json_or_text(resp)}


def _json_or_text(resp: requests.Response):
    try:
        return resp.json()
    except ValueError:
        return resp.text


def summarize(result: dict) -> str:
    body = result["response"]
    if not isinstance(body, dict):
        return f"HTTP {result['http_status']} (non-JSON)"
    lines = [f"HTTP {result['http_status']}  return_code={body.get('return_code')}  {body.get('return_msg', '')}"]
    for key, val in body.items():
        if isinstance(val, list):
            lines.append(f"  list '{key}': {len(val)} rows")
            if val and isinstance(val[0], dict):
                lines.append(f"    fields: {list(val[0].keys())}")
                lines.append(f"    first : {json.dumps(val[0], ensure_ascii=False)}")
    return "\n".join(lines)


def main() -> None:
    load_env()
    stk_cd = sys.argv[1] if len(sys.argv) > 1 else "005930"
    today = date.today().strftime("%Y%m%d")

    # 파라미터는 공개 문서 기반 추정값. 오류가 나면 return_msg 를 보고 조정한다.
    probes = [
        # 종목별 일별 투자자 수급 (연기금 필드 여부 확인이 핵심)
        ("ka10059", "/api/dostk/stkinfo", "종목별투자자기관별",
         {"dt": today, "stk_cd": stk_cd, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1000"}),
        # 장중 투자자별 순매수 상위 - 연기금(6000)
        ("ka10065", "/api/dostk/rkinfo", "장중투자자별매매상위_연기금_코스피",
         {"trde_tp": "1", "mrkt_tp": "001", "orgn_tp": "6000"}),
        ("ka10065", "/api/dostk/rkinfo", "장중투자자별매매상위_연기금_코스닥",
         {"trde_tp": "1", "mrkt_tp": "101", "orgn_tp": "6000"}),
        # 장중 투자자별 매매 (투자자 구분 선택형)
        ("ka10063", "/api/dostk/mrkcond", "장중투자자별매매",
         {"mrkt_tp": "000", "amt_qty_tp": "1", "invsr": "3", "frgn_all": "0", "smtm_netprps_tp": "0", "stex_tp": "1"}),
        # 장 마감 후 투자자별 매매 (비교용)
        ("ka10066", "/api/dostk/mrkcond", "장마감후투자자별매매",
         {"mrkt_tp": "000", "amt_qty_tp": "1", "trde_tp": "0", "stex_tp": "1"}),
    ]

    OUT_DIR.mkdir(exist_ok=True)
    token = issue_token()
    print("토큰 발급 OK\n")
    for api_id, path, label, body in probes:
        result = call(token, path, api_id, body)
        out = OUT_DIR / f"{api_id}_{label}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"[{api_id}] {label} -> {out.relative_to(ROOT)}\n{summarize(result)}\n")
        time.sleep(0.3)  # 호출 제한 여유


if __name__ == "__main__":
    main()
