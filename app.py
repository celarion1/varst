import streamlit as st
import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    YF_OK = True
except ImportError:
    YF_OK = False

import plotly.graph_objects as go

# ── 페이지 설정 ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Portfolio VaR Calculator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

COLORS = ["#185FA5", "#A32D2D", "#3B6D11", "#854F0B", "#534AB7", "#0F6E56"]
DT = 1 / 252
MJD_MU_J  = -0.05
MJD_SIG_J =  0.10

# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 분석 파라미터")

    st.subheader("기본 설정")
    USDKRW   = st.number_input("USD/KRW 환율", value=1370, min_value=800, max_value=2000, step=10)
    CONF     = st.selectbox("신뢰수준", [0.99, 0.95], format_func=lambda x: f"{int(x*100)}%")
    HOLDING  = st.slider("보유기간 (영업일)", 1, 30, 10)
    EWMA_LAM = st.slider("EWMA λ", 0.90, 0.99, 0.94, 0.01)
    OBS_DAYS = st.select_slider("관측기간 (영업일)", options=[252, 504, 756], value=504)

    st.divider()
    st.subheader("시뮬레이션")
    SIM_N      = st.slider("경로 수", 500, 5000, 2000, 500)
    SIM_T_DAYS = st.slider("예측 기간 (영업일)", 5, 126, 63, 5)
    T_DF       = st.slider("t-분포 자유도 ν", 3, 30, 5)
    MJD_LAM    = st.slider("점프 강도 λ (연간)", 0.5, 10.0, 3.0, 0.5)
    BS_BLOCK   = st.slider("Bootstrap 블록 크기", 3, 30, 10)

# ── 헤더 ──────────────────────────────────────────────────────────────────
st.title("📊 Portfolio VaR Calculator")
st.caption("EWMA Parametric VaR · Merton Jump Diffusion · Historical Block Bootstrap · t-분포 Monte Carlo")
st.divider()

# ── 포트폴리오 편집 ──────────────────────────────────────────────────────
st.subheader("📋 포트폴리오 구성")
st.caption("티커, 수량, 가격을 수정하거나 행을 추가/삭제할 수 있습니다.")

default_positions = pd.DataFrame([
    {"ticker": "005930.KS", "type": "Stock",  "qty": 100,   "price": 75000.0, "ccy": "KRW", "mult": 1},
    {"ticker": "AAPL",      "type": "Stock",  "qty": 50,    "price": 185.0,   "ccy": "USD", "mult": 1},
    {"ticker": "K200C",     "type": "Option", "qty": 10,    "price": 257.0,   "ccy": "KRW", "mult": 250000},
    {"ticker": "KR-BOND",   "type": "Bond",   "qty": 10000, "price": 10000.0, "ccy": "KRW", "mult": 1},
])

portfolio_df = st.data_editor(
    default_positions,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "ticker": st.column_config.TextColumn("티커"),
        "type":   st.column_config.SelectboxColumn("유형", options=["Stock", "Bond", "Option", "Future"]),
        "qty":    st.column_config.NumberColumn("수량", format="%d"),
        "price":  st.column_config.NumberColumn("가격", format="%.2f"),
        "ccy":    st.column_config.SelectboxColumn("통화", options=["KRW", "USD"]),
        "mult":   st.column_config.NumberColumn("승수", format="%d"),
    },
    hide_index=True,
)

# 포트폴리오 계산
POSITIONS = portfolio_df.dropna(subset=["ticker"]).to_dict("records")
if not POSITIONS:
    st.warning("포트폴리오에 종목을 추가해주세요.")
    st.stop()

mv_list  = [p["price"] * p["qty"] * (USDKRW if p.get("ccy") == "USD" else 1) * p.get("mult", 1) for p in POSITIONS]
total_mv = sum(mv_list)
weights  = [mv / total_mv for mv in mv_list] if total_mv > 0 else [1 / len(POSITIONS)] * len(POSITIONS)
tickers  = [p["ticker"] for p in POSITIONS]

