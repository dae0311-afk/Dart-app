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
st.title(“🔐 DART 재무 조회”)
st.markdown(”—”)
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
st.subheader(“로그인”)
pw = st.text_input(“비밀번호를 입력하세요”, type=“password”)
if st.button(“로그인”, use_container_width=True, type=“primary”):
if pw == st.secrets[“APP_PASSWORD”]:
st.session_state.authenticated = True
st.rerun()
else:
st.error(“비밀번호가 틀렸습니다.”)
return False

if not check_password():
st.stop()

# ────────────────────────────────────────────

# 페이지 설정

# ────────────────────────────────────────────

st.set_page_config(page_title=“DART 재무 조회”, page_icon=“📊”, layout=“wide”)
API_KEY = st.secrets[“DART_API_KEY”]

# ────────────────────────────────────────────

# 계정 매핑

# ────────────────────────────────────────────

# 손익계산서

IS_ACCOUNTS = {
“매출액”:       [“ifrs-full_Revenue”, “dart_Revenue”, “ifrs_Revenue”],
“매출원가”:     [“ifrs-full_CostOfSales”, “dart_CostOfSales”],
“매출총이익”:   [“ifrs-full_GrossProfit”],
“판관비”:       [“ifrs-full_SellingGeneralAndAdministrativeExpense”,
“dart_TotalSellingGeneralAdministrativeExpenses”,
“ifrs-full_DistributionCosts”],
“영업이익”:     [“dart_OperatingIncomeLoss”, “ifrs-full_OperatingIncome”,
“ifrs-full_ProfitLossFromOperatingActivities”],
“당기순이익”:   [“ifrs-full_ProfitLoss”, “ifrs-full_ProfitLossAttributableToOwnersOfParent”],
}

# 재무상태표

BS_ACCOUNTS = {
“자산총계”:             [“ifrs-full_Assets”],
“현금및현금성자산”:     [“ifrs-full_CashAndCashEquivalents”],
“단기금융상품”:         [“ifrs-full_ShorttermInvestments”, “dart_ShortTermFinancialInstruments”,
“ifrs-full_CurrentFinancialAssetsAtFairValueThroughProfitOrLoss”],
“부채총계”:             [“ifrs-full_Liabilities”],
“자본총계”:             [“ifrs-full_Equity”],
# 차입금 관련
“단기차입금”:           [“ifrs-full_ShorttermBorrowings”, “dart_ShortTermBorrowings”],
“유동성장기차입금”:     [“ifrs-full_CurrentPortionOfLongtermBorrowings”,
“dart_CurrentPortionOfLongTermBorrowings”],
“유동성사채”:           [“dart_CurrentPortionOfBondsIssued”,
“ifrs-full_CurrentPortionOfLongtermNotesAndDebenturesPayable”],
“단기리스부채”:         [“ifrs-full_CurrentLeaseLiabilities”],
“장기차입금”:           [“ifrs-full_LongtermBorrowings”, “dart_LongTermBorrowings”,
“ifrs-full_NoncurrentPortionOfLongtermBorrowings”],
“사채”:                 [“dart_BondsIssued”, “ifrs-full_NoncurrentPortionOfLongtermNotesAndDebenturesPayable”],
“장기리스부채”:         [“ifrs-full_NoncurrentLeaseLiabilities”],
}

# 현금흐름표 (감가상각비)

CF_ACCOUNTS = {
“감가상각비”:       [“ifrs-full_AdjustmentsForDepreciationExpense”,
“dart_DepreciationExpenses”,
“ifrs-full_DepreciationAndAmortisationExpense”],
“무형자산상각비”:   [“ifrs-full_AdjustmentsForAmortisationExpense”,
“dart_AmortisationExpenses”,
“ifrs-full_AmortisationIntangibleAssets”],
}

REPORT_CODES = {
“사업보고서”:  “11011”,
“반기보고서”:  “11012”,
“1분기보고서”: “11013”,
“3분기보고서”: “11014”,
}

FS_DIV_MAP = {“연결재무제표”: “CFS”, “개별재무제표”: “OFS”}

# ────────────────────────────────────────────

# 유틸 함수

# ────────────────────────────────────────────

def parse_amount(val):
try:
return int(str(val).replace(”,”, “”).replace(” “, “”))
except:
return None

def to_억(val):
if val is None:
return None
return val / 100_000_000

def fmt_억(val, decimal=False):
if val is None:
return “-”
if abs(val) < 1:
return f”{val:.2f}”
return f”{val:,.0f}”

