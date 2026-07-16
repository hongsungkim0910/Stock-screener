# -*- coding: utf-8 -*-
"""
주간 오닐 RS(상대강도) 스크리너 → 텔레그램 전송 (GitHub Actions용, Streamlit 불필요)

로직: RS 점수 = 3개월 수익률×2 + 6개월 + 9개월 + 12개월 수익률 (수정주가 기준)
      → 필터 통과 유니버스 내 백분위 1~99 랭킹 → RS 90 이상 상위 50종목 전송

유니버스: KOSPI+KOSDAQ 전체 보통주 (스팩·우선주 제외)
필터  ①: 시가총액 하위 20% 제외
필터  ②: 최근 20거래일 평균 거래대금(종가×거래량) 하위 30% 제외
순서   : 필터 먼저 → 남은 종목끼리 백분위 (거래정지 종목은 ②에서 자연 탈락)
신규상장: 상장 3개월(60거래일) 이상이면 포함 — 부족한 기간(6/9/12개월)의
          수익률은 상장 후 전체 수익률로 대체 (오닐식: 신규 주도주 포착)

부수 출력: docs/rs_latest.csv (Streamlit 탭에서 읽어 표시 — 워크플로가 커밋)

필요 환경변수 (GitHub Secrets):
    TELEGRAM_TOKEN
    TELEGRAM_CHAT_ID_SCREENER   ← 기존 스크리너 채널 공용 (별도 채널이면 교체)
"""

import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import FinanceDataReader as fdr

KST = timezone(timedelta(hours=9))

MCAP_DROP_PCT = 20       # 시총 하위 20% 제외
VALUE_DROP_PCT = 30      # 20일 평균 거래대금 하위 30% 제외
VALUE_WINDOW = 20        # 거래대금 평균 기간 (거래일)
MIN_HISTORY_DAYS = 60    # 신규상장 최소 이력 (거래일, 약 3개월)
SEND_MCAP_TOP_PCT = 30   # 텔레그램 발송: 랭킹 대상 중 시총 상위 30%만
RS_CUT = 90              # 발송 기준 RS
MAX_SEND = 50            # 발송 종목 수 상한
CSV_PATH = "docs/rs_latest.csv"

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID_SCREENER", "")


# ------------------------------------------------------------------ 유니버스
def get_universe() -> pd.DataFrame:
    """전체 보통주 (스팩·우선주 제외) + 시총 하위 20% 제외"""
    kospi = fdr.StockListing("KOSPI"); kospi["시장"] = "KOSPI"
    kosdaq = fdr.StockListing("KOSDAQ"); kosdaq["시장"] = "KOSDAQ"
    df = pd.concat([kospi, kosdaq], ignore_index=True)

    code_col = next(c for c in ["Code", "Symbol", "종목코드"] if c in df.columns)
    name_col = next(c for c in ["Name", "종목명"] if c in df.columns)
    mc_col = next(c for c in ["Marcap", "MarketCap", "시가총액"] if c in df.columns)

    df = df.dropna(subset=[mc_col])
    df[code_col] = df[code_col].astype(str).str.zfill(6)

    df = df[df[code_col].str[-1] == "0"]                       # 보통주 (끝자리 0)
    df = df[~df[name_col].str.contains("스팩", na=False)]      # 스팩 제외

    mc_cut = np.percentile(df[mc_col], MCAP_DROP_PCT)          # 시총 하위 20% 컷
    df = df[df[mc_col] >= mc_cut]

    out = pd.DataFrame({"종목명": df[name_col].values,
                        "시장": df["시장"].values,
                        "시총": df[mc_col].values},
                       index=df[code_col].values)
    out.index.name = "티커"
    return out[~out.index.duplicated(keep="first")]


# ------------------------------------------------------------------ RS 계산
def weighted_return(closes: pd.Series, now: datetime):
    """3·6·9·12개월 수익률과 가중 점수.
    상장 60거래일 미만이면 None. 12개월 미만 신규주는 부족한 기간의
    수익률을 상장 후 전체 수익률로 대체하고 신규=True 표시."""
    if len(closes) < MIN_HISTORY_DAYS:
        return None
    cur = closes.iloc[-1]
    if not np.isfinite(cur) or cur <= 0:
        return None
    today = pd.Timestamp(now.date())
    is_new = closes.index[0] > today - pd.Timedelta(days=365 - 21)
    rets = {}
    for m in (3, 6, 9, 12):
        target = today - pd.Timedelta(days=int(m * 30.44))
        past = closes.loc[:target]
        base = past.iloc[-1] if not past.empty else closes.iloc[0]  # 신규주: 상장 초기가 대체
        if not np.isfinite(base) or base <= 0:
            return None
        rets[m] = (cur / base - 1) * 100
    score = 2 * rets[3] + rets[6] + rets[9] + rets[12]
    return {"score": score, "r3": rets[3], "r6": rets[6],
            "r9": rets[9], "r12": rets[12], "close": cur, "신규": is_new}