c1, c2, c3, c4 = st.columns(4)
c1.metric("총 종목", f"{len(POSITIONS)}개")
c2.metric("총 평가금액", f"{total_mv / 1e8:.2f}억원")
c3.metric("신뢰수준", f"{int(CONF * 100)}%")
c4.metric("보유기간", f"{HOLDING}일")

st.divider()
run_btn = st.button("🚀 분석 실행", type="primary", use_container_width=True)

if not run_btn and "analysis_ready" not in st.session_state:
    st.info("포트폴리오를 확인하고 **분석 실행** 버튼을 눌러주세요.")
    st.stop()

st.session_state["analysis_ready"] = True

# ── 분석 함수 ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_returns(ticker: str, days: int):
    if YF_OK:
        try:
            end   = datetime.today()
            start = end - timedelta(days=int(days * 1.6))
            d = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                            end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
            if not d.empty and len(d) >= 60:
                px_  = d["Close"].squeeze()
                ret  = np.log(px_ / px_.shift(1)).dropna().values
                return ret[-days:], float(px_.iloc[-1]), True
        except Exception:
            pass
    rng   = np.random.default_rng(abs(hash(ticker)) % 9999)
    sigma = 0.22 / np.sqrt(252)
    ret   = rng.normal(-0.5 * sigma ** 2, sigma, days)
    return ret, 50000.0, False


def ewma_vol(returns, lam=0.94):
    n = len(returns)
    v = np.zeros(n)
    v[0] = returns[0] ** 2
    for t in range(1, n):
        v[t] = lam * v[t - 1] + (1 - lam) * returns[t - 1] ** 2
    return np.sqrt(v)


def ewma_cov_matrix(ret_matrix, lam=0.94):
    T, n = ret_matrix.shape
    w    = np.array([(1 - lam) * lam ** (T - 1 - t) for t in range(T)])
    w   /= w.sum()
    mu   = ret_matrix.T @ w
    d    = ret_matrix - mu
    return (d * w[:, None]).T @ d


def parametric_var_ewma(ret, mv, holding=10, conf=0.99, lam=0.94):
    sig_d = ewma_vol(ret, lam)[-1]
    z     = stats.norm.ppf(conf)
    var   = z * sig_d * np.sqrt(holding) * mv
    cvar  = (stats.norm.pdf(z) / (1 - conf)) * sig_d * np.sqrt(holding) * mv
    return {"sigma_d": sig_d, "var": var, "cvar": cvar, "var_pct": var / mv if mv else 0}


def portfolio_var_ewma(w_arr, cov_mat, total_mv, conf=0.99, holding=10):
    w        = np.array(w_arr)
    port_var = float(w @ cov_mat @ w)
    sig_p    = np.sqrt(max(port_var, 1e-30))
    z        = stats.norm.ppf(conf)
    pf_var   = z * sig_p * np.sqrt(holding) * total_mv
    cov_w    = cov_mat @ w
    mvar     = z * np.sqrt(holding) * cov_w / sig_p
    comp     = w * mvar * total_mv
    beta     = cov_w / port_var if port_var > 0 else np.zeros(len(w))
    return {"pf_var": pf_var, "pf_sigma": sig_p, "mvar": mvar, "comp": comp, "beta": beta}


def incremental_var(w_arr, cov_mat, total_mv, conf=0.99, holding=10):
    w        = np.array(w_arr)
    base_var = portfolio_var_ewma(w, cov_mat, total_mv, conf, holding)["pf_var"]
    ivars    = []
    for i in range(len(w)):
        mask = [j for j in range(len(w)) if j != i]
        if not mask:
            ivars.append(base_var); continue
        w_ex   = w[mask]; w_ex = w_ex / w_ex.sum()
        c_ex   = cov_mat[np.ix_(mask, mask)]
        mv_ex  = total_mv * w[mask].sum()
        ivars.append(base_var - portfolio_var_ewma(w_ex, c_ex, mv_ex, conf, holding)["pf_var"])
    return ivars, base_var


