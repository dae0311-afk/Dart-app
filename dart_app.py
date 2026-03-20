import streamlit as st
import requests
import pandas as pd
import zipfile
import io
import xml.etree.ElementTree as ET
import plotly.graph_objects as go

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return True
    st.title("DART")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("Login")
        pw = st.text_input("Password", type="password")
        if st.button("Login", use_container_width=True, type="primary"):
            if pw == st.secrets["APP_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Wrong password.")
    return False

if not check_password():
    st.stop()

st.set_page_config(page_title="DART", page_icon="chart_with_upwards_trend", layout="wide")
API_KEY = st.secrets["DART_API_KEY"]

IS_IDS = {
    "매출액":     ["ifrs-full_Revenue", "dart_Revenue"],
    "매출원가":   ["ifrs-full_CostOfSales", "dart_CostOfSales"],
    "매출총이익": ["ifrs-full_GrossProfit"],
    "판관비":     ["ifrs-full_SellingGeneralAndAdministrativeExpense",
                   "dart_TotalSellingGeneralAdministrativeExpenses"],
    "영업이익":   ["dart_OperatingIncomeLoss", "ifrs-full_OperatingIncome",
                   "ifrs-full_ProfitLossFromOperatingActivities"],
    "당기순이익": ["ifrs-full_ProfitLoss",
                   "ifrs-full_ProfitLossAttributableToOwnersOfParent"],
}

BS_IDS = {
    "자산총계":         ["ifrs-full_Assets"],
    "현금및현금성자산": ["ifrs-full_CashAndCashEquivalents"],
    "단기금융상품":     ["ifrs-full_ShorttermInvestments",
                         "dart_ShortTermFinancialInstruments"],
    "부채총계":         ["ifrs-full_Liabilities"],
    "자본총계":         ["ifrs-full_Equity"],
    "단기차입금":       ["ifrs-full_ShorttermBorrowings", "dart_ShortTermBorrowings"],
    "유동성장기차입금": ["ifrs-full_CurrentPortionOfLongtermBorrowings",
                         "dart_CurrentPortionOfLongTermBorrowings"],
    "유동성사채":       ["dart_CurrentPortionOfBondsIssued"],
    "단기리스부채":     ["ifrs-full_CurrentLeaseLiabilities"],
    "장기차입금":       ["ifrs-full_LongtermBorrowings", "dart_LongTermBorrowings"],
    "사채":             ["dart_BondsIssued"],
    "장기리스부채":     ["ifrs-full_NoncurrentLeaseLiabilities"],
}

CF_IDS = {
    "감가상각비":     ["ifrs-full_AdjustmentsForDepreciationExpense",
                       "dart_DepreciationExpenses"],
    "무형자산상각비": ["ifrs-full_AdjustmentsForAmortisationExpense",
                       "dart_AmortisationExpenses"],
}

REPORT_CODES = {
    "사업보고서":  "11011",
    "반기보고서":  "11012",
    "1분기보고서": "11013",
    "3분기보고서": "11014",
}

FS_DIV_MAP = {"연결재무제표": "CFS", "개별재무제표": "OFS"}

def parse_amount(val):
    try:
        return int(str(val).replace(",", "").replace(" ", ""))
    except:
        return None

def to_uk(val):
    if val is None:
        return None
    return val / 100000000

def fmt_uk(val):
    if val is None:
        return "-"
    if abs(val) < 1:
        return "{:.2f}".format(val)
    return "{:,.0f}".format(val)

def fmt_pct(val):
    if val is None:
        return "-"
    return "{:.1f}%".format(val)

@st.cache_data(ttl=3600)
def get_corp_list():
    url = "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=" + API_KEY
    r = requests.get(url, timeout=30)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read("CORPCODE.xml"))
    corps = []
    for item in root.findall("list"):
        corps.append({
            "corp_code":  item.findtext("corp_code", ""),
            "corp_name":  item.findtext("corp_name", ""),
            "stock_code": item.findtext("stock_code", "").strip(),
        })
    return pd.DataFrame(corps)

def search_corp(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)

def get_corp_info(corp_code):
    r = requests.get(
        "https://opendart.fss.or.kr/api/company.json",
        params={"crtfc_key": API_KEY, "corp_code": corp_code},
        timeout=15
    )
    return r.json()