def scan_rs(uni: pd.DataFrame, sleep: float = 0.1) -> pd.DataFrame:
    """유니버스 전체 1회 스캔 → 수익률·20일 평균 거래대금 계산"""
    now = datetime.now(KST)
    start = (now - timedelta(days=400)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    rows = []
    for i, tkr in enumerate(uni.index, 1):
        if i % 100 == 0:
            print(f"  RS 스캔: {i}/{len(uni)}")
        try:
            daily = fdr.DataReader(tkr, start, end)
            if daily is None or daily.empty or "Close" not in daily.columns:
                continue
            daily = daily.dropna(subset=["Close"])
            r = weighted_return(daily["Close"], now)
            if r is None:
                continue
            tail = daily.tail(VALUE_WINDOW)
            value20 = float((tail["Close"] * tail.get("Volume", 0)).mean())
            rows.append({"티커": tkr, "종목명": uni.loc[tkr, "종목명"],
                         "시장": uni.loc[tkr, "시장"], "시총": uni.loc[tkr, "시총"],
                         "거래대금20": value20, **r})
        except Exception:
            pass
        time.sleep(sleep)
    return pd.DataFrame(rows).set_index("티커")


def rank_rs(df: pd.DataFrame) -> pd.DataFrame:
    """거래대금 필터 → 백분위 1~99 (필터 먼저, 백분위 나중)"""
    v_cut = np.percentile(df["거래대금20"], VALUE_DROP_PCT)
    df = df[df["거래대금20"] >= v_cut].copy()
    df["RS"] = (df["score"].rank(pct=True) * 98 + 1).round().astype(int)
    return df.sort_values("score", ascending=False)


# ------------------------------------------------------------------ 텔레그램
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    chunks.append(cur)
    for c in chunks:
        r = requests.post(url, json={"chat_id": TG_CHAT, "text": c,
                                     "disable_web_page_preview": True}, timeout=30)
        r.raise_for_status()
        time.sleep(0.5)


# ------------------------------------------------------------------ 메인
def main():
    now = datetime.now(KST)

    uni = get_universe()
    print(f"유니버스(시총 필터 후): {len(uni)}종목")

    raw = scan_rs(uni)
    print(f"수익률 계산 완료: {len(raw)}종목 (신규상장 {int(raw['신규'].sum())}종목 포함)")

    ranked = rank_rs(raw)
    print(f"거래대금 필터 후 랭킹 대상: {len(ranked)}종목")

    mc_send_cut = np.percentile(ranked["시총"], 100 - SEND_MCAP_TOP_PCT)
    send_pool = ranked[ranked["시총"] >= mc_send_cut]
    top = send_pool[send_pool["RS"] >= RS_CUT].head(MAX_SEND)
    print(f"발송 시총컷(상위 {SEND_MCAP_TOP_PCT}%): {mc_send_cut/1e8:,.0f}억 → RS {RS_CUT}+ : {len(top)}종목")

    # Streamlit 탭용 CSV (전체 랭킹 저장 — 탭에서 자유롭게 필터)
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    cols = ["종목명", "시장", "RS", "신규", "시총", "r3", "r6", "r9", "r12", "close", "거래대금20"]
    ranked[cols].to_csv(CSV_PATH, encoding="utf-8-sig")
    print(f"{CSV_PATH} 저장 완료")

    lines = [
        f"📈 주간 RS 상대강도 스크리너 ({now.strftime('%Y-%m-%d')})",
        f"조건: 전체 보통주 · 시총 하위 {MCAP_DROP_PCT}% 및 "
        f"20일 거래대금 하위 {VALUE_DROP_PCT}% 제외 · RS {RS_CUT} 이상",
        f"발송: 시총 상위 {SEND_MCAP_TOP_PCT}% (컷 {mc_send_cut/1e8:,.0f}억) · 전체 랭킹은 웹앱 RS 탭",
        f"점수: 3개월×2 + 6 + 9 + 12개월 수익률 (수정주가) · 🆕=상장 12개월 미만",
        "",
    ]
    if top.empty:
        lines.append("해당 종목 없음")
    else:
        for i, (tkr, s) in enumerate(top.iterrows(), 1):
            new_tag = " 🆕" if s["신규"] else ""
            lines.append(
                f"{i}. {s['종목명']} ({tkr}·{s['시장']}) RS {s['RS']}{new_tag}\n"
                f"   3M {s['r3']:+.0f}% · 12M {s['r12']:+.0f}% · {s['close']:,.0f}원"
            )
    send_telegram("\n".join(lines))
    print("텔레그램 전송 완료")


if __name__ == "__main__":
    main()
