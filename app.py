# -*- coding: utf-8 -*-
"""
국내주식 스크리너 (모바일)
데이터: FinanceDataReader (네이버 금융 기반, 수정주가)
      + DART Open API (분기 영업이익, 다중회사 주요계정)
"""

import os
import time
import streamlit as st
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, timezone, date

try:
    from opendartreader import OpenDartReader   # 0.3.x
    HAS_DART = True
    DART_IMPORT_ERR = None
except Exception:
    try:
        import OpenDartReader                    # 0.2.x
        HAS_DART = True
        DART_IMPORT_ERR = None
    except Exception as e:
        HAS_DART = False
        DART_IMPORT_ERR = f"{type(e).__name__}: {e}"

KST = timezone(timedelta(hours=9))
def kst_now(): return datetime.now(KST)
def yyyymmdd_to_dash(s): return f"{s[:4]}-{s[4:6]}-{s[6:8]}"

st.set_page_config(page_title="국내주식 스크리너", page_icon="📈",
                   layout="centered", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container {padding: 0.75rem 0.5rem; max-width: 100%;}
h1 {font-size: 1.4rem !important; margin-bottom: 0.5rem;}
h2 {font-size: 1.15rem !important;}
h3 {font-size: 1.0rem !important;}
.stTabs [data-baseweb="tab-list"] {gap: 4px;}
.stTabs [data-baseweb="tab"] {padding: 8px 12px; font-size: 0.95rem;}
.stDataFrame {font-size: 0.85rem;}
div[data-testid="stExpander"] {margin-bottom: 0.5rem;}
</style>
""", unsafe_allow_html=True)

st.title("📈 국내주식 스크리너")
st.caption("KOSPI + KOSDAQ · 수정주가 기준 (FDR) · 분기실적 (DART)")

st.radio("차트 스타일", ["업무 모드", "일반 모드"], horizontal=True,
         key="chart_style", label_visibility="collapsed",
         help="업무 모드: 무채색 OHLC 바 · 일반 모드: 빨파 캔들")

@st.cache_data(ttl=86400, show_spinner=False)
def get_universe_top300() -> pd.DataFrame:
    try:
        kospi = fdr.StockListing('KOSPI')
        kosdaq = fdr.StockListing('KOSDAQ')
    except Exception as e:
        st.error(f"종목 리스트 조회 실패: {e}")
        return pd.DataFrame()

    if kospi is None or kospi.empty or kosdaq is None or kosdaq.empty:
        st.error("종목 리스트가 비어있습니다.")
        return pd.DataFrame()

    kospi = kospi.copy(); kosdaq = kosdaq.copy()
    kospi['시장'] = 'KOSPI'; kosdaq['시장'] = 'KOSDAQ'
    df = pd.concat([kospi, kosdaq], ignore_index=True)

    code_col = next((c for c in ['Code','Symbol','종목코드'] if c in df.columns), None)
    name_col = next((c for c in ['Name','종목명'] if c in df.columns), None)
    mc_col = next((c for c in ['Marcap','MarketCap','시가총액'] if c in df.columns), None)

    if not all([code_col, name_col, mc_col]):
        st.error(f"필요 컬럼 누락. 컬럼: {df.columns.tolist()}")
        return pd.DataFrame()

    df = df.dropna(subset=[mc_col])
    df = df.sort_values(mc_col, ascending=False).head(300).reset_index(drop=True)

    out = pd.DataFrame({
        '종목명': df[name_col].values,
        '시장': df['시장'].values,
        '시가총액': df[mc_col].values,
    }, index=df[code_col].astype(str).str.zfill(6).values)
    out.index.name = '티커'
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe_all() -> pd.DataFrame:
    """코스피+코스닥 전체 상장종목 (영업이익 스크리너용)"""
    try:
        kospi = fdr.StockListing('KOSPI')
        kosdaq = fdr.StockListing('KOSDAQ')
    except Exception as e:
        st.error(f"종목 리스트 조회 실패: {e}")
        return pd.DataFrame()
    if kospi is None or kospi.empty or kosdaq is None or kosdaq.empty:
        return pd.DataFrame()

    kospi = kospi.copy(); kosdaq = kosdaq.copy()
    kospi['시장'] = 'KOSPI'; kosdaq['시장'] = 'KOSDAQ'
    df = pd.concat([kospi, kosdaq], ignore_index=True)

    code_col = next((c for c in ['Code','Symbol','종목코드'] if c in df.columns), None)
    name_col = next((c for c in ['Name','종목명'] if c in df.columns), None)
    if not all([code_col, name_col]):
        return pd.DataFrame()

    df[code_col] = df[code_col].astype(str).str.zfill(6)
    # 보통주만 (우선주/스팩 등 코드 끝자리 0이 아닌 종목 제외)
    df = df[df[code_col].str.endswith('0')]
    # 스팩 제외
    df = df[~df[name_col].str.contains('스팩', na=False)]

    out = pd.DataFrame({
        '종목명': df[name_col].values,
        '시장': df['시장'].values,
    }, index=df[code_col].values)
    out.index.name = '티커'
    return out[~out.index.duplicated(keep='first')]


@st.cache_data(ttl=3600, show_spinner=False)
def ohlcv_adjusted(ticker: str, start: str, end: str) -> pd.DataFrame:
    s = yyyymmdd_to_dash(start); e = yyyymmdd_to_dash(end)
    try:
        df = fdr.DataReader(ticker, s, e)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={'Open':'시가','High':'고가','Low':'저가','Close':'종가','Volume':'거래량'})
        if '거래량' not in df.columns: df['거래량'] = 0
        return df[['시가','고가','저가','종가','거래량']]
    except Exception:
        return pd.DataFrame()


def to_weekly(df):
    if df.empty: return df
    return df.resample("W-FRI").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna(subset=["종가"])

def to_monthly(df):
    if df.empty: return df
    return df.resample("ME").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna(subset=["종가"])

def to_quarterly(df):
    if df.empty: return df
    return df.resample("QE").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna(subset=["종가"])


@st.cache_data(ttl=3600, show_spinner=False)
def period_winners(start: str, end: str, top_n: int) -> pd.DataFrame:
    uni = get_universe_top300()
    if uni.empty: return pd.DataFrame()
    rows = []
    bar = st.progress(0.0, text="기간 수익률 계산 중...")
    for i, tkr in enumerate(uni.index):
        bar.progress((i+1)/len(uni), text=f"기간 수익률 계산 중... ({i+1}/{len(uni)})")
        d = ohlcv_adjusted(tkr, start, end)
        if len(d) < 2: continue
        sp, ep = d.iloc[0]["종가"], d.iloc[-1]["종가"]
        if sp <= 0: continue
        rows.append({"티커": tkr, "종목명": uni.loc[tkr,"종목명"], "시장": uni.loc[tkr,"시장"],
                     "시작가": sp, "종료가": ep, "수익률(%)": (ep/sp-1)*100})
    bar.empty()
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("수익률(%)", ascending=False).head(top_n).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def new_high_screen(weeks_list=(26,52)) -> dict:
    uni = get_universe_top300()
    if uni.empty: return {w: pd.DataFrame() for w in weeks_list}
    max_w = max(weeks_list)
    end = kst_now().strftime("%Y%m%d")
    start = (kst_now() - timedelta(days=int(max_w*7*1.5))).strftime("%Y%m%d")
    results = {w: [] for w in weeks_list}
    bar = st.progress(0.0, text="신고가 스크리닝 중...")
    for i, tkr in enumerate(uni.index):
        bar.progress((i+1)/len(uni), text=f"신고가 스크리닝 중... ({i+1}/{len(uni)})")
        daily = ohlcv_adjusted(tkr, start, end)
        if daily.empty: continue
        w = to_weekly(daily)
        if w.empty: continue
        cur_high = w.iloc[-1]["고가"]; cur_close = w.iloc[-1]["종가"]
        for nw in weeks_list:
            if len(w) < nw+1: continue
            prior_max = w.iloc[-nw-1:-1]["고가"].max()
            if cur_high >= prior_max:
                results[nw].append({"티커": tkr, "종목명": uni.loc[tkr,"종목명"], "시장": uni.loc[tkr,"시장"],
                                    "주봉 종가": cur_close, "주봉 고가": cur_high, "직전 최고": prior_max})
    bar.empty()
    return {w: pd.DataFrame(results[w]) for w in weeks_list}


# =====================================================================
# DART 분기 영업이익 스크리너 (탭 3)
# =====================================================================
REPRT_MAP = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
BATCH_SIZE = 90  # 다중회사 주요계정 API 배치 크기


@st.cache_resource(show_spinner=False)
def get_dart():
    key = None
    try:
        key = st.secrets["DART_API_KEY"]
    except Exception:
        key = os.environ.get("DART_API_KEY")
    if not key or not HAS_DART:
        return None
    try:
        return OpenDartReader(key)
    except Exception:
        return None


@st.cache_data(ttl=86400*7, show_spinner=False)
def get_corp_map() -> pd.DataFrame:
    """상장사 stock_code ↔ DART corp_code 매핑"""
    dart = get_dart()
    if dart is None: return pd.DataFrame()
    cc = dart.corp_codes.copy()
    cc['stock_code'] = cc['stock_code'].astype(str).str.strip()
    cc = cc[cc['stock_code'].str.len() == 6]
    return cc[['corp_code', 'stock_code']].drop_duplicates('stock_code')


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_cum_op(year: int, reprt_code: str, corp_codes: tuple, label: str) -> dict:
    """다중회사 주요계정 API로 누적 영업이익 일괄 조회.
    반환: {stock_code: 누적 영업이익(원)} — 연결(CFS) 우선, 없으면 별도(OFS)"""
    dart = get_dart()
    if dart is None: return {}
    out_cfs, out_ofs = {}, {}
    batches = [corp_codes[i:i+BATCH_SIZE] for i in range(0, len(corp_codes), BATCH_SIZE)]
    bar = st.progress(0.0, text=f"{label} 실적 조회 중...")
    for bi, batch in enumerate(batches):
        bar.progress((bi+1)/len(batches), text=f"{label} 실적 조회 중... ({bi+1}/{len(batches)} 배치)")
        try:
            df = dart.finstate(",".join(batch), year, reprt_code=reprt_code)
        except Exception:
            time.sleep(1.0)
            try:
                df = dart.finstate(",".join(batch), year, reprt_code=reprt_code)
            except Exception:
                df = None
        if df is None or df.empty:
            time.sleep(0.4); continue
        op = df[df['account_nm'].isin(['영업이익', '영업이익(손실)'])]
        for _, r in op.iterrows():
            sc = str(r.get('stock_code', '')).strip().zfill(6)
            try:
                amt = float(str(r['thstrm_amount']).replace(',', ''))
            except (ValueError, TypeError):
                continue
            if r.get('fs_div') == 'CFS':
                out_cfs[sc] = amt
            else:
                out_ofs.setdefault(sc, amt)
        time.sleep(0.4)  # rate limit 여유
    bar.empty()
    return {**out_ofs, **out_cfs}  # CFS가 OFS를 덮어씀


def single_q_op(year: int, quarter: int, corp_codes: tuple) -> dict:
    """단일 분기 영업이익 = 당기 누적 − 직전분기 누적 (1분기는 누적 그대로)"""
    cum_now = fetch_cum_op(year, REPRT_MAP[quarter], corp_codes, f"{year}Q{quarter}")
    if quarter == 1:
        return cum_now
    cum_prev = fetch_cum_op(year, REPRT_MAP[quarter-1], corp_codes, f"{year}Q{quarter-1}(누적)")
    return {sc: cum_now[sc] - cum_prev[sc] for sc in cum_now if sc in cum_prev}


@st.cache_data(ttl=86400, show_spinner=False)
def op_growth_screen(year: int, quarter: int, universe_key: str) -> pd.DataFrame:
    """유니버스 전체의 단일분기 영업이익 QoQ/YoY 성장률 테이블"""
    uni = get_universe_all() if universe_key == "전체" else get_universe_top300()
    if uni.empty: return pd.DataFrame()
    cmap = get_corp_map()
    if cmap.empty: return pd.DataFrame()

    merged = uni.reset_index().merge(cmap, left_on='티커', right_on='stock_code', how='inner')
    codes = tuple(merged['corp_code'].tolist())

    q_now = single_q_op(year, quarter, codes)
    qy, qq = (year-1, 4) if quarter == 1 else (year, quarter-1)
    q_qoq = single_q_op(qy, qq, codes)
    q_yoy = single_q_op(year-1, quarter, codes)

    rows = []
    for _, r in merged.iterrows():
        sc = r['티커']
        a, b, c = q_now.get(sc), q_qoq.get(sc), q_yoy.get(sc)
        if a is None or b is None or c is None or b == 0 or c == 0:
            continue
        rows.append({
            "티커": sc, "종목명": r['종목명'], "시장": r['시장'],
            "영업이익(억)": a/1e8,
            "QoQ(%)": (a-b)/abs(b)*100,
            "YoY(%)": (a-c)/abs(c)*100,
        })
    return pd.DataFrame(rows)


def check_new_high(tickers: list, weeks_list=(26, 52)) -> dict:
    """후보 종목에 대해서만 26/52주 주봉 신고가 여부 확인"""
    end = kst_now().strftime("%Y%m%d")
    start = (kst_now() - timedelta(days=int(max(weeks_list)*7*1.5))).strftime("%Y%m%d")
    out = {}
    bar = st.progress(0.0, text="신고가 확인 중...")
    for i, tkr in enumerate(tickers):
        bar.progress((i+1)/len(tickers), text=f"신고가 확인 중... ({i+1}/{len(tickers)})")
        daily = ohlcv_adjusted(tkr, start, end)
        flags = {w: False for w in weeks_list}
        if not daily.empty:
            w = to_weekly(daily)
            if not w.empty:
                cur_high = w.iloc[-1]["고가"]
                for nw in weeks_list:
                    if len(w) >= nw+1 and cur_high >= w.iloc[-nw-1:-1]["고가"].max():
                        flags[nw] = True
        out[tkr] = flags
    bar.empty()
    return out


def latest_confirmed_quarter(now: datetime):
    """공시기한 기준 최근 확정 분기 (Q1: ~5/15, 반기: ~8/14, Q3: ~11/14, 사업보고서: ~3월말)"""
    d = now.date(); y = d.year
    if d >= date(y, 11, 16): return y, 3
    if d >= date(y, 8, 16):  return y, 2
    if d >= date(y, 5, 16):  return y, 1
    if d >= date(y, 4, 1):   return y-1, 4
    return y-1, 3


def plot_candle(df, title, ma_periods=None, height=380):
    if df.empty: return None
    work = st.session_state.get("chart_style", "업무 모드") == "업무 모드"

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.02, row_heights=[0.75, 0.25])

    # 가격 봉
    if work:
        fig.add_trace(go.Ohlc(x=df.index, open=df["시가"], high=df["고가"], low=df["저가"], close=df["종가"],
            name="OHLC",
            increasing=dict(line=dict(color="#374151", width=1)),
            decreasing=dict(line=dict(color="#9ca3af", width=1))), row=1, col=1)
    else:
        fig.add_trace(go.Candlestick(x=df.index, open=df["시가"], high=df["고가"], low=df["저가"], close=df["종가"],
            name="OHLC", increasing_line_color="#ef4444", decreasing_line_color="#3b82f6",
            increasing_fillcolor="#ef4444", decreasing_fillcolor="#3b82f6"), row=1, col=1)

    # 이평선
    if ma_periods:
        if work:
            ma_styles = [
                dict(color="#1e3a8a", width=1.5, dash="solid"),
                dict(color="#64748b", width=1.3, dash="dash"),
                dict(color="#94a3b8", width=1.2, dash="dot"),
                dict(color="#cbd5e1", width=1.2, dash="dashdot"),
            ]
        else:
            ma_styles = [dict(color=c, width=1.5) for c in ["#f59e0b","#10b981","#8b5cf6","#ec4899"]]
        for i, p in enumerate(ma_periods):
            if len(df) >= p:
                ma = df["종가"].rolling(p).mean()
                fig.add_trace(go.Scatter(x=df.index, y=ma, name=f"MA{p}",
                    line=ma_styles[i % len(ma_styles)]), row=1, col=1)

    # 거래량
    if "거래량" in df.columns and df["거래량"].sum() > 0:
        if work:
            fig.add_trace(go.Bar(x=df.index, y=df["거래량"], marker_color="#d1d5db",
                                 name="거래량", showlegend=False, opacity=0.8), row=2, col=1)
        else:
            vol_colors = ["#ef4444" if c >= o else "#3b82f6"
                          for o, c in zip(df["시가"], df["종가"])]
            fig.add_trace(go.Bar(x=df.index, y=df["거래량"], marker_color=vol_colors,
                                 name="거래량", showlegend=False, opacity=0.7), row=2, col=1)

    grid = "#f1f5f9" if work else "#e5e7eb"
    fig.update_layout(title=dict(text=title, font=dict(size=13)),
        height=height + 100, margin=dict(l=8,r=8,t=36,b=20), showlegend=True,
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center", font=dict(size=10)),
        plot_bgcolor="white", font=dict(size=10), dragmode="pan",
        xaxis_rangeslider_visible=False, bargap=0.1)
    fig.update_yaxes(showgrid=True, gridcolor=grid, side="right", type="log", row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor=grid, side="right", row=2, col=1)
    fig.update_xaxes(showgrid=not work, gridcolor=grid)
    return fig


tab1, tab2, tab3 = st.tabs(["📊 기간 수익률 TOP", "🚀 신고가 스크리너", "💰 실적+신고가"])

with tab1:
    st.markdown("##### 기간 설정")
    today_d = kst_now().date()
    c1, c2 = st.columns(2)
    with c1: sdate = st.date_input("시작일", value=today_d - timedelta(days=90), key="t1_s")
    with c2: edate = st.date_input("종료일", value=today_d, key="t1_e")
    top_n = st.slider("상위 종목 수", 5, 30, 20, key="t1_n")

    if st.button("스크리닝 실행", type="primary", key="t1_run", use_container_width=True):
        if sdate >= edate:
            st.error("종료일이 시작일보다 뒤여야 합니다.")
        else:
            with st.spinner("계산 중..."):
                winners = period_winners(sdate.strftime("%Y%m%d"), edate.strftime("%Y%m%d"), top_n)
            st.session_state["winners"] = winners

    if "winners" in st.session_state:
        w = st.session_state["winners"]
        if w.empty:
            st.warning("결과가 비어있습니다. 사이드바에서 캐시를 비우고 다시 시도하세요.")
        else:
            st.markdown(f"##### TOP {len(w)} 결과")
            show = w[["종목명","시장","시작가","종료가","수익률(%)"]].copy()
            show["시작가"] = show["시작가"].map(lambda x: f"{x:,.0f}")
            show["종료가"] = show["종료가"].map(lambda x: f"{x:,.0f}")
            show["수익률(%)"] = show["수익률(%)"].map(lambda x: f"{x:+.2f}")
            st.dataframe(show, use_container_width=True, hide_index=False)

            st.markdown("##### 주봉 차트")
            for _, row in w.iterrows():
                tkr, name, ret = row["티커"], row["종목명"], row["수익률(%)"]
                with st.expander(f"{name} · {tkr} · {ret:+.2f}%"):
                    end = kst_now().strftime("%Y%m%d")
                    start = (kst_now() - timedelta(days=365*2)).strftime("%Y%m%d")
                    daily = ohlcv_adjusted(tkr, start, end)
                    weekly = to_weekly(daily)
                    fig = plot_candle(weekly, f"{name} 주봉 (MA5, MA20)", ma_periods=[5,20])
                    if fig:
                        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"period_{tkr}")
                    else:
                        st.warning("데이터 없음")

with tab2:
    st.markdown("##### 26주 / 52주 주봉 신고가")
    st.caption("현재 주(이번 주) 고가가 직전 N주 고가의 최대치 이상인 종목")
    if st.button("신고가 스크리닝 실행", type="primary", key="t2_run", use_container_width=True):
        with st.spinner("계산 중..."):
            res = new_high_screen([26, 52])
        st.session_state["hi"] = res

    if "hi" in st.session_state:
        res = st.session_state["hi"]
        # 26주 신고가에서 52주 신고가 종목 제외 (52주 신고가는 정의상 26주 신고가)
        res_26_only = res[26].copy()
        if not res[52].empty and not res[26].empty:
            res_26_only = res[26][~res[26]["티커"].isin(res[52]["티커"])].reset_index(drop=True)
        sub1, sub2 = st.tabs([f"26주 ({len(res_26_only)})", f"52주 ({len(res[52])})"])

        def render_block(df, prefix):
            if df.empty:
                st.info("해당 종목이 없습니다."); return
            show = df[["종목명","시장","주봉 종가","주봉 고가","직전 최고"]].copy()
            for c in ["주봉 종가","주봉 고가","직전 최고"]:
                show[c] = show[c].map(lambda x: f"{x:,.0f}")
            st.dataframe(show, use_container_width=True)
            for _, row in df.iterrows():
                tkr, name = row["티커"], row["종목명"]
                with st.expander(f"{name} · {tkr}"):
                    end = kst_now().strftime("%Y%m%d")
                    start = (kst_now() - timedelta(days=365*20)).strftime("%Y%m%d")
                    with st.spinner("장기 데이터 로딩..."):
                        daily = ohlcv_adjusted(tkr, start, end)
                    if daily.empty:
                        st.warning("데이터 없음"); continue
                    listed_from = daily.index.min().strftime("%Y-%m-%d")
                    st.caption(f"데이터 시작: {listed_from}")
                    q = to_quarterly(daily); m = to_monthly(daily)
                    fig_q = plot_candle(q, f"{name} 분기봉 · MA3", ma_periods=[3], height=360)
                    if fig_q: st.plotly_chart(fig_q, use_container_width=True, config={"displayModeBar": False}, key=f"{prefix}_q_{tkr}")
                    fig_m = plot_candle(m, f"{name} 월봉 · MA5, MA10", ma_periods=[5,10], height=360)
                    if fig_m: st.plotly_chart(fig_m, use_container_width=True, config={"displayModeBar": False}, key=f"{prefix}_m_{tkr}")

        with sub1: render_block(res_26_only, "w26")
        with sub2: render_block(res[52], "w52")

with tab3:
    st.markdown("##### 영업이익 QoQ+YoY 상위 % + 신고가")
    st.caption("직전 확정분기 단일 영업이익의 QoQ·YoY 성장률이 동시에 유니버스 상위 N% "
               "이면서 26주 또는 52주 주봉 신고가인 종목")

    if not HAS_DART:
        st.error(f"OpenDartReader 로드 실패: {DART_IMPORT_ERR}")
    elif get_dart() is None:
        st.error("DART API 키가 없습니다. Streamlit Cloud → Settings → Secrets에 "
                 "`DART_API_KEY = \"발급키\"` 를 추가하세요. "
                 "(GitHub Actions Secrets와는 별개입니다)")
    else:
        dy, dq = latest_confirmed_quarter(kst_now())
        c1, c2, c3 = st.columns(3)
        with c1:
            sel_year = st.selectbox("연도", list(range(dy, dy-3, -1)), index=0, key="t3_y")
        with c2:
            sel_q = st.selectbox("분기", [1, 2, 3, 4], index=dq-1, key="t3_q",
                                 format_func=lambda x: f"{x}분기")
        with c3:
            pct = st.selectbox("상위 %", [5, 10, 15, 20], index=3, key="t3_pct")

        c4, c5 = st.columns(2)
        with c4:
            uni_key = st.radio("유니버스", ["전체", "시총300"], index=1, horizontal=True, key="t3_uni",
                               help="전체: 코스피+코스닥 보통주 전체 (첫 실행 시 5분 내외 소요)")
        with c5:
            positive_only = st.checkbox("영업이익 흑자만", value=True, key="t3_pos")

        st.caption(f"기본값: 최근 확정분기 = {dy}년 {dq}분기 (공시기한 기준)")

        if st.button("실적+신고가 스크리닝 실행", type="primary", key="t3_run", use_container_width=True):
            with st.spinner("DART 실적 조회 중... (최초 실행은 수 분 소요, 이후 24시간 캐시)"):
                growth = op_growth_screen(sel_year, sel_q, uni_key)
            if growth.empty:
                st.warning("실적 데이터를 가져오지 못했습니다. 아직 공시 전이거나 API 한도 초과일 수 있습니다.")
            else:
                base = growth[growth["영업이익(억)"] > 0] if positive_only else growth
                q_cut = np.percentile(base["QoQ(%)"], 100 - pct)
                y_cut = np.percentile(base["YoY(%)"], 100 - pct)
                cand = base[(base["QoQ(%)"] >= q_cut) & (base["YoY(%)"] >= y_cut)].copy()

                st.info(f"성장률 계산 {len(growth)}종목 → QoQ 컷 {q_cut:+.1f}% · YoY 컷 {y_cut:+.1f}% "
                        f"→ 동시 통과 {len(cand)}종목")

                if cand.empty:
                    st.warning("조건 통과 종목이 없습니다.")
                else:
                    hi = check_new_high(cand["티커"].tolist(), (26, 52))
                    cand["26주 신고가"] = cand["티커"].map(lambda t: hi[t][26])
                    cand["52주 신고가"] = cand["티커"].map(lambda t: hi[t][52])
                    final = cand[cand["26주 신고가"] | cand["52주 신고가"]] \
                                .sort_values("YoY(%)", ascending=False).reset_index(drop=True)
                    st.session_state["t3_final"] = final
                    st.session_state["t3_meta"] = (sel_year, sel_q, pct)

        if "t3_final" in st.session_state:
            final = st.session_state["t3_final"]
            my, mq, mpct = st.session_state.get("t3_meta", (sel_year, sel_q, pct))
            st.markdown(f"##### 결과: {my}년 {mq}분기 · 상위 {mpct}% · {len(final)}종목")
            if final.empty:
                st.info("실적 조건 통과 종목 중 신고가 종목이 없습니다.")
            else:
                show = final[["종목명","시장","영업이익(억)","QoQ(%)","YoY(%)","26주 신고가","52주 신고가"]].copy()
                show["영업이익(억)"] = show["영업이익(억)"].map(lambda x: f"{x:,.0f}")
                show["QoQ(%)"] = show["QoQ(%)"].map(lambda x: f"{x:+.1f}")
                show["YoY(%)"] = show["YoY(%)"].map(lambda x: f"{x:+.1f}")
                show["26주 신고가"] = show["26주 신고가"].map(lambda x: "✓" if x else "")
                show["52주 신고가"] = show["52주 신고가"].map(lambda x: "✓" if x else "")
                st.dataframe(show, use_container_width=True)

                st.markdown("##### 주봉 차트")
                for _, row in final.iterrows():
                    tkr, name = row["티커"], row["종목명"]
                    tag = "52주" if row["52주 신고가"] else "26주"
                    with st.expander(f"{name} · {tkr} · YoY {row['YoY(%)']:+.0f}% · {tag} 신고가"):
                        end = kst_now().strftime("%Y%m%d")
                        start = (kst_now() - timedelta(days=365*2)).strftime("%Y%m%d")
                        daily = ohlcv_adjusted(tkr, start, end)
                        weekly = to_weekly(daily)
                        fig = plot_candle(weekly, f"{name} 주봉 (MA5, MA20)", ma_periods=[5,20])
                        if fig:
                            st.plotly_chart(fig, use_container_width=True,
                                            config={"displayModeBar": False}, key=f"t3_{tkr}")
                        else:
                            st.warning("데이터 없음")

with st.sidebar:
    st.header("⚙️ 관리")
    st.caption(f"현재 KST: {kst_now().strftime('%Y-%m-%d %H:%M')}")
    if st.button("캐시 비우기", use_container_width=True):
        st.cache_data.clear()
        for k in ["winners","hi","t3_final","t3_meta"]:
            st.session_state.pop(k, None)
        st.success("캐시 비움")
        st.rerun()
    st.divider()
    st.caption("데이터: FinanceDataReader (수정주가)\nDART Open API (분기실적)")
