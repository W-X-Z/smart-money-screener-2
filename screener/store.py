"""시그널 저장소 (public/data/signals.json + signals.csv). 일배치·장중 스크리너가 같은 파일에 쌓고 Git 으로 배포한다."""

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = OUTPUT_DIR / "cache"
# 웹에서 그대로 서빙되는 시그널 목록 (Git 에 커밋 -> Vercel 배포)
SIGNALS_PATH = ROOT / "public" / "data" / "signals.json"
CONFIRMED = "일별 확정"


def now_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


def load() -> Dict[str, dict]:
    if SIGNALS_PATH.exists():
        return {f"{s['code']}_{s['date']}": s for s in json.loads(SIGNALS_PATH.read_text())}
    return {}


def save(data: Dict[str, dict]) -> None:
    SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(data.values(), key=lambda s: (s["date"], s["buy_eok"]), reverse=True)
    SIGNALS_PATH.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n")
    write_csv(rows)


def upsert(signals: List[dict]) -> int:
    """종목코드+신호일 기준으로 추가/갱신 (장중 스캔용). 일별 확정 신호는 장중 값으로 덮어쓰지 않는다."""
    data = load()
    now = now_kst()
    n = 0
    for s in signals:
        key = f"{s['code']}_{s['date']}"
        if data.get(key, {}).get("source") == CONFIRMED:
            continue
        data[key] = {**data.get(key, {}), **s, "updated_at": now}
        n += 1
    save(data)
    return n


def replace_confirmed(start: str, end: str, signals: List[dict]) -> None:
    """start~end 의 일별 확정 신호를 새 계산 결과로 교체. 장중에 먼저 잡힌 신호는 표시를 남긴다."""
    data = load()
    now = now_kst()
    keys = {f"{s['code']}_{s['date']}" for s in signals}
    for k, v in list(data.items()):
        if start <= v["date"] <= end and v.get("source") == CONFIRMED and k not in keys:
            del data[k]
    for s in signals:
        key = f"{s['code']}_{s['date']}"
        prev = data.get(key, {})
        if prev.get("source", "").startswith("장중"):
            s = {**s, "intraday": prev["source"]}
        data[key] = {**prev, **s, "updated_at": now}
    save(data)


CSV_COLS = [
    ("매수일", "date"), ("종목코드", "code"), ("종목명", "name"), ("시장", "market"),
    ("연기금 순매수(억)", "buy_eok"), ("평소 거래대금 대비(%)", "adv_pct"), ("그 전 한 달 가장 큰 거래(억)", "past_max_eok"),
    ("그 전 한 달 3천만 원 넘는 거래일", "big_days"), ("이틀 합계", "two_day"), ("우선주", "pref"),
    ("이후 수익률(%)", "ret_pct"), ("지난 거래일", "ret_days"),
    ("구분", "source"), ("장중에 먼저 포착", "intraday"), ("업데이트", "updated_at"),
]


def write_csv(rows: List[dict]) -> None:
    """Google Sheets 가 IMPORTDATA 로 읽는 운영 시그널 기록 (public/data/signals.csv)."""
    with (SIGNALS_PATH.parent / "signals.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in CSV_COLS])
        for r in rows:
            d = str(r.get("date", ""))
            vals = []
            for _, k in CSV_COLS:
                v = r.get(k, "")
                if k == "date":
                    v = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                elif k == "code":
                    v = f"A{v}"  # IMPORTDATA 가 앞자리 0 을 지우지 않게
                elif isinstance(v, bool):
                    v = "Y" if v else ""
                elif v is None:
                    v = ""
                vals.append(v)
            w.writerow(vals)


def universe_meta() -> Dict[str, dict]:
    """backscan 이 저장한 종목 목록에서 코드 -> {name, market}."""
    meta = {}
    for mrkt, market in (("0", "코스피"), ("10", "코스닥")):
        path = CACHE_DIR / f"universe_{mrkt}.json"
        if path.exists():
            for r in json.loads(path.read_text()):
                meta[r["code"]] = {"name": r.get("name", ""), "market": market}
    return meta