@st.cache_data(show_spinner=False)
def run_mjd(S0, mu, sigma, lam, T, dt, n):
    rng   = np.random.default_rng(42)
    steps = int(T / dt)
    kappa = np.exp(MJD_MU_J + 0.5 * MJD_SIG_J ** 2) - 1
    drift = (mu - lam * kappa - 0.5 * sigma ** 2) * dt
    paths = np.zeros((n, steps + 1)); paths[:, 0] = S0
    for t in range(steps):
        Z = rng.standard_normal(n)
        N = rng.poisson(lam * dt, n)
        J = rng.normal(MJD_MU_J, MJD_SIG_J, n) * N
        paths[:, t + 1] = paths[:, t] * np.exp(drift + sigma * np.sqrt(dt) * Z + J)
    return paths


@st.cache_data(show_spinner=False)
def run_bootstrap(S0, hist_rets_tuple, T, dt, n, block):
    rng    = np.random.default_rng(42)
    hist   = np.array(hist_rets_tuple)
    steps  = int(T / dt)
    n_hist = len(hist)
    max_st = max(n_hist - block, 1)
    paths  = np.zeros((n, steps + 1)); paths[:, 0] = S0
    for p_idx in range(n):
        sampled = []
        while len(sampled) < steps:
            s = int(rng.integers(0, max_st))
            sampled.extend(hist[s:s + block].tolist())
        ret_seq = np.array(sampled[:steps])
        paths[p_idx, 1:] = S0 * np.exp(np.cumsum(ret_seq))
    return paths


@st.cache_data(show_spinner=False)
def run_t_mc(S0, mu, sigma, df, T, dt, n):
    rng   = np.random.default_rng(42)
    steps = int(T / dt)
    scale = sigma * np.sqrt(dt) * (np.sqrt((df - 2) / df) if df > 2 else 1)
    drift = (mu - 0.5 * sigma ** 2) * dt
    paths = np.zeros((n, steps + 1)); paths[:, 0] = S0
    draws = stats.t.rvs(df=df, size=(n, steps), random_state=int(rng.integers(0, 2 ** 31)))
    for t in range(steps):
        paths[:, t + 1] = paths[:, t] * np.exp(drift + scale * draws[:, t])
    return paths


def calc_sim_metrics(paths, S0, conf=0.99):
    terminal = paths[:, -1]
    lr       = np.log(terminal / S0)
    var_pct  = float(np.percentile(lr, (1 - conf) * 100))
    mask     = lr <= var_pct
    cvar_pct = float(lr[mask].mean()) if mask.any() else var_pct
    return {
        "기대수익률(%)":          round(float(np.mean(lr)) * 100, 2),
        "표준편차(%)":            round(float(np.std(lr, ddof=1)) * 100, 2),
        f"VaR{int(conf*100)}%(%)":  round(var_pct * 100, 2),
        f"CVaR{int(conf*100)}%(%)": round(cvar_pct * 100, 2),
        "손실확률(%)":            round(float((terminal < S0).mean()) * 100, 1),
        "5%분위주가":             round(float(np.percentile(terminal, 5)), 0),
        "중앙값주가":             round(float(np.median(terminal)), 0),
        "95%분위주가":            round(float(np.percentile(terminal, 95)), 0),
        "기대주가":               round(float(np.mean(terminal)), 0),
    }