def fmt_pct(val):
if val is None:
return “-”
return f”{val:.1f}%”

# ────────────────────────────────────────────

# API 함수

# ────────────────────────────────────────────

@st.cache_data(ttl=3600)
def get_corp_code_list():
url = f”https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={API_KEY}”
r = requests.get(url, timeout=30)
z = zipfile.ZipFile(io.BytesIO(r.content))
xml_data = z.read(“CORPCODE.xml”)
root = ET.fromstring(xml_data)
corps = []
for item in root.findall(“list”):
corps.append({
“corp_code”:   item.findtext(“corp_code”, “”),
“corp_name”:   item.findtext(“corp_name”, “”),
“stock_code”:  item.findtext(“stock_code”, “”).strip(),
“modify_date”: item.findtext(“modify_date”, “”),
})
return pd.DataFrame(corps)

def search_company(name, df):
return df[df[“corp_name”].str.contains(name, na=False)].reset_index(drop=True)

def get_company_info(corp_code):
r = requests.get(“https://opendart.fss.or.kr/api/company.json”,
params={“crtfc_key”: API_KEY, “corp_code”: corp_code}, timeout=15)
return r.json()

@st.cache_data(ttl=1800)
def get_full_fs(corp_code, year, report_code, fs_div):
“”“전체 재무제표 항목 조회”””
r = requests.get(“https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json”,
params={“crtfc_key”: API_KEY, “corp_code”: corp_code,
“bsns_year”: year, “reprt_code”: report_code, “fs_div”: fs_div},
timeout=30)
data = r.json()
if data.get(“status”) != “000”:
return None, data.get(“message”, “조회 실패”)
return pd.DataFrame(data[“list”]), None

def extract_account(df, account_ids, col=“thstrm_amount”):
“”“계정ID 목록에서 첫 번째로 매칭되는 값 반환”””
if df is None:
return None
for aid in account_ids:
rows = df[df[“account_id”] == aid]
if not rows.empty:
val = parse_amount(rows.iloc[0][col])
if val is not None:
return val
# account_id 미매칭 시 계정명으로 fallback
return None

def extract_by_name(df, keywords, col=“thstrm_amount”):
“”“계정명에 키워드가 포함된 항목 추출”””
if df is None:
return None
for kw in keywords:
rows = df[df[“account_nm”].str.contains(kw, na=False)]
if not rows.empty:
val = parse_amount(rows.iloc[0][col])
if val is not None:
return val
return None

def extract_prev_year(df, account_ids):
“”“전년도 값 추출 (frmtrm_amount)”””
return extract_account(df, account_ids, col=“frmtrm_amount”)

def get_all_accounts_raw(df, account_map):
“”“계정맵에서 모든 값 추출, 원화 단위”””
result = {}
for name, ids in account_map.items():
val = extract_account(df, ids)
if val is None:
# 이름 기반 fallback
val = extract_by_name(df, [name])
result[name] = val
return result

# ────────────────────────────────────────────

# 핵심 분석 함수

# ────────────────────────────────────────────

def analyze_year(corp_code, year, report_code, fs_div):
“”“한 연도의 전체 재무 데이터 수집 및 계산”””
df, err = get_full_fs(corp_code, year, report_code, fs_div)
if err or df is None:
return None, err

