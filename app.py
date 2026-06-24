# -*- coding: utf-8 -*-
"""
국내주식 스크리너 (모바일 최적화)
- 대상: KOSPI + KOSDAQ 시가총액 200위
- 기능 1) 기간 수익률 상위 N종목 + 주봉 차트
- 기능 2) 주봉 26주/52주 신고가 종목 + 20년 분기봉/월봉 차트
- 모든 가격은 수정주가 (adjusted=True)
"""

import streamlit as st
import pandas as pd
import numpy as np
from pykrx import stock
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

def kst_now() -> datetime:
    return datetime.now(KST)

st.set_page_config(
    page_title="국내주식 스크리너",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

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
st.caption("KOSPI + KOSDAQ 시총 200위 · 수정주가 기준")

@st.cache_data(ttl=86400, show_spinner=False)
def recent_business_day() -> str:
    """KST 기준 어제부터 거꾸로 거래일 탐색"""
    base = kst_now() - timedelta(days=1)
    for i in range(15):
        d = (base - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_cap_by_ticker(d, market="KOSPI")
            if df is not None and not df.empty and "시가총액" in df.columns and df["시가총액"].sum() > 0:
                return d
        except Exception:
            continue
    return (kst_now() - timedelta(days=7)).strftime("%Y%m%d")


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe_top200() -> pd.DataFrame:
    d = recent_business_day()
    try:
        kospi = stock.get_market_cap_by_ticker(d, market="KOSPI")
        kosdaq = stock.get_market_cap_by_ticker(d, market="KOSDAQ")
    except Exception as e:
        st.error(f"KRX 시가총액 조회 실패 ({d}): {e}")
        return pd.DataFrame()

    if kospi is None or kospi.empty or kosdaq is None or kosdaq.empty:
        st.error(f"빈 데이터 · 날짜={d}")
        return pd.DataFrame()

    if "시가총액" not in kospi.columns or "시가총액" not in kosdaq.columns:
        st.error(f"'시가총액' 컬럼 누락. 컬럼: {kospi.columns.tolist()}")
        return pd.DataFrame()

    cap = pd.concat([kospi, kosdaq]).sort_values("시가총액", ascending=False).head(200)
    names = []
    for t in cap.index:
        try:
            names.append(stock.get_market_ticker_name(t))
        except Exception:
            names.append(t)
    cap["종목명"] = names
    cap["시장"] = ["KOSPI" if t in kospi.index else "KOSDAQ" for t in cap.index]
    return cap


@st.cache_data(ttl=3600, show_spinner=False)
def ohlcv_adjusted(ticker: str, start: str, end: str) -> pd.DataFrame:
    try:
        df = stock.get_market_ohlcv(start, end, ticker, adjusted=True)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception:
        return pd.DataFrame()


def to_weekly(df):
    if df.empty: return df
    return df.resample("W-FRI").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna(subset=["종가"])

def to_monthly(df):
    if df.empty: return df
    return df.resample("M").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna(subset=["종가"])

def to_quarterly(df):
    if df.empty: return df
    return df.resample("Q").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna(subset=["종가"])


@st.cache_data(ttl=3600, show_spinner=False)
def period_winners(start: str, end: str, top_n: int) -> pd.DataFrame:
    uni = get_universe_top200()
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
    uni = get_universe_top200()
    if uni.empty: return {w: pd.DataFrame() for w in weeks_list}
    max_w = max(weeks_list)
    end = recent_business_day()
    start = (datetime.strptime(end,"%Y%m%d") - timedelta(days=int(max_w*7*1.5))).strftime("%Y%m%d")
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


def plot_candle(df, title, ma_periods=None, height=380):
    if df.empty: return None
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df.index, open=df["시가"], high=df["고가"], low=df["저가"], close=df["종가"],
        name="OHLC", increasing_line_color="#ef4444", decreasing_line_color="#3b82f6",
        increasing_fillcolor="#ef4444", decreasing_fillcolor="#3b82f6"))
    if ma_periods:
        palette = ["#f59e0b","#10b981","#8b5cf6","#ec4899"]
        for i, p in enumerate(ma_periods):
            if len(df) >= p:
                ma = df["종가"].rolling(p).mean()
                fig.add_trace(go.Scatter(x=df.index, y=ma, name=f"MA{p}",
                    line=dict(width=1.5, color=palette[i % len(palette)])))
    fig.update_layout(title=dict(text=title, font=dict(size=13)), xaxis_rangeslider_visible=False,
        height=height, margin=dict(l=8,r=8,t=36,b=20), showlegend=True,
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center", font=dict(size=10)),
        xaxis=dict(showgrid=True, gridcolor="#e5e7eb"),
        yaxis=dict(showgrid=True, gridcolor="#e5e7eb", side="right"),
        plot_bgcolor="white", font=dict(size=10), dragmode="pan")
    return fig


