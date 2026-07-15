# -*- coding: utf-8 -*-
"""
주간 실적+신고가 스크리너 → 텔레그램 전송 (GitHub Actions용, Streamlit 불필요)

로직: 직전 확정분기 단일 영업이익 QoQ·YoY 동시 상위 20% (흑자, 시총 300위 유니버스)
      AND 26주 또는 52주 주봉 신고가

필요 환경변수 (GitHub Secrets):
    DART_API_KEY
    TELEGRAM_TOKEN
    TELEGRAM_CHAT_ID_SCREENER   ← 새 채널용 chat_id
"""

import os
import time
from datetime import datetime, timedelta, timezone, date

import numpy as np
import pandas as pd
import requests
import FinanceDataReader as fdr

try:
    from opendartreader import OpenDartReader   # 0.3.x
except ImportError:
    import OpenDartReader                        # 0.2.x

KST = timezone(timedelta(hours=9))
REPRT_MAP = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
BATCH_SIZE = 90
TOP_PCT = 20  # 상위 20%

DART_KEY = os.environ["DART_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID_SCREENER"]

dart = OpenDartReader(DART_KEY)


# ------------------------------------------------------------------ 유니버스 (시총 300위)
def get_universe_top300() -> pd.DataFrame:
    kospi = fdr.StockListing("KOSPI"); kospi["시장"] = "KOSPI"
    kosdaq = fdr.StockListing("KOSDAQ"); kosdaq["시장"] = "KOSDAQ"
    df = pd.concat([kospi, kosdaq], ignore_index=True)
    code_col = next(c for c in ["Code", "Symbol", "종목코드"] if c in df.columns)
    name_col = next(c for c in ["Name", "종목명"] if c in df.columns)
    mc_col = next(c for c in ["Marcap", "MarketCap", "시가총액"] if c in df.columns)
    df = df.dropna(subset=[mc_col])
    df = df.sort_values(mc_col, ascending=False).head(300)
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    out = pd.DataFrame({"종목명": df[name_col].values, "시장": df["시장"].values},
                       index=df[code_col].values)
    out.index.name = "티커"
    return out[~out.index.duplicated(keep="first")]


def get_corp_map() -> pd.DataFrame:
    cc = dart.corp_codes.copy()
    cc["stock_code"] = cc["stock_code"].astype(str).str.strip()
    cc = cc[cc["stock_code"].str.len() == 6]
    return cc[["corp_code", "stock_code"]].drop_duplicates("stock_code")


# ------------------------------------------------------------------ DART 실적
def fetch_cum_op(year: int, reprt_code: str, corp_codes: list, label: str) -> dict:
    out_cfs, out_ofs = {}, {}
    batches = [corp_codes[i:i+BATCH_SIZE] for i in range(0, len(corp_codes), BATCH_SIZE)]
    for bi, batch in enumerate(batches):
        print(f"  {label}: batch {bi+1}/{len(batches)}")
        df = None
        for attempt in range(2):
            try:
                df = dart.finstate(",".join(batch), year, reprt_code=reprt_code)
                break
            except Exception as e:
                print(f"    retry ({e})"); time.sleep(2)
        if df is None or df.empty:
            time.sleep(0.4); continue
        op = df[df["account_nm"].isin(["영업이익", "영업이익(손실)"])]
        for _, r in op.iterrows():
            sc = str(r.get("stock_code", "")).strip().zfill(6)
            try:
                amt = float(str(r["thstrm_amount"]).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if r.get("fs_div") == "CFS":
                out_cfs[sc] = amt
            else:
                out_ofs.setdefault(sc, amt)
        time.sleep(0.4)
    return {**out_ofs, **out_cfs}


def single_q_op(year: int, quarter: int, corp_codes: list) -> dict:
    cum_now = fetch_cum_op(year, REPRT_MAP[quarter], corp_codes, f"{year}Q{quarter}")
    if quarter == 1:
        return cum_now
    cum_prev = fetch_cum_op(year, REPRT_MAP[quarter-1], corp_codes, f"{year}Q{quarter-1}누적")
    return {sc: cum_now[sc] - cum_prev[sc] for sc in cum_now if sc in cum_prev}


def latest_confirmed_quarter(now: datetime):
    d = now.date(); y = d.year
    if d >= date(y, 11, 16): return y, 3
    if d >= date(y, 8, 16):  return y, 2
    if d >= date(y, 5, 16):  return y, 1
    if d >= date(y, 4, 1):   return y-1, 4
    return y-1, 3


# ------------------------------------------------------------------ 신고가
def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    ).dropna(subset=["Close"])


def scan_new_highs(uni: pd.DataFrame, weeks_list=(26, 52)) -> dict:
    """유니버스 전체 1회 스캔 → {티커: {26: bool, 52: bool, 'close': 주봉종가}}
    실적 교집합과 순수 신고가 목록 양쪽에서 재사용"""
    now = datetime.now(KST)
    start = (now - timedelta(days=int(max(weeks_list)*7*1.5))).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    out = {}
    for i, tkr in enumerate(uni.index, 1):
        if i % 20 == 0:
            print(f"  신고가 스캔: {i}/{len(uni)}")
        flags = {w: False for w in weeks_list}
        close = None
        try:
            daily = fdr.DataReader(tkr, start, end)
            if daily is not None and not daily.empty:
                w = to_weekly(daily)
                if not w.empty:
                    cur_high = w.iloc[-1]["High"]
                    close = w.iloc[-1]["Close"]
                    for nw in weeks_list:
                        if len(w) >= nw+1 and cur_high >= w.iloc[-nw-1:-1]["High"].max():
                            flags[nw] = True
        except Exception:
            pass
        out[tkr] = {**flags, "close": close}
        time.sleep(0.2)
    return out


