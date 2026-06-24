# 국내주식 스크리너 (모바일)

KOSPI + KOSDAQ 시가총액 200위 대상.
모든 가격은 **수정주가**(`adjusted=True`)를 사용합니다.

## 기능
1. **기간 수익률 TOP** — 특정 시작일~종료일 상승률 상위 N개 + 각 종목 주봉 차트 (MA5, MA20)
2. **신고가 스크리너** — 주봉 26주/52주 신고가 종목 + 분기봉 (MA3) · 월봉 (MA5, MA10) 차트 (최대 20년)

---

## 폰에서 보는 방법: Streamlit Community Cloud (무료, 권장)

### 1단계. GitHub에 업로드
1. [GitHub](https://github.com) 새 저장소 생성 (예: `kr-stock-screener`, public)
2. 다음 3개 파일 업로드:
   - `app.py`
   - `requirements.txt`
   - `README.md`

### 2단계. Streamlit Cloud에 배포
1. [share.streamlit.io](https://share.streamlit.io) 접속 → GitHub 계정으로 로그인
2. **New app** → 위에서 만든 저장소 선택 → Main file path: `app.py` → **Deploy**
3. 1~2분 후 `https://<앱이름>.streamlit.app` 형태의 URL 발급

### 3단계. 폰에서 접속
- 발급된 URL을 폰 브라우저로 열고 홈 화면에 추가 (iOS Safari: 공유 → 홈 화면에 추가 / Android Chrome: 메뉴 → 홈 화면에 추가)
- 앱처럼 동작합니다.

> Streamlit Cloud는 무료 플랜에서 일정 시간 미사용 시 슬립합니다.
> 슬립 상태에서 접속하면 처음 한 번 30초~1분 정도 깨어나는 시간이 필요합니다.

---

## 로컬 PC에서 실행하고 폰으로 접속하는 방법

PC를 켜둘 수 있다면 가장 빠른 방법입니다.

```bash
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0
```

PC의 내부 IP가 `192.168.0.10`이면, 같은 Wi-Fi 환경의 폰에서
`http://192.168.0.10:8501` 로 접속.

---

## 캐싱과 성능 메모

- 시총 200위 명단: 1일 캐시
- 가격 데이터: 1시간 캐시
- 처음 실행 시 신고가 스크리닝은 KRX에서 200종목을 순회 조회하므로 **2~3분** 소요. 이후엔 캐시로 즉시 표시.
- 사이드바의 **캐시 비우기**로 강제 재조회 가능.

## 신고가 정의
이번 주(가장 최근 주봉)의 **고가**가 직전 N주(N=26 또는 52) **고가의 최댓값** 이상.
즉 "이번 주에 신고가를 갱신한 종목"만 잡힙니다. 더 느슨하게 (예: 최근 2주 내) 보고 싶으면 `new_high_screen` 함수의 슬라이싱 부분만 수정하세요.