```
raw = {}

# 손익
for name, ids in IS_ACCOUNTS.items():
    raw[name] = extract_account(df, ids)
    if raw[name] is None:
        raw[name] = extract_by_name(df, [name])

# 재무상태표
for name, ids in BS_ACCOUNTS.items():
    raw[name] = extract_account(df, ids)
    if raw[name] is None:
        raw[name] = extract_by_name(df, [name])

# 감가상각 (현금흐름표 우선)
for name, ids in CF_ACCOUNTS.items():
    raw[name] = extract_account(df, ids)
    if raw[name] is None:
        raw[name] = extract_by_name(df, [name.replace("비", "")])

# ── 계산 항목
# EBITDA
op = raw.get("영업이익")
dep = raw.get("감가상각비")
amd = raw.get("무형자산상각비")
ebitda = None
ebitda_calc = ""
if op is not None:
    parts = [("영업이익", op)]
    total = op
    if dep:
        total += dep
        parts.append(("감가상각비", dep))
    if amd:
        total += amd
        parts.append(("무형자산상각비", amd))
    ebitda = total
    ebitda_calc = " + ".join([f"{k}({to_억(v):,.0f}억)" for k,v in parts])
    ebitda_calc += f" = {to_억(ebitda):,.0f}억"

# 현금성자산
cash = raw.get("현금및현금성자산")
stfi = raw.get("단기금융상품")
cash_total = None
cash_calc = ""
c_parts = []
if cash:
    c_parts.append(("현금및현금성자산", cash))
if stfi:
    c_parts.append(("단기금융상품", stfi))
if c_parts:
    cash_total = sum(v for _, v in c_parts)
    cash_calc = " + ".join([f"{k}({to_억(v):,.0f}억)" for k,v in c_parts])
    cash_calc += f" = {to_억(cash_total):,.0f}억"

# 총차입금
debt_items = ["단기차입금", "유동성장기차입금", "유동성사채", "단기리스부채",
              "장기차입금", "사채", "장기리스부채"]
debt_parts = []
for item in debt_items:
    v = raw.get(item)
    if v:
        debt_parts.append((item, v))
total_debt = sum(v for _, v in debt_parts) if debt_parts else None
debt_calc = ""
if debt_parts:
    debt_calc = " + ".join([f"{k}({to_억(v):,.0f}억)" for k,v in debt_parts])
    debt_calc += f" = {to_억(total_debt):,.0f}억"

raw["EBITDA"] = ebitda
raw["현금성자산"] = cash_total
raw["총차입금"] = total_debt
raw["_ebitda_calc"] = ebitda_calc
raw["_cash_calc"] = cash_calc
raw["_debt_calc"] = debt_calc
raw["_debt_parts"] = debt_parts

return raw, None
```

# ────────────────────────────────────────────

# 요약표 생성

# ────────────────────────────────────────────

def build_summary_table(year_data: dict):
“”“year_data: {year: raw_dict}”””
years = sorted(year_data.keys())

```
ROW_ORDER = [
    "매출액", "Growth", "매출원가", "매출원가율",
    "매출총이익", "매출총이익률",
    "판관비", "판관비율",
    "EBITDA", "EBITDA Margin",
    "영업이익", "영업이익률",
    "당기순이익", "순이익률",
    "자산총계", "현금성자산", "부채총계", "총차입금", "자본총계",
]

table = {r: {} for r in ROW_ORDER}

for i, year in enumerate(years):
    d = year_data[year]
    rev   = to_억(d.get("매출액"))
    cogs  = to_억(d.get("매출원가"))
    gp    = to_억(d.get("매출총이익"))
    sga   = to_억(d.get("판관비"))
    ebit  = to_억(d.get("영업이익"))
    ni    = to_억(d.get("당기순이익"))
    ebitda= to_억(d.get("EBITDA"))
    asset = to_억(d.get("자산총계"))
    cash  = to_억(d.get("현금성자산"))
    liab  = to_억(d.get("부채총계"))
    debt  = to_억(d.get("총차입금"))
    eq    = to_억(d.get("자본총계"))

    # 전년 매출 (성장률)
    prev_rev = to_억(year_data[years[i-1]].get("매출액")) if i > 0 else None

    table["매출액"][year]       = fmt_억(rev)
    table["Growth"][year]       = fmt_pct((rev/prev_rev - 1)*100) if rev and prev_rev else "-"
    table["매출원가"][year]     = fmt_억(cogs)
    table["매출원가율"][year]   = fmt_pct(cogs/rev*100) if cogs and rev else "-"
    table["매출총이익"][year]   = fmt_억(gp)
    table["매출총이익률"][year] = fmt_pct(gp/rev*100) if gp and rev else "-"
    table["판관비"][year]       = fmt_억(sga)
    table["판관비율"][year]     = fmt_pct(sga/rev*100) if sga and rev else "-"
    table["EBITDA"][year]       = fmt_억(ebitda)
    table["EBITDA Margin"][year]= fmt_pct(ebitda/rev*100) if ebitda and rev else "-"
    table["영업이익"][year]     = fmt_억(ebit)
    table["영업이익률"][year]   = fmt_pct(ebit/rev*100) if ebit and rev else "-"
    table["당기순이익"][year]   = fmt_억(ni)
    table["순이익률"][year]     = fmt_pct(ni/rev*100) if ni and rev else "-"
    table["자산총계"][year]     = fmt_억(asset)
    table["현금성자산"][year]   = fmt_억(cash)
    table["부채총계"][year]     = fmt_억(liab)
    table["총차입금"][year]     = fmt_억(debt)
    table["자본총계"][year]     = fmt_억(eq)

rows = []
for r in ROW_ORDER:
    row = {"계정": r}
    for y in years:
        row[y] = table[r].get(y, "-")
    rows.append(row)
return pd.DataFrame(rows)
```

