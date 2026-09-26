"""KRX 정보데이터시스템(data.krx.co.kr) 최소 클라이언트. requests 만 사용.

pykrx 와 같은 방식으로 로그인(KRX_ID / KRX_PW)한 세션으로 getJsonData 를 호출한다.
일배치(batch/daily.py)와 Vercel 함수(api/flow.py)가 함께 쓴다.
"""

import os
import time
from datetime import date, timedelta
from typing import Dict, List, Optional

import requests

BASE = "https://data.krx.co.kr"
LOGIN_PAGE = f"{BASE}/contents/MDC/COMS/client/MDCCOMS001.cmd"
LOGIN_JSP = f"{BASE}/contents/MDC/COMS/client/view/login.jsp?site=mdc"
LOGIN_URL = f"{BASE}/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
DATA_URL = f"{BASE}/comm/bldAttendant/getJsonData.cmd"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SESSION_TTL = 50 * 60  # KRX 세션 1시간 만료, 여유를 둔다
MARKETS = {"STK": "코스피", "KSQ": "코스닥"}
PENSION = 6000  # 투자자 코드: 연기금


def to_int(v) -> int:
    s = str(v or "").replace(",", "").strip()
    return int(float(s)) if s not in ("", "-") else 0


class KRXError(RuntimeError):
    pass


class KRX:
    def __init__(self, login_id: Optional[str] = None, login_pw: Optional[str] = None):
        self.login_id = login_id or os.environ.get("KRX_ID", "")
        self.login_pw = login_pw or os.environ.get("KRX_PW", "")
        if not (self.login_id and self.login_pw):
            raise KRXError("KRX_ID / KRX_PW 환경변수가 필요합니다")
        self.session: Optional[requests.Session] = None
        self.logged_in_at = 0.0
        self._isin: Dict[str, str] = {}

    def login(self) -> None:
        s = requests.Session()
        s.get(LOGIN_PAGE, headers={"User-Agent": UA}, timeout=15)
        s.get(LOGIN_JSP, headers={"User-Agent": UA, "Referer": LOGIN_PAGE}, timeout=15)
        payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "",
                   "mbrId": self.login_id, "pw": self.login_pw}
        headers = {"User-Agent": UA, "Referer": LOGIN_PAGE}
        data = s.post(LOGIN_URL, data=payload, headers=headers, timeout=15).json()
        if data.get("_error_code") == "CD011":  # 중복 로그인 -> 기존 세션 끊고 진행
            payload["skipDup"] = "Y"
            data = s.post(LOGIN_URL, data=payload, headers=headers, timeout=15).json()
        code = data.get("_error_code", "")
        if code != "CD001":
            raise KRXError(f"KRX 로그인 실패: {code} {data.get('_error_message', '')}")
        self.session, self.logged_in_at = s, time.time()

    def query(self, bld: str, **params) -> dict:
        if self.session is None or time.time() - self.logged_in_at > SESSION_TTL:
            self.login()
        headers = {
            "User-Agent": UA,
            "Referer": f"{BASE}/contents/MDC/MDI/outerLoader/index.cmd",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = self.session.post(DATA_URL, data={"bld": bld, **params}, headers=headers, timeout=30)
        try:
            return resp.json()
        except ValueError:
            raise KRXError(f"KRX 응답 오류 ({bld}): HTTP {resp.status_code} {resp.text[:200]}")

    # --- 사용하는 조회 ---

    def pension_net_by_stock(self, day: str, mkt: str) -> List[dict]:
        """하루 동안 연기금이 매매한 종목별 순매수 (투자자별 순매수상위종목, 12010).

        Returns: [{"code", "name", "net": 순매수거래대금(원)}]
        """
        out = self.query("dbms/MDC/STAT/standard/MDCSTAT02401",
                         strtDd=day, endDd=day, mktId=mkt, invstTpCd=PENSION).get("output", [])
        return [{"code": r["ISU_SRT_CD"], "name": r["ISU_NM"], "net": to_int(r["NETBID_TRDVAL"])} for r in out]

    def listing_dates(self) -> Dict[str, str]:
        """코스피·코스닥 전 종목 상장일 {코드: YYYYMMDD} (전종목 기본정보, 12005)."""
        out = {}
        for mkt in MARKETS:
            params = {"mktId": mkt, **({"segTpCd": "ALL"} if mkt == "KSQ" else {})}
            rows = self.query("dbms/MDC/STAT/standard/MDCSTAT01901", **params).get("OutBlock_1", [])
            for r in rows:
                out[r["ISU_SRT_CD"]] = r.get("LIST_DD", "").replace("/", "")
        return out

    def isin(self, code: str) -> str:
        if not self._isin:
            out = self.query("dbms/comm/finder/finder_stkisu",
                             locale="ko_KR", mktsel="ALL", searchText="", typeNo=0).get("block1", [])
            self._isin = {r["short_code"]: r["full_code"] for r in out}
        if code not in self._isin:
            raise KRXError(f"종목코드를 찾을 수 없습니다: {code}")
        return self._isin[code]

    def pension_flow(self, code: str, days: int = 150) -> List[dict]:
        """종목의 일별 연기금 순매수(억)와 종가. 오름차순. days 는 달력일 기준 조회 기간."""
        isin = self.isin(code)
        end = date.today()
        start = end - timedelta(days=days)
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        # 12009 투자자별 거래실적(개별종목) 상세, 거래대금·순매수. TRDVAL7 = 연기금
        inv = self.query("dbms/MDC/STAT/standard/MDCSTAT02303", strtDd=s, endDd=e, isuCd=isin,
                         inqTpCd=2, trdVolVal=2, askBid=3, detailView=1).get("output", [])
        # 12003 개별종목 시세 추이 (단순종가)
        px = self.query("dbms/MDC/STAT/standard/MDCSTAT01701", strtDd=s, endDd=e, isuCd=isin,
                        adjStkPrc=1).get("output", [])
        close = {r["TRD_DD"].replace("/", ""): to_int(r["TDD_CLSPRC"]) for r in px}
        rows = [{"dt": r["TRD_DD"].replace("/", ""),
                 "pension": round(to_int(r["TRDVAL7"]) / 1e8, 2),
                 "close": close.get(r["TRD_DD"].replace("/", ""), 0)} for r in inv]
        return sorted(rows, key=lambda r: r["dt"])