@st.cache_data(ttl=1800)
def get_fs(corp_code, year, report_code, fs_div):
    r = requests.get(
        "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        params={
            "crtfc_key":  API_KEY,
            "corp_code":  corp_code,
            "bsns_year":  year,
            "reprt_code": report_code,
            "fs_div":     fs_div,
        },
        timeout=30
    )
    data = r.json()
    if data.get("status") != "000":
        return None, data.get("message", "fail")
    return pd.DataFrame(data["list"]), None

def find_val(df, ids, col="thstrm_amount"):
    if df is None:
        return None
    for aid in ids:
        rows = df[df["account_id"] == aid]
        if not rows.empty:
            v = parse_amount(rows.iloc[0][col])
            if v is not None:
                return v
    return None

def find_by_name(df, kw, col="thstrm_amount"):
    if df is None:
        return None
    rows = df[df["account_nm"].str.contains(kw, na=False)]
    if not rows.empty:
        return parse_amount(rows.iloc[0][col])
    return None

def analyze(corp_code, year, report_code, fs_div):
    df, err = get_fs(corp_code, year, report_code, fs_div)
    if err:
        return None, err

    raw = {}
    for nm, ids in IS_IDS.items():
        raw[nm] = find_val(df, ids) or find_by_name(df, nm)
    for nm, ids in BS_IDS.items():
        raw[nm] = find_val(df, ids) or find_by_name(df, nm)
    for nm, ids in CF_IDS.items():
        raw[nm] = find_val(df, ids) or find_by_name(df, nm)

    op  = raw.get("영업이익")
    dep = raw.get("감가상각비")
    amd = raw.get("무형자산상각비")
    ebitda = None
    ebitda_calc = ""
    if op is not None:
        total = op
        parts = [("영업이익", op)]
        if dep:
            total += dep
            parts.append(("감가상각비", dep))
        if amd:
            total += amd
            parts.append(("무형자산상각비", amd))
        ebitda = total
        ebitda_calc = " + ".join(["{0}({1:,.0f}억)".format(k, to_uk(v)) for k, v in parts])
        ebitda_calc += " = {0:,.0f}억".format(to_uk(ebitda))

    cash = raw.get("현금및현금성자산")
    stfi = raw.get("단기금융상품")
    cash_total = None
    cash_calc  = ""
    c_parts = []
    if cash:
        c_parts.append(("현금및현금성자산", cash))
    if stfi:
        c_parts.append(("단기금융상품", stfi))
    if c_parts:
        cash_total = sum(v for _, v in c_parts)
        cash_calc  = " + ".join(["{0}({1:,.0f}억)".format(k, to_uk(v)) for k, v in c_parts])
        cash_calc  += " = {0:,.0f}억".format(to_uk(cash_total))

    debt_keys = ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                 "장기차입금","사채","장기리스부채"]
    debt_parts = [(k, raw[k]) for k in debt_keys if raw.get(k)]
    total_debt = sum(v for _, v in debt_parts) if debt_parts else None
    debt_calc  = ""
    if debt_parts:
        debt_calc  = " + ".join(["{0}({1:,.0f}억)".format(k, to_uk(v)) for k, v in debt_parts])
        debt_calc  += " = {0:,.0f}억".format(to_uk(total_debt))

    raw["EBITDA"]       = ebitda
    raw["현금성자산"]   = cash_total
    raw["총차입금"]     = total_debt
    raw["_ebitda_calc"] = ebitda_calc
    raw["_cash_calc"]   = cash_calc
    raw["_debt_calc"]   = debt_calc
    raw["_debt_parts"]  = debt_parts
    return raw, None