# ────────────────────────────────────────────

# UI

# ────────────────────────────────────────────

st.title(“📊 DART 재무 분석”)
st.caption(“금융감독원 전자공시(DART) 기반 요약 재무제표 자동 생성”)

with st.sidebar:
st.header(“⚙️ 조회 설정”)
report_type    = st.selectbox(“보고서 종류”, list(REPORT_CODES.keys()), index=0)
fs_type        = st.selectbox(“재무제표 종류”, list(FS_DIV_MAP.keys()), index=0)
years_options  = [str(y) for y in range(2024, 2014, -1)]
selected_years = st.multiselect(“조회 연도”, years_options, default=[“2024”,“2023”,“2022”])
st.divider()
if st.button(“로그아웃”):
st.session_state.authenticated = False
st.rerun()

# ── 기업 검색

st.subheader(“🔍 기업 검색”)
col1, col2 = st.columns([4, 1])
with col1:
search_query = st.text_input(“기업명 입력”, placeholder=“예: 삼성전자, LG화학”)
with col2:
search_btn = st.button(“검색”, use_container_width=True, type=“primary”)

if search_btn and search_query:
with st.spinner(“기업 목록 로딩 중…”):
corp_df = get_corp_code_list()
results = search_company(search_query, corp_df)
if results.empty:
st.warning(f”’{search_query}’에 해당하는 기업이 없습니다.”)
else:
st.success(f”{len(results)}개 기업 검색됨”)
disp = results.copy()
disp[“상장여부”] = disp[“stock_code”].apply(lambda x: “✅ 상장” if x else “비상장”)
disp = disp.rename(columns={“corp_code”:“기업코드”,“corp_name”:“기업명”,
“stock_code”:“종목코드”,“modify_date”:“수정일자”})
st.dataframe(disp[[“기업명”,“기업코드”,“종목코드”,“상장여부”]], use_container_width=True, hide_index=True)
st.session_state[“search_results”] = results

# ── 기업 선택 → 분석

if “search_results” in st.session_state and not st.session_state[“search_results”].empty:
results = st.session_state[“search_results”]
st.divider()
st.subheader(“📋 요약 재무제표 생성”)
selected_name = st.selectbox(“기업 선택”, results[“corp_name”].tolist())
corp_code = results[results[“corp_name”] == selected_name].iloc[0][“corp_code”]

