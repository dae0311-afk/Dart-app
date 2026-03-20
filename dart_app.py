import streamlit as st
import requests
import pandas as pd
import zipfile
import io
import xml.etree.ElementTree as ET
import plotly.graph_objects as go

# ────────────────────────────────────────────
# 비밀번호 보호
# ────────────────────────────────────────────
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.title("🔐 DART 재무 조회")
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("로그인")
        pw = st.text_input("비밀번호를 입력하세요", type="password")
        if st.button("로그인", use_container_width=True, type="primary"):
            if pw == st.secrets["APP_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")
    return False

if not check_password():
    st.stop()

# ────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────
st.set_page_config(
    page_title="DART 재무 조회",
    page_icon="📊",
    layout="wide",
)

API_KEY = st.secrets["DART_API_KEY"]

ACCOUNT_MAP = {
    "ifrs-full_Revenue":                "매출액",
    "dart_Revenue":                     "매출액",
    "ifrs-full_GrossProfit":            "매출총이익",
    "ifrs-full_OperatingIncome":        "영업이익",
    "dart_OperatingIncomeLoss":         "영업이익",
    "ifrs-full_ProfitLoss":             "당기순이익",
    "ifrs-full_Assets":                 "자산총계",
    "ifrs-full_Liabilities":            "부채총계",
    "ifrs-full_Equity":                 "자본총계",
    "ifrs-full_CurrentAssets":          "유동자산",
    "ifrs-full_NoncurrentAssets":       "비유동자산",
    "ifrs-full_CurrentLiabilities":     "유동부채",
    "ifrs-full_NoncurrentLiabilities":  "비유동부채",
}

REPORT_CODES = {
    "사업보고서":  "11011",
    "반기보고서":  "11012",
    "1분기보고서": "11013",
    "3분기보고서": "11014",
}

FS_DIV_MAP = {
    "연결재무제표": "CFS",
    "개별재무제표": "OFS",
}

# ────────────────────────────────────────────
# API 함수
# ────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_corp_code_list():
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}"
    r = requests.get(url, timeout=30)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml_data = z.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    corps = []
    for item in root.findall("list"):
        corps.append({
            "corp_code":   item.findtext("corp_code", ""),
            "corp_name":   item.findtext("corp_name", ""),
            "stock_code":  item.findtext("stock_code", "").strip(),
            "modify_date": item.findtext("modify_date", ""),
        })
    return pd.DataFrame(corps)


def search_company(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)


def get_financial_statements(corp_code, year, report_code, fs_div):
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key":  API_KEY,
        "corp_code":  corp_code,
        "bsns_year":  year,
        "reprt_code": report_code,
        "fs_div":     fs_div,
    }
    r = requests.get(url, params=params, timeout=30)
    data = r.json()
    if data.get("status") != "000":
        return None, data.get("message", "조회 실패")
    return pd.DataFrame(data["list"]), None


def get_company_info(corp_code):
    url = "https://opendart.fss.or.kr/api/company.json"
    r = requests.get(url, params={"crtfc_key": API_KEY, "corp_code": corp_code}, timeout=15)
    return r.json()


def parse_amount(val):
    try:
        return int(str(val).replace(",", "").replace(" ", ""))
    except:
        return None


def fmt_krw(val):
    if val is None:
        return "-"
    sign = "-" if val < 0 else ""
    v = abs(val)
    if v >= 1_000_000_000_000:
        return f"{sign}{v/1_000_000_000_000:.2f}조"
    elif v >= 100_000_000:
        return f"{sign}{v/100_000_000:.1f}억"
    elif v >= 10_000:
        return f"{sign}{v/10_000:.1f}만"
    return f"{sign}{v:,}"


def extract_key_accounts(df):
    result = {}
    for _, row in df.iterrows():
        account_id = row.get("account_id", "")
        account_nm = row.get("account_nm", "")
        label = ACCOUNT_MAP.get(account_id)
        if label is None:
            for v in ACCOUNT_MAP.values():
                if v in account_nm:
                    label = v
                    break
        if label:
            amt = parse_amount(row.get("thstrm_amount", ""))
            if amt is not None and label not in result:
                result[label] = amt
    return result

# ────────────────────────────────────────────
# UI
# ────────────────────────────────────────────
st.title("📊 DART 기업 재무 조회")
st.caption("금융감독원 전자공시(DART) 기반 재무 정보 조회 도구")