tab1, tab2 = st.tabs(["📊 기간 수익률 TOP", "🚀 신고가 스크리너"])

with tab1:
    st.markdown("##### 기간 설정")
    today_kst_d = kst_now().date()
    c1, c2 = st.columns(2)
    with c1:
        sdate = st.date_input("시작일", value=today_kst_d - timedelta(days=90), key="t1_s")
    with c2:
        edate = st.date_input("종료일", value=today_kst_d, key="t1_e")
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
            st.warning("데이터를 가져오지 못했습니다. 사이드바에서 캐시를 비우고 다시 시도하세요.")
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
                    end = recent_business_day()
                    start = (datetime.strptime(end,"%Y%m%d") - timedelta(days=365*2)).strftime("%Y%m%d")
                    daily = ohlcv_adjusted(tkr, start, end)
                    weekly = to_weekly(daily)
                    fig = plot_candle(weekly, f"{name} 주봉 (MA5, MA20)", ma_periods=[5,20])
                    if fig:
                        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
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
        sub1, sub2 = st.tabs([f"26주 ({len(res[26])})", f"52주 ({len(res[52])})"])

        def render_block(df):
            if df.empty:
                st.info("해당 종목이 없습니다.")
                return
            show = df[["종목명","시장","주봉 종가","주봉 고가","직전 최고"]].copy()
            for c in ["주봉 종가","주봉 고가","직전 최고"]:
                show[c] = show[c].map(lambda x: f"{x:,.0f}")
            st.dataframe(show, use_container_width=True)
            for _, row in df.iterrows():
                tkr, name = row["티커"], row["종목명"]
                with st.expander(f"{name} · {tkr}"):
                    end = recent_business_day()
                    start = (datetime.strptime(end,"%Y%m%d") - timedelta(days=365*20)).strftime("%Y%m%d")
                    with st.spinner("장기 데이터 로딩..."):
                        daily = ohlcv_adjusted(tkr, start, end)
                    if daily.empty:
                        st.warning("데이터 없음"); continue
                    listed_from = daily.index.min().strftime("%Y-%m-%d")
                    st.caption(f"데이터 시작: {listed_from}")
                    q = to_quarterly(daily); m = to_monthly(daily)
                    fig_q = plot_candle(q, f"{name} 분기봉 · MA3", ma_periods=[3], height=360)
                    if fig_q: st.plotly_chart(fig_q, use_container_width=True, config={"displayModeBar": False})
                    fig_m = plot_candle(m, f"{name} 월봉 · MA5, MA10", ma_periods=[5,10], height=360)
                    if fig_m: st.plotly_chart(fig_m, use_container_width=True, config={"displayModeBar": False})

        with sub1: render_block(res[26])
        with sub2: render_block(res[52])

with st.sidebar:
    st.header("⚙️ 관리")
    st.caption(f"현재 KST: {kst_now().strftime('%Y-%m-%d %H:%M')}")
    st.caption(f"기준 영업일: {recent_business_day()}")
    if st.button("캐시 비우기", use_container_width=True):
        st.cache_data.clear()
        for k in ["winners","hi"]:
            st.session_state.pop(k, None)
        st.success("캐시 비움")
        st.rerun()
    st.divider()
    st.caption("데이터: KRX (pykrx)\n수정주가 사용")
