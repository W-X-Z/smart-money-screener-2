"""검증 결과를 Google Sheets 로 올릴 .xlsx 로 만든다 (Drive 업로드 시 시트로 변환).

탭:
  요약                 입력값(기간·주 수)과, 두 시그널 탭을 참조하는 수식으로 계산한 비교표
  추천 로직 시그널     추천 로직의 이벤트별 데이터
  현재 로직 시그널     현재 운영 로직의 이벤트별 데이터
  로직 비교            모든 변형의 결과 (research/evaluate.py 계산값)
  Data Sources         데이터 출처

사용법:
    python -m research.sheet --current "<현재 로직 이름>" --recommended "<추천 로직 이름>" --headline "<한 줄 결론>"
"""

import argparse
import json
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

RESULTS = Path(__file__).resolve().parent / "results"
FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF")
BODY_FONT = Font(name=FONT)
INPUT_FONT = Font(name=FONT, color="0000FF")
PCT = '0.0%;-0.0%;"-"'
EOK = '#,##0.0'
MAX_ROW = 2000

SIGNAL_COLS = [
    ("신호일", 12), ("종목코드", 10), ("종목명", 18), ("시장", 8),
    ("연기금 순매수(억)", 14), ("20일 평균 거래대금(억)", 16), ("거래대금 대비 순매수", 16),
    ("시가총액(억)", 12), ("직전 20일 최대 |순매수|(억)", 18),
    ("5일 초과수익", 12), ("10일 초과수익", 12), ("20일 초과수익", 12), ("20일 절대수익", 12),
    ("20일 초과수익 (당일 종가 진입)", 20),
]


def header(ws, row, labels, widths=None):
    for i, label in enumerate(labels, 1):
        c = ws.cell(row=row, column=i, value=label)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]