def plot_fan(paths, S0, title):
    steps    = paths.shape[1]
    t_axis   = np.arange(steps) * DT * 252
    terminal = paths[:, -1]
    i_best   = int(np.argmax(terminal))
    i_worst  = int(np.argmin(terminal))
    i_med    = int(np.argmin(np.abs(terminal - np.median(terminal))))
    sample   = np.random.default_rng(0).choice(len(paths), min(150, len(paths)), replace=False)
    fig = go.Figure()
    for i in sample:
        if i in (i_best, i_worst, i_med): continue
        fig.add_trace(go.Scatter(x=t_axis, y=paths[i].tolist(), mode="lines",
                                 line=dict(color="rgba(55,138,221,0.05)", width=0.7),
                                 showlegend=False, hoverinfo="skip"))
    for idx, col, nm, dash in [(i_worst, "#222", "최악", "dot"),
                                (i_med,   "#A32D2D", "중앙값", "solid"),
                                (i_best,  "#3B6D11", "최선", "dash")]:
        fig.add_trace(go.Scatter(x=t_axis, y=paths[idx].tolist(), mode="lines",
                                 name=nm, line=dict(color=col, width=2.5, dash=dash)))
    fig.add_hline(y=S0, line_dash="dash", line_color="#888",
                  annotation_text=f"현재가 {S0:,.0f}")
    fig.update_layout(title=title, xaxis_title="영업일", yaxis_title="주가",
                      height=360, legend=dict(orientation="h", y=-0.25),
                      margin=dict(t=40, b=60))
    return fig


def plot_hist_dist(paths, S0, title, conf):
    terminal  = paths[:, -1]
    var_price = float(np.percentile(terminal, (1 - conf) * 100))
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=terminal.tolist(), nbinsx=60,
                               marker_color="rgba(55,138,221,0.6)"))
    fig.add_vline(x=S0, line_dash="dash", line_color="#185FA5",
                  annotation_text=f"현재가 {S0:,.0f}")
    fig.add_vline(x=var_price, line_dash="dash", line_color="#A32D2D",
                  annotation_text=f"VaR {var_price:,.0f}")
    fig.update_layout(title=title, xaxis_title="만기 주가", yaxis_title="빈도",
                      height=300, showlegend=False, margin=dict(t=40))
    return fig


# ── 데이터 수집 ───────────────────────────────────────────────────────────
with st.spinner("시장 데이터 수집 중..."):
    RETURNS   = {}
    PRICES_LV = {}
    data_info = []
    for p in POSITIONS:
        tk = p["ticker"]
        ret, live_px, live = fetch_returns(tk, OBS_DAYS)
        RETURNS[tk]   = ret
        PRICES_LV[tk] = live_px
        data_info.append({"티커": tk, "유형": p.get("type", ""),
                           "관측(일)": len(ret), "현재가": f"{live_px:,.0f}",
                           "데이터": "실시간" if live else "샘플(GBM)"})

# ── 개별 VaR ──────────────────────────────────────────────────────────────
ind_vars = []
ind_rows = []
for i, (p, mv) in enumerate(zip(POSITIONS, mv_list)):
    tk  = p["ticker"]
    res = parametric_var_ewma(RETURNS[tk], mv, HOLDING, CONF, EWMA_LAM)
    ind_vars.append(res["var"])
    ind_rows.append({
        "티커": tk, "유형": p.get("type", ""),
        "평가금액(억)":       round(mv / 1e8, 3),
        "비중(%)":            round(weights[i] * 100, 1),
        "EWMA σ(일,%)":      round(res["sigma_d"] * 100, 4),
        f"σ({HOLDING}일,%)": round(res["sigma_d"] * np.sqrt(HOLDING) * 100, 3),
        f"VaR {int(CONF*100)}%(억)":  round(res["var"] / 1e8, 3),
        f"CVaR {int(CONF*100)}%(억)": round(res["cvar"] / 1e8, 3),
        "VaR%": round(res["var_pct"] * 100, 3),
    })

