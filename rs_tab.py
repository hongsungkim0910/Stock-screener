# -*- coding: utf-8 -*-
"""
Streamlit RS 탭 — app.py에서 사용:

    from rs_tab import render_rs_tab
    ...
    with tab4:
        render_rs_tab(ohlcv_adjusted, to_weekly, plot_candle)

app.py의 차트 헬퍼(ohlcv_adjusted, to_weekly, plot_candle)를 인자로 넘겨받아
기간 수익률 탭과 동일한 주봉 차트(업무 모드 연동)를 종목별 expander로 표시.
데이터는 rs_screener.py(GitHub Actions 주간 실행)가 커밋한 docs/rs_latest.csv를
읽기만 하므로 탭 로딩이 즉시 끝남 (전 종목 스캔 10~15분을 앱에서 돌리지 않음).
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

CSV_PATH = "docs/rs_latest.csv"
KST = timezone(timedelta(hours=9))


@st.cache_data(ttl=3600)
def load_rs() -> tuple[pd.DataFrame, str]:
    df = pd.read_csv(CSV_PATH, dtype={"티커": str}).set_index("티커")
    mtime = datetime.fromtimestamp(os.path.getmtime(CSV_PATH), tz=timezone.utc)
    return df, mtime.strftime("%Y-%m-%d")


def render_rs_tab(ohlcv_adjusted=None, to_weekly=None, plot_candle=None):
    st.subheader("오닐 RS 상대강도 랭킹")
    st.caption(
        "점수 = 3개월 수익률×2 + 6 + 9 + 12개월 (수정주가) → 백분위 1~99. "
        "유니버스: 전체 보통주, 시총 하위 20%·20일 거래대금 하위 30% 제외. "
        "🆕=상장 12개월 미만(부족 기간은 상장 후 수익률 대체). 매주 토요일 자동 갱신."
    )
    try:
        df, updated = load_rs()
    except FileNotFoundError:
        st.info("아직 RS 데이터가 없습니다. 주간 워크플로가 한 번 실행되면 표시됩니다.")
        return

    st.caption(f"데이터 기준: {updated} · 랭킹 대상 {len(df):,}종목")

    c1, c2, c3 = st.columns(3)
    rs_min = c1.slider("RS 하한", 1, 99, 90)
    market = c2.selectbox("시장", ["전체", "KOSPI", "KOSDAQ"])
    only_new = c3.checkbox("신규상장만 (🆕)", value=False)

    view = df[df["RS"] >= rs_min]
    if market != "전체":
        view = view[view["시장"] == market]
    if only_new and "신규" in view.columns:
        view = view[view["신규"] == True]  # noqa: E712

    st.markdown(f"##### 결과 {len(view)}종목")
    show = view.copy()
    if "신규" in show.columns:
        show["종목명"] = show.apply(
            lambda r: f"{r['종목명']} 🆕" if r["신규"] else r["종목명"], axis=1)
        show = show.drop(columns=["신규"])
    show = show.rename(columns={"r3": "3개월%", "r6": "6개월%", "r9": "9개월%",
                                "r12": "12개월%", "close": "종가",
                                "거래대금20": "거래대금(20일평균)"})
    st.dataframe(
        show.style.format({"3개월%": "{:+.0f}", "6개월%": "{:+.0f}",
                           "9개월%": "{:+.0f}", "12개월%": "{:+.0f}",
                           "종가": "{:,.0f}", "거래대금(20일평균)": "{:,.0f}"}),
        use_container_width=True, height=480,
    )

    # ---------- 주봉 차트 (기간 수익률 탭과 동일 스타일) ----------
    if not (ohlcv_adjusted and to_weekly and plot_candle):
        return
    st.markdown("##### 주봉 차트")
    chart_n = st.slider("차트 표시 종목 수 (상위)", 5, 30, 15, key="rs_chart_n")
    now = datetime.now(KST)
    end = now.strftime("%Y%m%d")
    start = (now - timedelta(days=365 * 2)).strftime("%Y%m%d")
    for tkr, row in view.head(chart_n).iterrows():
        new_tag = " 🆕" if row.get("신규") else ""
        with st.expander(f"{row['종목명']} · {tkr} · RS {row['RS']}{new_tag}"):
            daily = ohlcv_adjusted(tkr, start, end)
            weekly = to_weekly(daily)
            fig = plot_candle(weekly, f"{row['종목명']} 주봉 (MA5, MA20)", ma_periods=[5, 20])
            if fig:
                st.plotly_chart(fig, use_container_width=True,
                                config={"displayModeBar": False}, key=f"rs_{tkr}")
            else:
                st.warning("데이터 없음")