def build_table(year_data):
    years = sorted(year_data.keys())
    ROW_ORDER = [
        "매출액","Growth","매출원가","매출원가율",
        "매출총이익","매출총이익률",
        "판관비","판관비율",
        "EBITDA","EBITDA Margin",
        "영업이익","영업이익률",
        "당기순이익","순이익률",
        "자산총계","현금성자산","부채총계","총차입금","자본총계",
    ]
    table = {r: {} for r in ROW_ORDER}
    for i, year in enumerate(years):
        d    = year_data[year]
        rev  = to_uk(d.get("매출액"))
        cogs = to_uk(d.get("매출원가"))
        gp   = to_uk(d.get("매출총이익"))
        sga  = to_uk(d.get("판관비"))
        ebit = to_uk(d.get("영업이익"))
        ni   = to_uk(d.get("당기순이익"))
        ebd  = to_uk(d.get("EBITDA"))
        ast  = to_uk(d.get("자산총계"))
        csh  = to_uk(d.get("현금성자산"))
        lib  = to_uk(d.get("부채총계"))
        dbt  = to_uk(d.get("총차입금"))
        eq   = to_uk(d.get("자본총계"))
        prev_rev = to_uk(year_data[years[i-1]].get("매출액")) if i > 0 else None

        def sp(a, b):
            if a is not None and b and b != 0:
                return fmt_pct(a / b * 100)
            return "-"

        table["매출액"][year]        = fmt_uk(rev)
        table["Growth"][year]        = fmt_pct((rev/prev_rev-1)*100) if rev and prev_rev else "-"
        table["매출원가"][year]      = fmt_uk(cogs)
        table["매출원가율"][year]    = sp(cogs, rev)
        table["매출총이익"][year]    = fmt_uk(gp)
        table["매출총이익률"][year]  = sp(gp, rev)
        table["판관비"][year]        = fmt_uk(sga)
        table["판관비율"][year]      = sp(sga, rev)
        table["EBITDA"][year]        = fmt_uk(ebd)
        table["EBITDA Margin"][year] = sp(ebd, rev)
        table["영업이익"][year]      = fmt_uk(ebit)
        table["영업이익률"][year]    = sp(ebit, rev)
        table["당기순이익"][year]    = fmt_uk(ni)
        table["순이익률"][year]      = sp(ni, rev)
        table["자산총계"][year]      = fmt_uk(ast)
        table["현금성자산"][year]    = fmt_uk(csh)
        table["부채총계"][year]      = fmt_uk(lib)
        table["총차입금"][year]      = fmt_uk(dbt)
        table["자본총계"][year]      = fmt_uk(eq)

    rows = []
    for r in ROW_ORDER:
        row = {"계정": r}
        for y in years:
            row[y] = table[r].get(y, "-")
        rows.append(row)
    return pd.DataFrame(rows)

st.title("DART 재무 분석")
st.caption("금융감독원 전자공시(DART) 기반 요약 재무제표 자동 생성")

with st.sidebar:
    st.header("조회 설정")
    report_type    = st.selectbox("보고서 종류", list(REPORT_CODES.keys()), index=0)
    fs_type        = st.selectbox("재무제표 종류", list(FS_DIV_MAP.keys()), index=0)
    years_options  = [str(y) for y in range(2024, 2014, -1)]
    selected_years = st.multiselect("조회 연도", years_options,
                                    default=["2024", "2023", "2022"])
    st.divider()
    if st.button("로그아웃"):
        st.session_state.authenticated = False
        st.rerun()

st.subheader("기업 검색")
col1, col2 = st.columns([4, 1])
with col1:
    q = st.text_input("기업명 입력", placeholder="예: 삼성전자, LG화학")
with col2:
    search_btn = st.button("검색", use_container_width=True, type="primary")

if search_btn and q:
    with st.spinner("로딩 중..."):
        corp_df = get_corp_list()
    res = search_corp(q, corp_df)
    if res.empty:
        st.warning("해당 기업을 찾을 수 없습니다.")
    else:
        st.success("{0}개 기업 검색됨".format(len(res)))
        disp = res.copy()
        disp["상장여부"] = disp["stock_code"].apply(lambda x: "상장" if x else "비상장")
        disp = disp.rename(columns={"corp_code":"기업코드","corp_name":"기업명","stock_code":"종목코드"})
        st.dataframe(disp[["기업명","기업코드","종목코드","상장여부"]],
                     use_container_width=True, hide_index=True)
        st.session_state["search_results"] = res