# ── 포트폴리오 VaR ────────────────────────────────────────────────────────
min_len = min(len(RETURNS[tk]) for tk in tickers)
ret_mat = np.column_stack([RETURNS[tk][-min_len:] for tk in tickers])
cov_mat = ewma_cov_matrix(ret_mat, EWMA_LAM)
pf_res  = portfolio_var_ewma(weights, cov_mat, total_mv, CONF, HOLDING)
ivars, _= incremental_var(weights, cov_mat, total_mv, CONF, HOLDING)

sum_ind = sum(ind_vars)
divers  = sum_ind - pf_res["pf_var"]

comp_rows = []
for i, tk in enumerate(tickers):
    contrib = float(pf_res["comp"][i]) / pf_res["pf_var"] * 100 if pf_res["pf_var"] else 0
    comp_rows.append({
        "티커": tk, "비중(%)": round(weights[i] * 100, 1),
        "개별VaR(억)": round(ind_vars[i] / 1e8, 3),
        "MVaR": round(float(pf_res["mvar"][i]), 4),
        "CVaR(억)": round(float(pf_res["comp"][i]) / 1e8, 3),
        "기여도(%)": round(contrib, 1),
        "IVaR(억)": round(ivars[i] / 1e8, 3),
        "Beta": round(float(pf_res["beta"][i]), 3),
    })

# ── 탭 레이아웃 ──────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📈 포트폴리오 개요", "📊 EWMA VaR 분석", "🎲 시뮬레이션", "📄 리포트"])

# ── Tab 1: 개요 ───────────────────────────────────────────────────────────
with tab1:
    st.subheader("데이터 수집 현황")
    st.dataframe(pd.DataFrame(data_info), use_container_width=True, hide_index=True)

    st.subheader(f"포트폴리오 VaR 요약 ({int(CONF*100)}%, {HOLDING}일)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("포트폴리오 VaR",  f"{pf_res['pf_var']/1e8:.3f}억원",
              f"{pf_res['pf_var']/total_mv*100:.3f}%")
    c2.metric("비분산 VaR",       f"{sum_ind/1e8:.3f}억원")
    c3.metric("분산 효과",         f"{divers/1e8:.3f}억원",
              delta=f"-{divers/sum_ind*100:.1f}%", delta_color="inverse")
    c4.metric("포트폴리오 σ(일)",  f"{pf_res['pf_sigma']*100:.4f}%")

    col_l, col_r = st.columns(2)
    with col_l:
        fig_pie = go.Figure(go.Pie(
            labels=tickers, values=mv_list, hole=0.4,
            marker_colors=COLORS[:len(tickers)],
        ))
        fig_pie.update_layout(title="포트폴리오 비중 (평가금액 기준)", height=360,
                              margin=dict(t=50))
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_r:
        show_n = min(252, min(len(RETURNS[tk]) for tk in tickers))
        dates  = [(datetime.today() - timedelta(days=show_n - i)).strftime("%m/%d")
                  for i in range(show_n)]
        fig_vol = go.Figure()
        for i, tk in enumerate(tickers):
            sig = ewma_vol(RETURNS[tk], EWMA_LAM)[-show_n:] * np.sqrt(252) * 100
            fig_vol.add_trace(go.Scatter(x=dates, y=sig.tolist(), mode="lines",
                                         name=tk, line=dict(color=COLORS[i % len(COLORS)], width=2)))
        fig_vol.update_layout(title="EWMA 연율 변동성 추이 (%)",
                              xaxis_title="날짜", yaxis_title="연율 변동성 (%)",
                              height=360, legend=dict(orientation="h", y=-0.3),
                              margin=dict(t=50, b=80))
        st.plotly_chart(fig_vol, use_container_width=True)