def signal_sheet(wb, title, ev: pd.DataFrame, run_at: str):
    ws = wb.create_sheet(title)
    header(ws, 1, [c for c, _ in SIGNAL_COLS], [w for _, w in SIGNAL_COLS])
    ws.row_dimensions[1].height = 32
    ws["A1"].comment = Comment(
        f"Source: KRX 정보데이터시스템 (투자자별 순매수상위종목·전종목 시세), research/archive, "
        f"{len(ev)} rows, run {run_at} KST. research/evaluate.py 로 계산. "
        "초과수익 = 같은 날·같은 시장·같은 시총 5분위 중앙값 대비, 다음날 시가 진입, 수정주가 기준.", "Claude")
    ev = ev.sort_values("date", ascending=False)
    for r, e in enumerate(ev.itertuples(index=False), 2):
        d = str(e.date)
        vals = [f"{d[:4]}-{d[4:6]}-{d[6:]}", str(e.code), e.name, e.market,
                e.net_eok, e.adv20_eok, f"=IF(F{r}=0,0,E{r}/F{r})", e.mcap_eok, e.past_max_eok]
        rets = [getattr(e, "x5_T1시가"), getattr(e, "x10_T1시가"), getattr(e, "x20_T1시가"),
                getattr(e, "r20_T1시가"), getattr(e, "x20_T종가")]
        vals += [None if pd.isna(v) else v / 100 for v in rets]
        for col, v in enumerate(vals, 1):
            c = ws.cell(row=r, column=col, value=v)
            c.font = BODY_FONT
            if col in (5, 6, 9):
                c.number_format = EOK
            elif col == 8:
                c.number_format = "#,##0"
            elif col >= 7 and col != 8:
                c.number_format = PCT
    ws.freeze_panes = "D2"
    return ws


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", required=True)
    ap.add_argument("--recommended", required=True)
    ap.add_argument("--headline", required=True)
    ap.add_argument("--out", default=str(RESULTS / "pension_signal_logic_test.xlsx"))
    args = ap.parse_args()

    meta = json.loads((RESULTS / "meta.json").read_text())
    events = pd.read_pickle(RESULTS / "events.pkl")
    variants = pd.read_csv(RESULTS / "variants.csv")
    for name in (args.current, args.recommended):
        ev = events[name]
        # itertuples 는 한글·특수문자 컬럼명을 바꾸므로 사용할 컬럼만 영문으로 정리
        events[name] = ev.rename(columns={
            "x5_T+1시가": "x5_T1시가", "x10_T+1시가": "x10_T1시가", "x20_T+1시가": "x20_T1시가",
            "r20_T+1시가": "r20_T1시가"})
    run_at = meta["generated"].replace("T", " ")

    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    ws["A1"] = "연기금 시그널 로직 검증"
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws["A2"] = args.headline
    ws["A2"].font = Font(name=FONT, size=11)

    ws["A4"] = "입력값 (파란 글씨)"
    ws["A4"].font = Font(name=FONT, bold=True)
    inputs = [("분석 시작일", f"{meta['from'][:4]}-{meta['from'][4:6]}-{meta['from'][6:]}"),
              ("분석 종료일", f"{meta['to'][:4]}-{meta['to'][4:6]}-{meta['to'][6:]}"),
              ("분석 기간 거래일 수", meta["days"])]
    for i, (k, v) in enumerate(inputs, 5):
        ws.cell(row=i, column=1, value=k).font = BODY_FONT
        ws.cell(row=i, column=2, value=v).font = INPUT_FONT
    ws["B7"].comment = Comment("Source: research/archive 거래일 수 (침묵 판단용 앞부분 45거래일 제외)", "Claude")
    ws["A8"] = "분석 기간 주 수"
    ws["A8"].font = BODY_FONT
    ws["B8"] = "=B7/5"
    ws["B8"].number_format = "0.0"

    cols = ["로직", "신호수", "주당 신호수", "5일 초과수익 중앙값", "20일 초과수익 평균",
            "20일 초과수익 중앙값", "20일 승률 (초과수익>0)"]
    header(ws, 10, cols, [34, 10, 12, 16, 16, 16, 16])
    ws.row_dimensions[10].height = 32
    for r, (label, tab) in enumerate((("현재 로직", "현재 로직 시그널"), ("추천 로직", "추천 로직 시그널")), 11):
        rng = lambda col: f"'{tab}'!{col}2:{col}{MAX_ROW}"  # noqa: E731
        row = [label, f"=COUNTA({rng('A')})", f"=IF($B$8=0,0,B{r}/$B$8)",
               f"=MEDIAN({rng('J')})", f"=AVERAGE({rng('L')})", f"=MEDIAN({rng('L')})",
               f"=IF(COUNT({rng('L')})=0,0,COUNTIF({rng('L')},\">0\")/COUNT({rng('L')}))"]
        for col, v in enumerate(row, 1):
            c = ws.cell(row=r, column=col, value=v)
            c.font = BODY_FONT
            c.number_format = {2: "0", 3: "0.0"}.get(col, PCT)
    ws["A14"] = "로직 정의"
    ws["A14"].font = Font(name=FONT, bold=True)
    ws["A15"], ws["B15"] = "현재 로직", args.current
    ws["A16"], ws["B16"] = "추천 로직", args.recommended
    for cell in ("A15", "B15", "A16", "B16"):
        ws[cell].font = BODY_FONT
    ws["A18"] = "초과수익: 신호 다음날 시가에 사서 N거래일 뒤 종가까지의 수익률에서, 같은 날 같은 시장·같은 시총 5분위 종목들의 중앙값을 뺀 값"
    ws["A18"].font = Font(name=FONT, italic=True, color="555555")

    signal_sheet(wb, "추천 로직 시그널", events[args.recommended], run_at)
    signal_sheet(wb, "현재 로직 시그널", events[args.current], run_at)

    wv = wb.create_sheet("로직 비교")
    keep = [c for c in variants.columns if c not in ("종목수",)]
    header(wv, 1, keep, [12, 46] + [13] * (len(keep) - 2))
    wv.row_dimensions[1].height = 44
    wv["A1"].comment = Comment(
        f"Source: research/evaluate.py (research/results/variants.csv), {len(variants)} rows, run {run_at} KST. "
        "Run by Claude. 수익률 단위는 %, 95%구간은 신호일 단위 부트스트랩.", "Claude")
    for r, row in enumerate(variants[keep].itertuples(index=False), 2):
        for col, v in enumerate(row, 1):
            c = wv.cell(row=r, column=col, value=None if (isinstance(v, float) and pd.isna(v)) else v)
            c.font = BODY_FONT
    wv.freeze_panes = "C2"

    wd = wb.create_sheet("Data Sources")
    ds_cols = ["Written to", "Source", "Object", "Obtained via", "Query or request", "Parameters",
               "Run at", "Requested by", "Row count", "Changes after retrieval", "Notes"]
    header(wd, 1, ds_cols, [26, 22, 40, 22, 40, 34, 18, 14, 10, 30, 30])
    period = f"{meta['from']}~{meta['to']}"
    n_rec, n_cur = len(events[args.recommended]), len(events[args.current])
    rows = [
        [f"'추천 로직 시그널'!A1:N{n_rec + 1}", "KRX 정보데이터시스템", "MDCSTAT02401 (투자자별 순매수상위종목, 연기금), MDCSTAT01501 (전종목 시세)",
         "GitHub Actions research-archive -> research/evaluate.py", "연기금 로직 개선안 검증", f"기간 {period}, 코스피·코스닥",
         run_at, "사용자", n_rec, "추천 로직 조건으로 필터, 20거래일 재신호 제외, 수익률 계산", ""],
        [f"'현재 로직 시그널'!A1:N{n_cur + 1}", "KRX 정보데이터시스템", "MDCSTAT02401, MDCSTAT01501",
         "GitHub Actions research-archive -> research/evaluate.py", "연기금 로직 개선안 검증", f"기간 {period}, 코스피·코스닥",
         run_at, "사용자", n_cur, "현재 로직 조건으로 필터, 수익률 계산", ""],
        [f"'로직 비교'!A1:{get_column_letter(len(keep))}{len(variants) + 1}", "research/evaluate.py", "research/results/variants.csv",
         "Run by Claude", "연기금 로직 개선안 검증", f"기간 {period}", run_at, "사용자", len(variants), "없음", ""],
    ]
    for r, row in enumerate(rows, 2):
        for col, v in enumerate(row, 1):
            wd.cell(row=r, column=col, value=v).font = BODY_FONT

    wb.save(args.out)
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