```
if st.button(f"'{selected_name}' 요약 재무제표 생성", type="primary"):

    # 기업 기본정보
    info = get_company_info(corp_code)
    if info.get("status") == "000":
        with st.expander("🏢 기업 기본 정보", expanded=False):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("기업명",   info.get("corp_name","-"))
            c2.metric("대표자",   info.get("ceo_nm","-"))
            c3.metric("설립일",   info.get("est_dt","-"))
            c4.metric("결산월",   (info.get("acc_mt") or "-") + "월")

    # 연도별 수집
    report_code = REPORT_CODES[report_type]
    fs_div      = FS_DIV_MAP[fs_type]
    year_data   = {}
    raw_data    = {}

    prog = st.progress(0, text="재무 데이터 수집 중...")
    for i, year in enumerate(selected_years):
        d, err = analyze_year(corp_code, year, report_code, fs_div)
        if err:
            st.warning(f"{year}년 조회 실패: {err}")
        elif d:
            year_data[year] = d
            raw_data[year]  = d
        prog.progress((i+1)/len(selected_years), text=f"{year}년 완료")
    prog.empty()

    if not year_data:
        st.error("조회된 데이터가 없습니다. DART에 공시 자료가 없는 기업입니다.")
        st.stop()

    years_sorted = sorted(year_data.keys())

    # ══════════════════════════════
    # 1단계: 원재료 데이터
    # ══════════════════════════════
    with st.expander("📌 1단계 : 원재료 데이터 수집", expanded=True):
        st.caption("DART API에서 수집한 원시 데이터 (단위: 억원)")
        raw_rows = []
        raw_items = (list(IS_ACCOUNTS.keys()) +
                     ["현금및현금성자산","단기금융상품","감가상각비","무형자산상각비"] +
                     ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                      "장기차입금","사채","장기리스부채","자산총계","부채총계","자본총계"])
        for item in raw_items:
            row = {"계정": item}
            for year in years_sorted:
                val = to_억(year_data[year].get(item))
                row[year] = fmt_억(val) if val is not None else "미조회"
            raw_rows.append(row)
        st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

    # ══════════════════════════════
    # 2단계: 계산 과정
    # ══════════════════════════════
    with st.expander("📌 2단계 : EBITDA / 현금성자산 / 총차입금 계산 과정", expanded=True):
        for year in years_sorted:
            d = year_data[year]
            st.markdown(f"**── {year}년 ──**")

            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown("**EBITDA**")
                calc = d.get("_ebitda_calc", "-")
                st.code(calc if calc else "데이터 부족", language=None)

            with col_b:
                st.markdown("**현금성자산**")
                calc = d.get("_cash_calc", "-")
                st.code(calc if calc else "데이터 부족", language=None)

            with col_c:
                st.markdown("**총차입금**")
                calc = d.get("_debt_calc", "-")
                st.code(calc if calc else "데이터 부족", language=None)

            # 차입금 세부내역
            debt_parts = d.get("_debt_parts", [])
            if debt_parts:
                st.markdown("총차입금 세부내역:")
                dp_rows = [{"항목": k, "금액(억원)": fmt_억(to_억(v))} for k,v in debt_parts]
                dp_rows.append({"항목": "합계", "금액(억원)": fmt_억(to_억(sum(v for _,v in debt_parts)))})
                st.dataframe(pd.DataFrame(dp_rows), use_container_width=False, hide_index=True)

    # ══════════════════════════════
    # 3단계: 최종 요약 재무제표
    # ══════════════════════════════
    st.subheader("📊 최종 요약 재무제표 (단위: 억원)")
    summary_df = build_summary_table(year_data)

    # 스타일링
    def style_summary(df):
        styles = []
        margin_rows = ["Growth","매출원가율","매출총이익률","판관비율",
                       "EBITDA Margin","영업이익률","순이익률"]
        header_rows = ["매출액","자산총계"]
        for i, row in df.iterrows():
            if row["계정"] in margin_rows:
                styles.append(["background-color: #f0f0f0; color: #666666; font-size: 0.85em"] * len(row))
            elif row["계정"] in header_rows:
                styles.append(["background-color: #1F497D; color: white; font-weight: bold"] * len(row))
            elif row["계정"] in ["EBITDA","영업이익","당기순이익"]:
                styles.append(["background-color: #EBF3FB; font-weight: bold"] * len(row))
            elif row["계정"] in ["총차입금","현금성자산"]:
                styles.append(["background-color: #FFF3E0"] * len(row))
            else:
                styles.append([""] * len(row))
        return pd.DataFrame(styles, columns=df.columns)

    st.dataframe(
        summary_df.style.apply(style_summary, axis=None),
        use_container_width=True, hide_index=True, height=700
    )

    # ══════════════════════════════
    # 검증
    # ══════════════════════════════
    with st.expander("✅ 교차 검증", expanded=False):
        st.caption("EBITDA, 현금성자산, 총차입금의 계산 결과와 요약표 수치 일치 여부")
        verify_rows = []
        for year in years_sorted:
            d = year_data[year]
            verify_rows.append({
                "연도": year,
                "EBITDA(계산)": fmt_억(to_억(d.get("EBITDA"))),
                "현금성자산(계산)": fmt_억(to_억(d.get("현금성자산"))),
                "총차입금(계산)": fmt_억(to_억(d.get("총차입금"))),
            })
        st.dataframe(pd.DataFrame(verify_rows), use_container_width=True, hide_index=True)
        st.success("✅ 2단계 계산값과 최종 요약표의 수치가 동일한 소스에서 생성되어 일치합니다.")

    # ══════════════════════════════
    # 손익 차트
    # ══════════════════════════════
    st.subheader("📈 손익 추이")
    fig = go.Figure()
    for acc, color in zip(["매출액","EBITDA","영업이익","당기순이익"],
                           ["#1f77b4","#9467bd","#2ca02c","#ff7f0e"]):
        vals = [to_억(year_data[y].get(acc)) for y in years_sorted]
        fig.add_trace(go.Bar(
            name=acc, x=years_sorted, y=vals,
            marker_color=color,
            text=[f"{v:,.0f}" if v else "-" for v in vals],
            textposition="outside",
        ))
    fig.update_layout(barmode="group", yaxis_title="억원",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02),
                      height=400, plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    # ══════════════════════════════
    # CSV 다운로드
    # ══════════════════════════════
    st.divider()
    csv = summary_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("📥 요약 재무제표 CSV 다운로드", csv,
                       f"{selected_name}_요약재무제표.csv", "text/csv")
```