# ── Tab 2: EWMA VaR ───────────────────────────────────────────────────────
with tab2:
    st.subheader(f"개별 종목 Parametric VaR ({int(CONF*100)}%, {HOLDING}일)")
    st.dataframe(pd.DataFrame(ind_rows), use_container_width=True, hide_index=True)

    st.subheader("Component / Incremental / Marginal VaR")
    st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)

    col_l, col_r = st.columns(2)
    with col_l:
        fig_comp = go.Figure(go.Bar(
            x=tickers,
            y=[r["CVaR(억)"] for r in comp_rows],
            marker_color=COLORS[:len(tickers)],
            text=[f"{r['기여도(%)']}%" for r in comp_rows],
            textposition="outside",
        ))
        fig_comp.update_layout(title="종목별 VaR 기여도 (Component VaR)",
                               yaxis_title="억원", height=360, margin=dict(t=50))
        st.plotly_chart(fig_comp, use_container_width=True)

    with col_r:
        corr = np.corrcoef(ret_mat.T)
        fig_heat = go.Figure(go.Heatmap(
            z=corr, x=tickers, y=tickers,
            colorscale="RdBu", zmid=0, zmin=-1, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in corr],
            texttemplate="%{text}",
        ))
        fig_heat.update_layout(title="수익률 상관계수 행렬", height=360, margin=dict(t=50))
        st.plotly_chart(fig_heat, use_container_width=True)

# ── Tab 3: 시뮬레이션 ─────────────────────────────────────────────────────
with tab3:
    sim_ticker = st.selectbox("시뮬레이션 종목", tickers, index=0)
    sim_rets   = RETURNS[sim_ticker]
    S0         = PRICES_LV[sim_ticker]
    mu_ann     = float(np.mean(sim_rets)) * 252
    sig_ann    = float(np.std(sim_rets, ddof=1)) * np.sqrt(252)
    T_YR       = SIM_T_DAYS / 252

    st.info(f"**{sim_ticker}** | 현재가 {S0:,.0f} | 연율μ={mu_ann*100:.2f}% | 연율σ={sig_ann*100:.2f}%")

    with st.spinner(f"시뮬레이션 중 ({SIM_N:,}경로 × {SIM_T_DAYS}일)..."):
        p_mjd = run_mjd(S0, mu_ann, sig_ann, MJD_LAM, T_YR, DT, SIM_N)
        p_bs  = run_bootstrap(S0, tuple(sim_rets.tolist()), T_YR, DT, SIM_N, BS_BLOCK)
        p_t   = run_t_mc(S0, mu_ann, sig_ann, T_DF, T_YR, DT, SIM_N)

    m_mjd = calc_sim_metrics(p_mjd, S0, CONF)
    m_bs  = calc_sim_metrics(p_bs,  S0, CONF)
    m_t   = calc_sim_metrics(p_t,   S0, CONF)
    vkey  = f"VaR{int(CONF*100)}%(%)"
    ckey  = f"CVaR{int(CONF*100)}%(%)"

    st.subheader(f"기법별 결과 비교 ({SIM_T_DAYS}일, {int(CONF*100)}%)")
    df_sim = pd.DataFrame({"Merton JD": m_mjd, "Block Bootstrap": m_bs,
                            f"t-MC (ν={T_DF})": m_t}).T
    st.dataframe(df_sim, use_container_width=True)

    methods   = ["Merton JD", "Block Bootstrap", f"t-MC (ν={T_DF})"]
    var_vals  = [abs(m_mjd[vkey]), abs(m_bs[vkey]), abs(m_t[vkey])]
    cvar_vals = [abs(m_mjd[ckey]), abs(m_bs[ckey]), abs(m_t[ckey])]

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(x=methods, y=var_vals, name=f"VaR {int(CONF*100)}%",
                             marker_color="#A32D2D",
                             text=[f"{v:.2f}%" for v in var_vals], textposition="outside"))
    fig_bar.add_trace(go.Bar(x=methods, y=cvar_vals, name=f"CVaR {int(CONF*100)}%",
                             marker_color="#854F0B",
                             text=[f"{v:.2f}%" for v in cvar_vals], textposition="outside"))
    fig_bar.update_layout(barmode="group", yaxis_title="손실률 (%)",
                          title="기법별 VaR / CVaR 비교", height=360, margin=dict(t=50))
    st.plotly_chart(fig_bar, use_container_width=True)

    st.subheader("경로 팬 차트")
    c1, c2, c3 = st.columns(3)
    with c1: st.plotly_chart(plot_fan(p_mjd, S0, "Merton Jump Diffusion"), use_container_width=True)
    with c2: st.plotly_chart(plot_fan(p_bs,  S0, "Historical Bootstrap"),  use_container_width=True)
    with c3: st.plotly_chart(plot_fan(p_t,   S0, f"t-분포 MC (ν={T_DF})"), use_container_width=True)

    st.subheader("만기 주가 분포")
    c1, c2, c3 = st.columns(3)
    with c1: st.plotly_chart(plot_hist_dist(p_mjd, S0, "Merton JD",     CONF), use_container_width=True)
    with c2: st.plotly_chart(plot_hist_dist(p_bs,  S0, "Bootstrap",     CONF), use_container_width=True)
    with c3: st.plotly_chart(plot_hist_dist(p_t,   S0, f"t-MC ν={T_DF}", CONF), use_container_width=True)

