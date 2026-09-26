"""시그널 저장소 (public/data/signals.json). 일배치·장중 스크리너가 같은 파일에 쌓고 Git 으로 배포한다."""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from screener.kiwoom import ROOT

OUTPUT_DIR = ROOT / "output"
CACHE_DIR = OUTPUT_DIR / "cache"
# 웹에서 그대로 서빙되는 시그널 목록 (Git 에 커밋 -> Vercel 배포)
SIGNALS_PATH = ROOT / "public" / "data" / "signals.json"


def load() -> Dict[str, dict]:
    if SIGNALS_PATH.exists():
        return {f"{s['code']}_{s['date']}": s for s in json.loads(SIGNALS_PATH.read_text())}
    return {}


def upsert(signals: List[dict]) -> int:
    """종목코드+신호일 기준으로 추가/갱신. 추가·갱신된 건수를 반환."""
    data = load()
    now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    for s in signals:
        key = f"{s['code']}_{s['date']}"
        data[key] = {**data.get(key, {}), **s, "updated_at": now}
    SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(data.values(), key=lambda s: (s["date"], s["buy_eok"]), reverse=True)
    SIGNALS_PATH.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n")
    return len(signals)


def universe_meta() -> Dict[str, dict]:
    """backscan 이 저장한 종목 목록에서 코드 -> {name, market}."""
    meta = {}
    for mrkt, market in (("0", "코스피"), ("10", "코스닥")):
        path = CACHE_DIR / f"universe_{mrkt}.json"
        if path.exists():
            for r in json.loads(path.read_text()):
                meta[r["code"]] = {"name": r.get("name", ""), "market": market}
    return meta
