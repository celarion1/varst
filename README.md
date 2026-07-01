# Portfolio VaR Calculator — Streamlit

## 실행 방법

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 앱 실행
```bash
streamlit run app.py
```

브라우저가 자동으로 열리고 다음 주소로 접근됩니다:
```
http://localhost:8501
```

---

## 화면 구성

| 탭 | 기능 |
|---|---|
| 📁 포트폴리오 | KOSPI / KOSDAQ / 한국채권 / 한국파생 / NYSE / NASDAQ / 미국채권 종목 추가·삭제 |
| 📈 VaR/Exposure 추이 | 최근 1년 일별 산점도 (X=Exposure, Y=VaR), 시계열 차트 |
| ⚙️ VaR 계산 | 신뢰수준·보유기간·상관관계 설정 후 계산 실행 |
| 📋 결과 요약 | 자산군별 VaR 막대·도넛 차트, 종목별 상세 테이블, 수익률 분포 |
| ⚖️ 포트폴리오 비교 | My Portfolio 복사본 생성 후 VaR 변화 비교 |
| 🔥 스트레스 테스트 | 금융위기·COVID·지정학 시나리오별 충격 VaR |

## 사이드바 설정

- **USD/KRW 환율**: 미국 자산 KRW 환산에 사용
- **신뢰수준**: 99% (바젤) / 95%
- **보유기간**: 1~250 영업일
- **자산군 간 상관관계**: 주식↔채권, 주식↔옵션, 채권↔옵션
- **포트폴리오 복사/삭제**: My Portfolio N 생성 및 비교

## 파일 구조

```
var_streamlit/
├── app.py           ← 메인 앱 (단일 파일)
├── requirements.txt ← 의존성
└── README.md
```

## yfinance 실시간 연동 (선택)

현재는 GBM 샘플 데이터로 동작합니다.
실제 yfinance 연동은 `app.py` 내 `sample_returns()` 함수를
아래로 교체하면 됩니다:

```python
import yfinance as yf

def get_real_returns(ticker, period=252):
    end = datetime.today()
    start = end - timedelta(days=int(period * 1.5))
    hist = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if hist.empty:
        return sample_returns(ticker, period)
    prices = hist["Close"].squeeze()
    return np.log(prices / prices.shift(1)).dropna().tail(period).values
```