with st.sidebar:
    st.header("⚙️ 조회 설정")
    report_type   = st.selectbox("보고서 종류", list(REPORT_CODES.keys()), index=0)
    fs_type       = st.selectbox("재무제표 종류", list(FS_DIV_MAP.keys()), index=0)
    years_options = [str(y) for y in range(2024, 2014, -1)]
    selected_years = st.multiselect("조회 연도 (복수 선택)", years_options, default=["2024", "2023", "2022"])
    st.divider()
    if st.button("로그아웃"):
        st.session_state.authenticated = False
        st.rerun()

# ── 기업 검색
st.subheader("🔍 기업 검색")
col1, col2 = st.columns([4, 1])
with col1:
    search_query = st.text_input("기업명 입력", placeholder="예: 삼성전자, LG화학, 티에스씨앤씨")
with col2:
    search_btn = st.button("검색", use_container_width=True, type="primary")

if search_btn and search_query:
    with st.spinner("기업 목록 로딩 중... (첫 검색은 10초 정도 걸릴 수 있습니다)"):
        corp_df = get_corp_code_list()
    results = search_company(search_query, corp_df)

    if results.empty:
        st.warning(f"'{search_query}'에 해당하는 기업을 찾을 수 없습니다. (DART 미등록 기업일 수 있습니다)")
    else:
        st.success(f"{len(results)}개 기업 검색됨")
        disp = results.copy()
        disp["상장여부"] = disp["stock_code"].apply(lambda x: "✅ 상장" if x else "비상장")
        disp = disp.rename(columns={"corp_code":"기업코드","corp_name":"기업명","stock_code":"종목코드","modify_date":"수정일자"})
        st.dataframe(disp[["기업명","기업코드","종목코드","상장여부","수정일자"]], use_container_width=True, hide_index=True)
        st.session_state["search_results"] = results