# ------------------------------------------------------------------ 텔레그램
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    # 텔레그램 4096자 제한 → 분할 전송
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
    year, quarter = latest_confirmed_quarter(now)
    print(f"기준 분기: {year}Q{quarter}")

    uni = get_universe_top300()
    cmap = get_corp_map()
    merged = uni.reset_index().merge(cmap, left_on="티커", right_on="stock_code", how="inner")
    codes = merged["corp_code"].tolist()
    print(f"유니버스: {len(merged)}종목")

    q_now = single_q_op(year, quarter, codes)
    qy, qq = (year-1, 4) if quarter == 1 else (year, quarter-1)
    q_qoq = single_q_op(qy, qq, codes)
    q_yoy = single_q_op(year-1, quarter, codes)

    rows = []
    for _, r in merged.iterrows():
        sc = r["티커"]
        a, b, c = q_now.get(sc), q_qoq.get(sc), q_yoy.get(sc)
        if a is None or b is None or c is None or b == 0 or c == 0 or a <= 0:
            continue
        rows.append({"티커": sc, "종목명": r["종목명"], "시장": r["시장"],
                     "op": a/1e8,
                     "qoq": (a-b)/abs(b)*100, "yoy": (a-c)/abs(c)*100})
    g = pd.DataFrame(rows)
    print(f"성장률 계산: {len(g)}종목")

    q_cut = np.percentile(g["qoq"], 100 - TOP_PCT)
    y_cut = np.percentile(g["yoy"], 100 - TOP_PCT)
    cand = g[(g["qoq"] >= q_cut) & (g["yoy"] >= y_cut)].copy()
    print(f"QoQ컷 {q_cut:+.1f}% / YoY컷 {y_cut:+.1f}% → 후보 {len(cand)}종목")

    # 유니버스 전체 신고가 1회 스캔 (실적 교집합 + 순수 신고가 목록에 공용)
    print("신고가 스캔 시작 (유니버스 전체)")
    hi_map = scan_new_highs(uni)

    finals = []
    for _, r in cand.iterrows():
        hi = hi_map.get(r["티커"], {26: False, 52: False})
        if hi[26] or hi[52]:
            finals.append({**r.to_dict(), "h26": hi[26], "h52": hi[52]})
    finals = sorted(finals, key=lambda x: x["yoy"], reverse=True)
    print(f"실적+신고가 최종: {len(finals)}종목")

    earnings_set = {s["티커"] for s in finals}

    # 순수 신고가 목록 (52주 / 26주만) — 52주 신고가는 정의상 26주 신고가이므로 분리
    hi52 = [t for t in uni.index if hi_map.get(t, {}).get(52)]
    hi26_only = [t for t in uni.index
                 if hi_map.get(t, {}).get(26) and not hi_map.get(t, {}).get(52)]
    print(f"52주 신고가: {len(hi52)}종목 / 26주만 신고가: {len(hi26_only)}종목")

    # ---------- 메시지 ① 실적+신고가 (기존) ----------
    lines = [
        f"📊 주간 실적+신고가 스크리너 ({now.strftime('%Y-%m-%d')})",
        f"기준: {year}년 {quarter}분기 확정실적",
        f"조건: 시총300 · 영업이익 QoQ·YoY 동시 상위 {TOP_PCT}% (흑자) + 26/52주 신고가",
        f"컷오프: QoQ {q_cut:+.1f}% / YoY {y_cut:+.1f}%",
        "",
    ]
    if not finals:
        lines.append("해당 종목 없음")
    else:
        for i, s in enumerate(finals, 1):
            tag = "52주★" if s["h52"] else "26주"
            lines.append(
                f"{i}. {s['종목명']} ({s['티커']}·{s['시장']}) {tag}\n"
                f"   영업이익 {s['op']:,.0f}억 · QoQ {s['qoq']:+.0f}% · YoY {s['yoy']:+.0f}%"
            )
    send_telegram("\n".join(lines))

    # ---------- 메시지 ②③ 순수 신고가 목록 ----------
    def high_lines(tickers, title):
        L = [f"🚀 {title} ({now.strftime('%Y-%m-%d')})",
             "대상: 시총 300 · 주봉 고가 기준 · ★=실적 스크리너 동시 통과",
             ""]
        if not tickers:
            L.append("해당 종목 없음")
        else:
            for i, t in enumerate(tickers, 1):
                star = " ★" if t in earnings_set else ""
                close = hi_map[t].get("close")
                px = f" · {close:,.0f}원" if close else ""
                L.append(f"{i}. {uni.loc[t,'종목명']} ({t}·{uni.loc[t,'시장']}){px}{star}")
        return "\n".join(L)

    send_telegram(high_lines(hi52, "52주 신고가"))
    send_telegram(high_lines(hi26_only, "26주 신고가 (52주 제외)"))
    print("텔레그램 전송 완료")


if __name__ == "__main__":
    main()
