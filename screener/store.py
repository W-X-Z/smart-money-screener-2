"""시그널 저장소 (output/signals.json). 장중 스크리너와 과거 백필이 같은 곳에 쌓는다."""

import json
from datetime import datetime
from typing import Dict, List

from screener.kiwoom import ROOT

OUTPUT_DIR = ROOT / "output"
SIGNALS_PATH = OUTPUT_DIR / "signals.json"
CACHE_DIR = OUTPUT_DIR / "cache"


def load() -> Dict[str, dict]:
    if SIGNALS_PATH.exists():
        return json.loads(SIGNALS_PATH.read_text())
    return {}


def upsert(signals: List[dict]) -> int:
    """종목코드+신호일 기준으로 추가/갱신. 추가·갱신된 건수를 반환."""
    data = load()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for s in signals:
        key = f"{s['code']}_{s['date']}"
        data[key] = {**data.get(key, {}), **s, "updated_at": now}
    OUTPUT_DIR.mkdir(exist_ok=True)
    SIGNALS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1))
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
