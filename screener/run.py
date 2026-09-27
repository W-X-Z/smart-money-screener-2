"""장중 스캔: 키움 장중 잠정치(오늘) + 아카이브(어제까지 확정치)로 일배치와 같은 규칙의 시그널을 찾는다.

1) git pull 로 최신 아카이브를 받고
2) 키움 ka10063 에서 오늘 연기금 잠정 순매수(연기금이 매매한 전 종목) 조회
3) engine/signals.py 로 시그널 속성 계산 (기본 포착 = '전체', 웹 필터가 같은 규칙으로 좁혀 봄)
4) public/data/signals.json 의 오늘 장중 신호를 이번 결과로 교체 ('장중 HH:MM'),
   research/intraday/YYYYMMDD.parquet 에 잠정치 스냅숏을 쌓고, --push 면 커밋·푸시해 웹 반영
장 마감 후 일배치가 확정치로 다시 계산하면 '장중 → 확정' 으로 바뀌고, 확정치에 없는 장중 신호는 지워진다.
장중 반복 실행은 screener/loop.py.

사용법 (Mac, 키움 등록 IP):
    python -m screener.run            # 한 번 스캔 + 저장
    python -m screener.run --push     # 한 번 스캔 + 저장 + 커밋·푸시
"""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from batch.daily import passes, records, write_flows
from engine import signals as S
from engine.archive import load
from screener import store
from screener.kiwoom import ROOT, Kiwoom, num

PENSION_INVSR = "3"  # ka10063 투자자 구분 중 연기금 (ka10065 연기금 상위와 대조해 확인한 값)
SNAP_DIR = ROOT / "research" / "intraday"


def verify_pension_code(api: Kiwoom, intraday: list) -> None:
    """ka10065(연기금 확정 코드 6000) 상위 종목의 순매수 수량이 ka10063 결과와 일치하는지 대조."""
    by_code = {r["stk_cd"]: r for r in intraday}
    top = api.intraday_top_pension("001") + api.intraday_top_pension("101")
    matched = sum(1 for t in top
                  if t["stk_cd"] in by_code and num(by_code[t["stk_cd"]]["netprps_qty"]) == num(t["netslmt"]))
    print(f"  연기금 코드 확인: 상위 {len(top)}종목 중 {matched}종목 일치")
    if top and matched < len(top) * 0.5:
        print("  ⚠ 일치하는 종목이 절반이 안 돼요. 키움 투자자 코드가 바뀌었을 수 있으니 이번 결과는 참고만 하세요.")


def git(*cmd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *cmd], cwd=ROOT, check=False)


def snapshot(today: str, hhmm: str, net_today: dict) -> None:
    """장중 잠정 순매수 스냅숏 (나중에 장중 값이 확정치와 얼마나 맞는지 보는 용도)."""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAP_DIR / f"{today}.parquet"
    df = pd.DataFrame({"time": hhmm, "code": list(net_today), "net": list(net_today.values())})
    if path.exists():
        df = pd.concat([pd.read_parquet(path), df]).drop_duplicates(["time", "code"], keep="last")
    df.to_parquet(path, index=False, compression="zstd")


def missing_days(last: str, today: str) -> list:
    """아카이브 마지막 날과 오늘 사이에 빠진 거래일 (휴장일로 기록된 날·주말 제외)."""
    cal_path = ROOT / "research" / "archive" / "calendar.json"
    cal = json.loads(cal_path.read_text()) if cal_path.exists() else {}
    d, end, out = datetime.strptime(last, "%Y%m%d") + timedelta(days=1), datetime.strptime(today, "%Y%m%d"), []
    while d < end:
        k = d.strftime("%Y%m%d")
        if d.weekday() < 5 and cal.get(k) != "holiday":
            out.append(k)
        d += timedelta(days=1)
    return out


def scan(api: Kiwoom, push: bool = False, pull: bool = True, verify: bool = True, last: str = "") -> Optional[str]:
    """한 번 스캔. 이번 잠정치의 지문을 반환한다 (last 와 같으면 저장하지 않고 건너뜀).
    오늘 데이터가 없으면(휴장·장 시작 전) None."""
    now = datetime.now()
    today, hhmm = now.strftime("%Y%m%d"), now.strftime("%H:%M")
    if pull:
        git("pull", "--rebase", "--autostash", "-q")

    p = load()
    if str(p.dates[-1]) >= today:
        print(f"[{hhmm}] 오늘 확정치가 이미 들어와서 장중 스캔은 건너뛰어요.")
        return None

    gap = missing_days(str(p.dates[-1]), today)
    if gap:
        print(f"[{hhmm}] ⚠ 확정치가 {', '.join(gap)} 에 비어 있어요. 일배치가 돌았는지 확인해 주세요. "
              "그 전까지는 '한 달간 거래 없음' 판단이 어긋날 수 있어요.")

    intraday = api.intraday_investor(PENSION_INVSR)
    if not intraday:
        print(f"[{hhmm}] 오늘 연기금 장중 데이터가 없어요. 휴장일이거나 장 시작 전이에요.")
        return None
    net_today = {r["stk_cd"]: num(r["netprps_amt"]) * 1e6 for r in intraday}  # 백만원 -> 원
    fp = hashlib.md5(repr(sorted(net_today.items())).encode()).hexdigest()
    if fp == last:
        print(f"[{hhmm}] 잠정치가 지난번과 같아요. 건너뛸게요.")
        return fp
    print(f"[{hhmm}] 연기금 장중 잠정치 {len(intraday)}종목 (확정치는 {p.dates[-1]}까지)")
    if verify:
        verify_pension_code(api, intraday)
    snapshot(today, hhmm, net_today)

    p2 = S.with_today(p, today, net_today)
    a = S.attributes(p2)
    recs = records(p2, a, [len(p2.dates) - 1])
    for r in recs:
        r["source"] = f"장중 {hhmm}"
    store.replace_intraday(today, recs)
    print(f"  전체 {len(recs)}건 저장")
    for name, kw in S.PRESETS.items():
        hits = [r for r in recs if passes(r, **kw)]
        if name != "전체":
            print(f"  {name} {len(hits)}건: " + ", ".join(f"{r['name']} {r['buy_eok']}억" for r in hits[:8]))

    if push:
        codes = {r["code"] for r in recs}
        write_flows(p2, {k: v for k, v in store.load().items() if v["code"] in codes}, today)
        git("add", "public/data", "research/intraday")
        if git("diff", "--cached", "--quiet").returncode != 0:
            git("commit", "-q", "-m", f"장중 시그널 {today} {hhmm} ({len(recs)}건)")
            git("pull", "--rebase", "--autostash", "-q")
            ok = git("push", "-q").returncode == 0
            print("  웹에 올렸어요." if ok else "  ⚠ 푸시하지 못했어요. 다음 스캔 때 다시 올려요.")
    return fp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="시그널을 커밋·푸시해 웹에 반영")
    ap.add_argument("--no-pull", action="store_true", help="시작할 때 git pull 생략")
    args = ap.parse_args()
    scan(Kiwoom(), push=args.push, pull=not args.no_pull)


if __name__ == "__main__":
    main()