# ── Tab 4: 리포트 ─────────────────────────────────────────────────────────
with tab4:
    now = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

    def fmt_num(v):
        a = abs(v)
        if a >= 1e8: return f"{v/1e8:.2f}억"
        if a >= 1e4: return f"{v/1e4:,.0f}만"
        return f"{v:,.0f}"

    lines = [
        "# 포트폴리오 리스크 분석 리포트", "",
        "| 항목 | 내용 |", "|------|------|",
        f"| 작성일시 | {now} |",
        f"| 신뢰수준 | {int(CONF*100)}% |",
        f"| 보유기간 | {HOLDING}일 |",
        f"| USD/KRW | {USDKRW:,} |",
        "", "## 1. 포트폴리오 구성", "",
        f"- **총 평가금액**: {fmt_num(total_mv)}원",
        f"- **종목 수**: {len(POSITIONS)}개", "",
        "| 티커 | 유형 | 수량 | 평가금액(억원) | 비중(%) |",
        "|------|------|-----:|-----:|-----:|",
    ] + [
        f"| {p['ticker']} | {p.get('type','')} | {p['qty']:,.0f} | {mv_list[i]/1e8:.3f} | {weights[i]*100:.1f}% |"
        for i, p in enumerate(POSITIONS)
    ] + [
        "", "## 2. EWMA VaR 분석", "",
        "| 지표 | 값 |", "|------|----|",
        f"| 포트폴리오 VaR | {fmt_num(pf_res['pf_var'])}원 ({pf_res['pf_var']/total_mv*100:.3f}%) |",
        f"| 비분산 VaR | {fmt_num(sum_ind)}원 |",
        f"| 분산 효과 | {fmt_num(divers)}원 ({divers/sum_ind*100:.1f}%) |",
        f"| 포트폴리오 σ(일) | {pf_res['pf_sigma']*100:.4f}% |",
        "", "### Component VaR", "",
        "| 티커 | 비중 | CVaR(억) | 기여도 | IVaR(억) | Beta |",
        "|------|-----:|-----:|-----:|-----:|-----:|",
    ] + [
        f"| {r['티커']} | {r['비중(%)']}% | {r['CVaR(억)']} | {r['기여도(%)']}% | {r['IVaR(억)']} | {r['Beta']} |"
        for r in comp_rows
    ] + [
        "", "---",
        f"*Portfolio VaR Calculator 자동 생성 리포트 | {now}*",
    ]

    report_md = "\n".join(lines)
    st.markdown(report_md)
    st.download_button(
        label="⬇️ 리포트 다운로드 (.md)",
        data=report_md,
        file_name=f"VaR_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
        use_container_width=True,
    )