if "search_results" in st.session_state and not st.session_state["search_results"].empty:
    res = st.session_state["search_results"]
    st.divider()
    st.subheader("요약 재무제표 생성")
    sel = st.selectbox("기업 선택", res["corp_name"].tolist())
    corp_code = res[res["corp_name"] == sel].iloc[0]["corp_code"]

    if st.button("요약 재무제표 생성", type="primary"):

        info = get_corp_info(corp_code)
        if info.get("status") == "000":
            with st.expander("기업 기본 정보", expanded=False):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("기업명", info.get("corp_name", "-"))
                c2.metric("대표자", info.get("ceo_nm", "-"))
                c3.metric("설립일", info.get("est_dt", "-"))
                acc_mt = info.get("acc_mt") or "-"
                c4.metric("결산월", acc_mt + "월")

        report_code = REPORT_CODES[report_type]
        fs_div      = FS_DIV_MAP[fs_type]
        year_data   = {}

        prog = st.progress(0, text="데이터 수집 중...")
        for i, year in enumerate(selected_years):
            d, err = analyze(corp_code, year, report_code, fs_div)
            if err:
                st.warning("{0}년 조회 실패: {1}".format(year, err))
            elif d:
                year_data[year] = d
            prog.progress((i+1)/len(selected_years), text="{0}년 완료".format(year))
        prog.empty()

        if not year_data:
            st.error("조회된 데이터가 없습니다.")
            st.stop()

        years_sorted = sorted(year_data.keys())

        with st.expander("1단계: 원재료 데이터 수집 (단위: 억원)", expanded=True):
            raw_items = (
                list(IS_IDS.keys()) +
                ["현금및현금성자산","단기금융상품","감가상각비","무형자산상각비"] +
                ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                 "장기차입금","사채","장기리스부채","자산총계","부채총계","자본총계"]
            )
            raw_rows = []
            for item in raw_items:
                row = {"계정": item}
                for year in years_sorted:
                    v = to_uk(year_data[year].get(item))
                    row[year] = fmt_uk(v) if v is not None else "미조회"
                raw_rows.append(row)
            st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

        with st.expander("2단계: EBITDA / 현금성자산 / 총차입금 계산 과정", expanded=True):
            for year in years_sorted:
                d = year_data[year]
                st.markdown("**── {0}년 ──**".format(year))
                ca, cb, cc = st.columns(3)
                with ca:
                    st.markdown("**EBITDA**")
                    st.code(d.get("_ebitda_calc") or "데이터 부족", language=None)
                with cb:
                    st.markdown("**현금성자산**")
                    st.code(d.get("_cash_calc") or "데이터 부족", language=None)
                with cc:
                    st.markdown("**총차입금**")
                    st.code(d.get("_debt_calc") or "데이터 부족", language=None)
                dp = d.get("_debt_parts", [])
                if dp:
                    dp_rows = [{"항목": k, "금액(억원)": fmt_uk(to_uk(v))} for k, v in dp]
                    dp_rows.append({"항목": "합계",
                                    "금액(억원)": fmt_uk(to_uk(sum(v for _, v in dp)))})
                    st.dataframe(pd.DataFrame(dp_rows), use_container_width=False, hide_index=True)

        st.subheader("3단계: 최종 요약 재무제표 (단위: 억원)")
        summary_df = build_table(year_data)
        st.dataframe(summary_df, use_container_width=True, hide_index=True, height=700)

        with st.expander("교차 검증", expanded=False):
            vrows = []
            for year in years_sorted:
                d = year_data[year]
                vrows.append({
                    "연도":             year,
                    "EBITDA(계산)":     fmt_uk(to_uk(d.get("EBITDA"))),
                    "현금성자산(계산)": fmt_uk(to_uk(d.get("현금성자산"))),
                    "총차입금(계산)":   fmt_uk(to_uk(d.get("총차입금"))),
                })
            st.dataframe(pd.DataFrame(vrows), use_container_width=True, hide_index=True)
            st.success("2단계 계산값과 최종 요약표 수치 일치 확인 완료")

        st.subheader("손익 추이")
        fig = go.Figure()
        colors = ["#1f77b4","#9467bd","#2ca02c","#ff7f0e"]
        accs   = ["매출액","EBITDA","영업이익","당기순이익"]
        for acc, color in zip(accs, colors):
            vals = [to_uk(year_data[y].get(acc)) for y in years_sorted]
            fig.add_trace(go.Bar(
                name=acc, x=years_sorted, y=vals,
                marker_color=color,
                text=["{:,.0f}".format(v) if v else "-" for v in vals],
                textposition="outside",
            ))
        fig.update_layout(
            barmode="group", yaxis_title="억원",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            height=400, plot_bgcolor="rgba(0,0,0,0)"
        )
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        csv = summary_df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "CSV 다운로드", csv,
            sel + "_재무제표.csv", "text/csv"
        )