# ── 기업 선택 → 재무 조회
if "search_results" in st.session_state and not st.session_state["search_results"].empty:
    results = st.session_state["search_results"]
    st.divider()
    st.subheader("📋 재무 정보 조회")
    selected_name = st.selectbox("기업 선택", results["corp_name"].tolist())
    corp_code = results[results["corp_name"] == selected_name].iloc[0]["corp_code"]

    if st.button(f"'{selected_name}' 재무 조회", type="primary"):
        # 기업 기본정보
        with st.spinner("기업 정보 불러오는 중..."):
            info = get_company_info(corp_code)

        if info.get("status") == "000":
            with st.expander("🏢 기업 기본 정보", expanded=True):
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("기업명",   info.get("corp_name","-"))
                c2.metric("대표자",   info.get("ceo_nm","-"))
                c3.metric("설립일",   info.get("est_dt","-"))
                c4.metric("결산월",   (info.get("acc_mt") or "-") + "월")
                c5,c6,c7,c8 = st.columns(4)
                c5.metric("법인구분", info.get("corp_cls","-"))
                c6.metric("업종코드", info.get("induty_code","-"))
                c7.metric("홈페이지", info.get("hm_url","-"))
                c8.metric("종목코드", info.get("stock_code","비상장") or "비상장")

        # 연도별 재무 조회
        all_data = {}
        report_code = REPORT_CODES[report_type]
        fs_div      = FS_DIV_MAP[fs_type]
        prog = st.progress(0, text="재무 데이터 로딩 중...")
        for i, year in enumerate(selected_years):
            df_fs, err = get_financial_statements(corp_code, year, report_code, fs_div)
            if err:
                st.warning(f"{year}년: {err}")
            else:
                accounts = extract_key_accounts(df_fs)
                if accounts:
                    all_data[year] = accounts
            prog.progress((i+1)/len(selected_years), text=f"{year}년 완료")
        prog.empty()

        if not all_data:
            st.error("조회된 재무 데이터가 없습니다. DART에 공시 자료가 없는 기업입니다.")
        else:
            st.success(f"{len(all_data)}개 연도 데이터 조회 완료!")

            # 요약 테이블
            st.subheader("📈 핵심 재무 요약")
            summary_accounts = ["매출액","매출총이익","영업이익","당기순이익","자산총계","부채총계","자본총계"]
            rows = []
            for acc in summary_accounts:
                row_data = {"항목": acc}
                for year in sorted(all_data.keys(), reverse=True):
                    row_data[f"{year}년"] = fmt_krw(all_data[year].get(acc))
                rows.append(row_data)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            # 손익 차트
            st.subheader("📊 손익 추이")
            years_sorted = sorted(all_data.keys())
            fig = go.Figure()
            for acc, color in zip(["매출액","영업이익","당기순이익"], ["#1f77b4","#2ca02c","#ff7f0e"]):
                vals = [all_data[y].get(acc, None) for y in years_sorted]
                vals억 = [v/100_000_000 if v is not None else None for v in vals]
                fig.add_trace(go.Bar(
                    name=acc, x=years_sorted, y=vals억,
                    marker_color=color,
                    text=[f"{v:.1f}억" if v is not None else "-" for v in vals억],
                    textposition="outside",
                ))
            fig.update_layout(barmode="group", yaxis_title="금액 (억원)",
                              legend=dict(orientation="h", yanchor="bottom", y=1.02),
                              height=400, plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)

            # 부채비율 / 영업이익률
            st.subheader("🏦 재무 건전성")
            col_a, col_b = st.columns(2)
            with col_a:
                fig2 = go.Figure()
                pts = []
                for y in years_sorted:
                    l = all_data[y].get("부채총계"); e = all_data[y].get("자본총계")
                    pts.append((y, round(l/e*100,1) if l and e and e!=0 else None))
                fig2.add_trace(go.Scatter(
                    x=[p[0] for p in pts], y=[p[1] for p in pts],
                    mode="lines+markers+text",
                    text=[f"{p[1]}%" if p[1] else "-" for p in pts],
                    textposition="top center",
                    line=dict(color="#e74c3c", width=3), marker=dict(size=10),
                ))
                fig2.update_layout(title="부채비율 (%)", yaxis_title="%", height=300, plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig2, use_container_width=True)

            with col_b:
                fig3 = go.Figure()
                pts2 = []
                for y in years_sorted:
                    r = all_data[y].get("매출액"); o = all_data[y].get("영업이익")
                    pts2.append((y, round(o/r*100,1) if r and o and r!=0 else None))
                fig3.add_trace(go.Scatter(
                    x=[p[0] for p in pts2], y=[p[1] for p in pts2],
                    mode="lines+markers+text",
                    text=[f"{p[1]}%" if p[1] else "-" for p in pts2],
                    textposition="top center",
                    line=dict(color="#2ecc71", width=3), marker=dict(size=10),
                    fill="tozeroy", fillcolor="rgba(46,204,113,0.1)",
                ))
                fig3.update_layout(title="영업이익률 (%)", yaxis_title="%", height=300, plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig3, use_container_width=True)

            # 자산 구성 파이차트
            latest_year = sorted(all_data.keys())[-1]
            latest = all_data[latest_year]
            if all(k in latest for k in ["부채총계","자본총계"]):
                st.subheader(f"🥧 자산 구성 ({latest_year}년)")
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    if "유동자산" in latest and "비유동자산" in latest:
                        fig4 = go.Figure(go.Pie(
                            labels=["유동자산","비유동자산"],
                            values=[latest["유동자산"], latest["비유동자산"]],
                            hole=0.4, marker_colors=["#3498db","#9b59b6"],
                        ))
                        fig4.update_layout(title="자산 구성", height=300)
                        st.plotly_chart(fig4, use_container_width=True)
                with col_p2:
                    fig5 = go.Figure(go.Pie(
                        labels=["부채","자본"],
                        values=[latest["부채총계"], latest["자본총계"]],
                        hole=0.4, marker_colors=["#e74c3c","#2ecc71"],
                    ))
                    fig5.update_layout(title="부채 vs 자본", height=300)
                    st.plotly_chart(fig5, use_container_width=True)

            # CSV 다운로드
            st.divider()
            export_rows = []
            for year in sorted(all_data.keys()):
                for acc, val in all_data[year].items():
                    export_rows.append({"연도":year,"항목":acc,"금액(원)":val,"금액(억)":round(val/100_000_000,2) if val else None})
            csv = pd.DataFrame(export_rows).to_csv(index=False, encoding="utf-8-sig")
            st.download_button("📥 재무 데이터 CSV 다운로드", csv, f"{selected_name}_재무데이터.csv", "text/csv")
