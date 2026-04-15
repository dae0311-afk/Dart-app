"""
DART 재무 분석 앱
단위 설계:
  - XBRL (fnlttSinglAcntAll): API가 원(KRW) 단위로 반환 → 그대로 원 단위 저장
  - 문서 파싱: _detect_unit()로 문서 내 단위 감지 → 원 단위로 환산 저장
  - 저장 규칙: raw dict의 모든 금액 = 원(KRW) 단위
  - 표시 규칙: 원 ÷ disp_unit → 화면 표시값
"""
import re
import streamlit as st
import requests
import pandas as pd
import zipfile
import io
import xml.etree.ElementTree as ET
import plotly.graph_objects as go
import time
from datetime import datetime

st.set_page_config(page_title="DART 재무 분석", page_icon="📈", layout="wide")

st.markdown("""
<style>
.btn-active > button, .btn-active > button:hover {
    background:#e74c3c !important; color:#fff !important;
    font-weight:700 !important; border:1px solid #c0392b !important;
}
.row-btn > button {
    background:#f5f6fa !important; color:#212529 !important;
    border:none !important; border-bottom:1px solid #dfe6e9 !important;
    border-radius:0 !important; text-align:left !important;
    padding:5px 10px !important; font-size:0.83rem !important;
    height:32px !important; width:100% !important;
}
.row-btn > button:hover { background:#e74c3c !important; color:#fff !important; }
.row-sel > button, .row-sel > button:hover {
    background:#e74c3c !important; color:#fff !important; font-weight:700 !important;
    border:none !important; border-bottom:1px solid #c0392b !important;
    border-radius:0 !important; text-align:left !important;
    padding:5px 10px !important; font-size:0.83rem !important;
    height:32px !important; width:100% !important;
}
table.fin-table { width:100%; border-collapse:collapse; font-size:0.82rem; }
table.fin-table th { background:#2c3e50; color:#fff; padding:6px 10px;
    text-align:right; white-space:nowrap; }
table.fin-table th:first-child { text-align:left; min-width:160px; }
table.fin-table td { padding:5px 10px; border-bottom:1px solid #eee;
    text-align:right; white-space:nowrap; }
table.fin-table td:first-child { text-align:left; color:#333; }
table.fin-table tr.hl td { background:#f0f4ff; font-weight:700; }
table.fin-table tr.sub td { color:#888; font-style:italic; }
table.fin-table tr:hover td { background:#fffde7; }
</style>
""", unsafe_allow_html=True)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://opendart.fss.or.kr/",
}

def _get(url, params=None, timeout=60, retries=4):
    exc = None
    for n in range(1, retries+1):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=_HEADERS)
            r.raise_for_status(); return r
        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            exc = e
            if n < retries: time.sleep(n * 5)
        except requests.exceptions.HTTPError as e: raise e
    raise exc

# ── 업종명 ────────────────────────────────────────────────────────────────────
_IND = {"10":"식료품","20":"화학물질","21":"의약품","22":"고무·플라스틱","24":"1차금속",
        "25":"금속가공","26":"전자부품·컴퓨터","27":"의료·정밀기기","28":"전기장비",
        "29":"기계·장비","30":"자동차","35":"전기·가스","41":"종합건설","42":"전문공사",
        "46":"도매","47":"소매","49":"운송","55":"숙박","56":"음식점",
        "61":"통신","62":"SW·IT서비스","63":"정보서비스","64":"금융","65":"보험",
        "68":"부동산","70":"연구개발","71":"전문서비스","85":"교육","86":"보건"}
def _ind(code): c=str(code).strip(); return _IND.get(c[:2],c) if c and c!="-" else "-"
CORP_CLS = {"Y":"유가증권","K":"코스닥","N":"코넥스","E":"비상장"}

# ── 로그인 ────────────────────────────────────────────────────────────────────
def check_pw():
    if st.session_state.get("auth"): return True
    st.title("DART 재무 분석")
    _,col,_ = st.columns([1,2,1])
    with col:
        st.subheader("🔒 Login")
        with st.form("lf"):
            pw = st.text_input("Password", type="password", placeholder="Enter 후 로그인")
            if st.form_submit_button("Login", use_container_width=True, type="primary"):
                if pw == st.secrets["APP_PASSWORD"]:
                    st.session_state.auth = True; st.rerun()
                else: st.error("비밀번호 오류")
    return False

if not check_pw(): st.stop()

API_KEY      = st.secrets["DART_API_KEY"]
CURRENT_YEAR = datetime.now().year

# ── 계정 ID 매핑 ──────────────────────────────────────────────────────────────
IS_IDS = {
    "매출액":     ["ifrs-full_Revenue","dart_Revenue"],
    "매출원가":   ["ifrs-full_CostOfSales","dart_CostOfSales"],
    "매출총이익": ["ifrs-full_GrossProfit"],
    "판관비":     ["ifrs-full_SellingGeneralAndAdministrativeExpense",
                   "dart_TotalSellingGeneralAdministrativeExpenses"],
    "영업이익":   ["dart_OperatingIncomeLoss","ifrs-full_OperatingIncome",
                   "ifrs-full_ProfitLossFromOperatingActivities"],
    "당기순이익": ["ifrs-full_ProfitLoss",
                   "ifrs-full_ProfitLossAttributableToOwnersOfParent"],
}
BS_IDS = {
    "자산총계":         ["ifrs-full_Assets"],
    "현금및현금성자산": ["ifrs-full_CashAndCashEquivalents"],
    "단기금융상품":     ["ifrs-full_ShorttermInvestments","dart_ShortTermFinancialInstruments"],
    "부채총계":         ["ifrs-full_Liabilities"],
    "자본총계":         ["ifrs-full_Equity"],
    "단기차입금":       ["ifrs-full_ShorttermBorrowings","dart_ShortTermBorrowings"],
    "유동성장기차입금": ["ifrs-full_CurrentPortionOfLongtermBorrowings",
                         "dart_CurrentPortionOfLongTermBorrowings"],
    "유동성사채":       ["dart_CurrentPortionOfBondsIssued"],
    "단기리스부채":     ["ifrs-full_CurrentLeaseLiabilities"],
    "장기차입금":       ["ifrs-full_LongtermBorrowings","dart_LongTermBorrowings"],
    "사채":             ["dart_BondsIssued"],
    "장기리스부채":     ["ifrs-full_NoncurrentLeaseLiabilities"],
}
CF_IDS = {
    "감가상각비":     ["ifrs-full_AdjustmentsForDepreciationExpense","dart_DepreciationExpenses"],
    "무형자산상각비": ["ifrs-full_AdjustmentsForAmortisationExpense","dart_AmortisationExpenses"],
}

# ─────────────────────────────────────────────────────────────────────────────
# 단위 설계 핵심
# ─────────────────────────────────────────────────────────────────────────────
# DART XBRL API (fnlttSinglAcntAll): 금액을 '원(KRW)' 단위로 반환
# → parse_amount 로 정수 변환 → 그대로 원 단위 저장
#
# 문서 파싱 (XML/HTML/PDF): 문서 내 "단위:천원" 등 찾아 원으로 환산 후 저장
# → 저장: 파싱숫자 × detect_unit() = 원 단위
#
# 표시: 원값 ÷ DISP_UNIT[선택단위]
DISP_UNIT = {"천원":1_000, "백만원":1_000_000, "억원":100_000_000, "십억원":1_000_000_000}

def to_won(val, unit_multiplier):
    """문서 파싱 숫자 → 원(KRW) 단위 변환"""
    if val is None: return None
    return int(val * unit_multiplier)

def fmt(val_won, disp_unit):
    """원(KRW) → 선택 단위로 나눠 포맷"""
    if val_won is None: return "-"
    v = val_won / disp_unit
    if v == 0: return "0"
    if abs(v) < 1: return f"{v:.2f}"
    return f"{v:,.0f}"

def pct(a_won, b_won):
    if a_won is not None and b_won and b_won != 0:
        return f"{a_won/b_won*100:.1f}%"
    return "-"

# ── 숫자 파싱 ─────────────────────────────────────────────────────────────────
def parse_int(val):
    """문자열 → 정수 (괄호 음수 처리)"""
    try:
        s = str(val).replace(",","").replace(" ","").replace("\xa0","")
        if s.startswith("(") and s.endswith(")"): s = "-" + s[1:-1]
        return int(float(s))
    except: return None

def detect_unit(text):
    """
    문서 내 단위 감지 → 원 기준 승수 반환.
    주의: 단위 표기가 없으면 '원'으로 가정(=1).
    """
    t = text.replace(" ","").replace("\xa0","").replace("　","")
    if "단위:억원" in t or "(단위:억원)" in t: return 100_000_000
    if "단위:백만원" in t or "(단위:백만원)" in t: return 1_000_000
    if ("단위:천원" in t or "(단위:천원)" in t or
        "단위:1,000원" in t or "(단위:1,000원)" in t): return 1_000
    if "단위:원" in t or "(단위:원)" in t: return 1
    # 마지막 수단: 숫자 크기로 추정
    # 재무제표 숫자 중 매출 관련 숫자들이 어느 범위인지로 추정
    return None  # 감지 실패

# ── 파생 지표 (원 단위 기준) ──────────────────────────────────────────────────
def compute_derived(raw):
    """raw dict: 모든 금액 = 원(KRW) 단위. EBITDA 등 파생 계산."""
    op  = raw.get("영업이익")
    dep = raw.get("감가상각비")
    amd = raw.get("무형자산상각비")

    ebitda = None
    if op is not None:
        ebitda = op
        if dep: ebitda += dep
        if amd: ebitda += amd

    cash  = raw.get("현금및현금성자산")
    stfi  = raw.get("단기금융상품")
    cash_total = (cash or 0) + (stfi or 0) if (cash or stfi) else None

    debt_keys = ["단기차입금","유동성장기차입금","유동성사채","단기리스부채",
                 "장기차입금","사채","장기리스부채"]
    debt_parts = [(k, raw[k]) for k in debt_keys if raw.get(k)]
    total_debt = sum(v for _,v in debt_parts) if debt_parts else None

    raw["EBITDA"]      = ebitda
    raw["현금성자산"]  = cash_total
    raw["총차입금"]    = total_debt
    raw["_debt_parts"] = debt_parts
    return raw

# ── XBRL API ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_corp_list():
    import os
    p = os.path.join(os.path.dirname(__file__), "data", "corpcode.csv")
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna(""); df["stock_code"]=df["stock_code"].str.strip(); return df
    r = _get("https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key="+API_KEY, timeout=120)
    z = zipfile.ZipFile(io.BytesIO(r.content)); root = ET.fromstring(z.read("CORPCODE.xml"))
    return pd.DataFrame([{"corp_code":i.findtext("corp_code",""),"corp_name":i.findtext("corp_name",""),
                           "stock_code":i.findtext("stock_code","").strip()} for i in root.findall("list")])

def search_corp(name, df):
    return df[df["corp_name"].str.contains(name, na=False)].reset_index(drop=True)

@st.cache_data(ttl=3600)
def get_corp_info(cc):
    try: r=_get("https://opendart.fss.or.kr/api/company.json",{"crtfc_key":API_KEY,"corp_code":cc},30); return r.json()
    except: return {}

@st.cache_data(ttl=3600)
def get_fs(corp_code, year, rcode, fs_div):
    try:
        r = _get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                 {"crtfc_key":API_KEY,"corp_code":corp_code,"bsns_year":year,
                  "reprt_code":rcode,"fs_div":fs_div}, 60)
        d = r.json()
        if d.get("status") != "000": return None, d.get("message","")
        return pd.DataFrame(d["list"]), None
    except Exception as e: return None, str(e)

def fv(df, ids, col="thstrm_amount"):
    """XBRL DataFrame에서 계정값 추출 → 원(KRW) 단위 정수"""
    if df is None or df.empty: return None
    for aid in ids:
        rows = df[df["account_id"]==aid]
        if not rows.empty:
            v = parse_int(rows.iloc[0][col])
            if v is not None: return v
    return None

def fn(df, kw, col="thstrm_amount"):
    if df is None or df.empty: return None
    rows = df[df["account_nm"].str.contains(kw, na=False)]
    return parse_int(rows.iloc[0][col]) if not rows.empty else None

# ── 문서 파싱 (비상장) ────────────────────────────────────────────────────────
@st.cache_data(ttl=1800)
def find_filing(corp_code, year):
    bgn, end = f"{year}0101", f"{int(year)+2}0630"
    for ty in ["F","A",""]:
        try:
            r = _get("https://opendart.fss.or.kr/api/list.json",
                     {"crtfc_key":API_KEY,"corp_code":corp_code,"bgn_de":bgn,"end_de":end,
                      "pblntf_ty":ty,"page_count":100}, 30)
            d = r.json()
            if d.get("status")!="000" or not d.get("list"): continue
            items = d["list"]
            for kw in ["감사보고서","사업보고서","연결감사보고서","재무제표"]:
                for item in items:
                    if kw in item.get("report_nm",""): return item["rcept_no"],item.get("report_nm","")
            return items[0]["rcept_no"],items[0].get("report_nm","")
        except: continue
    return None, None

@st.cache_data(ttl=3600)
def get_zip(rcept_no):
    """document.xml ZIP → (html_text, [(fname,bytes)], [pdf_bytes], meta)"""
    meta = {"status":"not_tried","files":[],"html":0,"xml":0,"pdf":0,"error":None}
    try:
        r = _get("https://opendart.fss.or.kr/api/document.xml",
                 {"crtfc_key":API_KEY,"rcept_no":rcept_no}, 120)
        if r.content[:4]==b"<?xm" and b"<r>" in r.content[:400]:
            meta["status"]="api_error"; meta["error"]=r.content[:400].decode("utf-8","ignore")
            return None,[],[],meta
        z = zipfile.ZipFile(io.BytesIO(r.content)); files=z.namelist()
        meta["status"]="ok"; meta["files"]=files
        hf=[f for f in files if f.lower().endswith((".html",".htm"))]
        xf=[f for f in files if f.lower().endswith(".xml") and ".xsd" not in f.lower()]
        pf=[f for f in files if f.lower().endswith(".pdf")]
        meta["html"]=len(hf); meta["xml"]=len(xf); meta["pdf"]=len(pf)
        html_txt="".join(z.read(f).decode(enc,errors="ignore")+"\n"
                         for f in hf[:15] for enc in ["utf-8","cp949","euc-kr"]
                         if _try_decode(z.read(f),enc)) or None
        xml_list=[(f,z.read(f)) for f in xf[:5]]
        pdf_list=[z.read(f) for f in sorted(pf)[:5]]
        return html_txt, xml_list, pdf_list, meta
    except zipfile.BadZipFile as e: meta["status"]="bad_zip"; meta["error"]=str(e); return None,[],[],meta
    except Exception as e:          meta["status"]="exception"; meta["error"]=str(e); return None,[],[],meta

def _try_decode(b, enc):
    try: b.decode(enc); return True
    except: return False

_FIN_KWDS = {
    "매출액":           ["매출액","수익(매출액)","영업수익","총매출액"],
    "매출원가":         ["매출원가","제품매출원가","상품매출원가"],
    "매출총이익":       ["매출총이익","매출총손익"],
    "판관비":           ["판매비와관리비","판매비및관리비","판관비"],
    "영업이익":         ["영업이익","영업손익","영업이익(손실)"],
    "당기순이익":       ["당기순이익","당기순손익"],
    "자산총계":         ["자산총계","자산합계"],
    "현금및현금성자산": ["현금및현금성자산","현금과예금","현금및예금"],
    "단기금융상품":     ["단기금융상품"],
    "부채총계":         ["부채총계","부채합계"],
    "자본총계":         ["자본총계","자본합계"],
    "단기차입금":       ["단기차입금"],
    "유동성장기차입금": ["유동성장기차입금","유동성장기부채"],
    "장기차입금":       ["장기차입금"],
    "사채":             ["사채"],
    "감가상각비":       ["감가상각비"],
    "무형자산상각비":   ["무형자산상각비"],
}

def _parse_from_soup(soup, log=None):
    """BeautifulSoup으로 HTML 테이블 파싱 → {계정: 원단위값}"""
    def L(m):
        if log: log.append(m)
    # ── 단위 감지: 전체 텍스트 대신 테이블 캡션/헤더에서만 감지 ────────────
    unit_mult = None
    all_text  = soup.get_text()
    unit_mult = detect_unit(all_text)
    if unit_mult is None:
        # 숫자 분포로 추정: 재무 수치 평균 크기 파악
        nums = re.findall(r"\d[\d,]{3,}", all_text.replace(" ",""))
        if nums:
            vals = [parse_int(n) for n in nums[:50] if parse_int(n)]
            if vals:
                avg = sum(abs(v) for v in vals) / len(vals)
                # 평균값이 억원 단위 수준이면 단위:원, 천원 수준이면 단위:천원
                if avg > 1e10:   unit_mult = 1          # 원 단위로 입력됨
                elif avg > 1e7:  unit_mult = 1_000       # 천원 단위로 입력됨
                elif avg > 1e4:  unit_mult = 1_000_000   # 백만원 단위
                else:            unit_mult = 100_000_000 # 억원 단위
        if unit_mult is None: unit_mult = 1_000  # 기본값: 천원 (한국 회계 관행)
    L(f"    단위감지: {unit_mult:,}원")

    results = {}
    for table in soup.find_all("table"):
        tbl_txt = table.get_text()
        hits = sum(1 for kw in ["매출","자산","부채","자본","이익"] if kw in tbl_txt)
        if hits < 2: continue
        for row in table.find_all("tr"):
            cells = row.find_all(["td","th"])
            if len(cells) < 2: continue
            label = re.sub(r"[\s\xa0\u3000①②③④⑤]","",cells[0].get_text(strip=True))
            for key, kwds in _FIN_KWDS.items():
                if key in results: continue
                if any(label==k.replace(" ","") or label.startswith(k.replace(" ","")) for k in kwds):
                    for cell in cells[1:6]:
                        v = parse_int(cell.get_text())
                        if v is not None and abs(v) > 0:
                            results[key] = v * unit_mult  # 원 단위로 저장
                            L(f"    {key}: {v:,} × {unit_mult:,} = {v*unit_mult:,}원")
                            break
    L(f"    총 {len(results)}개 추출")
    return results

def _parse_text(text, log=None):
    """태그 제거 후 정규식 파싱 → {계정: 원단위값}"""
    def L(m):
        if log: log.append(m)
    clean = re.sub(r"<[^>]+>"," ", text)
    unit_mult = detect_unit(text)
    if unit_mult is None: unit_mult = 1_000
    L(f"    텍스트파싱 단위: {unit_mult:,}원")
    L("    미리보기: " + " | ".join([l.strip()[:40] for l in clean.splitlines() if l.strip()][:8]))
    NUM = re.compile(r"\(?\d[\d,]{2,}\)?")
    results = {}
    for key, kwds in _FIN_KWDS.items():
        if key in results: continue
        for kw in kwds:
            m = re.search(re.escape(kw.replace(" ","")), clean.replace(" ",""))
            if not m: continue
            window = clean.replace(" ","")[m.end():m.end()+200]
            for rn in NUM.findall(window):
                v = parse_int(rn)
                if v is not None and abs(v) >= 100:
                    results[key] = v * unit_mult  # 원 단위로 저장
                    L(f"    {key}: {rn}={v:,} × {unit_mult:,}")
                    break
            if key in results: break
    return results

def parse_docs(html_txt, xml_list, pdf_list, log=None):
    """HTML→XML→PDF 순서로 파싱, 항상 원 단위로 반환"""
    def L(m):
        if log: log.append(m)
    try: from bs4 import BeautifulSoup; BS_OK=True
    except: BS_OK=False; L("beautifulsoup4 없음")

    results = {}

    if html_txt and BS_OK:
        L("  [1] HTML")
        soup = BeautifulSoup(html_txt, "html.parser")
        results = _parse_from_soup(soup, log)
        if "매출액" in results: return results

    for fname, xb in xml_list:
        L(f"  [2] XML: {fname}")
        for enc in ["utf-8","cp949","euc-kr"]:
            try:
                xt = xb.decode(enc, errors="ignore")
                if len(xt) < 100: continue
                if BS_OK:
                    soup = BeautifulSoup(xt, "html.parser")
                    r = _parse_from_soup(soup, log)
                    if "매출액" in r: L("  ✅ XML HTML파싱 성공"); return r
                r = _parse_text(xt, log)
                if len(r) > len(results): results = r
                if "매출액" in results: L("  ✅ XML 텍스트파싱 성공"); return results
                break
            except: continue

    if pdf_list:
        L(f"  [3] PDF ({len(pdf_list)}개)")
        try:
            import pdfplumber
            for i, pb in enumerate(pdf_list):
                r = {}; full_txt = ""
                with pdfplumber.open(io.BytesIO(pb)) as pdf:
                    for page in pdf.pages[:80]:
                        full_txt += (page.extract_text() or "") + "\n"
                        for tbl in (page.extract_tables() or []):
                            for row in tbl:
                                if not row or len(row)<2: continue
                                lbl = str(row[0] or "").strip().replace(" ","")
                                for key, kwds in _FIN_KWDS.items():
                                    if key in r: continue
                                    for kw in kwds:
                                        if lbl==kw.replace(" ","") or lbl.startswith(kw.replace(" ","")):
                                            for cell in row[1:5]:
                                                v=parse_int(cell)
                                                if v and abs(v)>0: r[key]=v; break
                                            break
                unit_mult = detect_unit(full_txt) or 1_000
                r = {k:v*unit_mult for k,v in r.items()}  # 원 단위로 변환
                if len(r)>len(results): results=r
                if "매출액" in results: L(f"  ✅ PDF파싱 성공"); return results
        except ImportError: L("  pdfplumber 없음")

    return results

def doc_analyze(corp_code, year):
    log = [f"=== {year}년 ==="]
    rcept_no, nm = find_filing(corp_code, year)
    if not rcept_no: log.append("❌ 공시없음"); return None,None,"DART공시없음",log
    log.append(f"✅ {nm} ({rcept_no})")
    html_txt, xml_list, pdf_list, meta = get_zip(rcept_no)
    log.append(f"ZIP: {meta['status']} h={meta['html']} x={meta['xml']} p={meta['pdf']} files={meta['files'][:3]}")
    if meta.get("error"): log.append(f"  err: {str(meta['error'])[:150]}")
    raw = parse_docs(html_txt, xml_list, pdf_list, log)
    if "매출액" not in raw:
        log.append(f"❌실패 추출={list(raw.keys())}"); return None,None,"파싱실패",log
    raw = compute_derived(raw)
    log.append(f"✅ 매출액={raw['매출액']:,}원 = {raw['매출액']/1e8:.1f}억원")
    return raw, f"별도(문서·{nm})", None, log

def analyze(corp_code, year, fs_pref="CFS"):
    """
    원(KRW) 단위로 raw dict 반환.
    XBRL: DART API가 원 단위 반환 → 그대로 사용
    문서: detect_unit으로 환산 → 원 단위 저장
    """
    order = ([("CFS","연결"),("OFS","별도")] if fs_pref=="CFS"
             else [("OFS","별도"),("CFS","연결")])
    df = None; label = None
    for rcode in ["11011","11012","11013","11014"]:
        for fs_div, fs_name in order:
            d, _ = get_fs(corp_code, year, rcode, fs_div)
            if d is not None and not d.empty:
                sfx={"11011":"","11012":"(반기)","11013":"(1분기)","11014":"(3분기)"}.get(rcode,"")
                df=d; label=fs_name+"재무제표"+sfx; break
        if df: break

    if df is not None:
        raw = {}
        for nm, ids in {**IS_IDS,**BS_IDS,**CF_IDS}.items():
            # XBRL: parse_int는 원(KRW) 단위 반환
            raw[nm] = fv(df,ids) or fn(df,nm)
        return compute_derived(raw), label, None, []

    return doc_analyze(corp_code, year)

# ── 재무표 HTML 생성 ──────────────────────────────────────────────────────────
def make_table_html(year_data, disp_unit, unit_label):
    years = sorted(year_data.keys())
    ROWS = [
        ("매출액","매출액","hl"),    ("Growth","Growth","sub"),
        ("매출원가","매출원가",""),   ("매출원가율","매출원가율","sub"),
        ("매출총이익","매출총이익","hl"), ("매출총이익률","매출총이익률","sub"),
        ("판관비","판관비",""),       ("판관비율","판관비율","sub"),
        ("EBITDA","EBITDA","hl"),    ("EBITDA M","EBITDA Margin","sub"),
        ("영업이익","영업이익","hl"), ("영업이익률","영업이익률","sub"),
        ("당기순이익","당기순이익","hl"),("순이익률","순이익률","sub"),
        ("자산총계","자산총계","hl"), ("현금성자산","현금성자산","sub"),
        ("부채총계","부채총계","hl"), ("총차입금","총차입금","sub"),
        ("자본총계","자본총계","hl"),
    ]
    # 연도별 계산
    vals = {}
    for i, yr in enumerate(years):
        d   = year_data[yr]
        rv  = d.get("매출액");    cg = d.get("매출원가"); gp = d.get("매출총이익")
        sg  = d.get("판관비");    op = d.get("영업이익"); ni = d.get("당기순이익")
        eb  = d.get("EBITDA");   ast= d.get("자산총계"); cs = d.get("현금성자산")
        lb  = d.get("부채총계"); db = d.get("총차입금"); eq = d.get("자본총계")
        pr  = year_data[years[i-1]].get("매출액") if i>0 else None
        rv_d = rv/disp_unit if rv else None
        pr_d = pr/disp_unit if pr else None
        vals[yr] = {
            "매출액":      fmt(rv,disp_unit),
            "Growth":      (f"{(rv_d/pr_d-1)*100:.1f}%" if rv_d and pr_d and pr_d!=0 else "-"),
            "매출원가":    fmt(cg,disp_unit), "매출원가율":   pct(cg,rv),
            "매출총이익":  fmt(gp,disp_unit), "매출총이익률": pct(gp,rv),
            "판관비":      fmt(sg,disp_unit), "판관비율":     pct(sg,rv),
            "EBITDA":      fmt(eb,disp_unit), "EBITDA Margin":pct(eb,rv),
            "영업이익":    fmt(op,disp_unit), "영업이익률":   pct(op,rv),
            "당기순이익":  fmt(ni,disp_unit), "순이익률":     pct(ni,rv),
            "자산총계":    fmt(ast,disp_unit),"현금성자산":   fmt(cs,disp_unit),
            "부채총계":    fmt(lb,disp_unit), "총차입금":     fmt(db,disp_unit),
            "자본총계":    fmt(eq,disp_unit),
        }
    hdr = "".join(f"<th>{y}</th>" for y in years)
    html = f'<div style="overflow-x:auto"><table class="fin-table"><thead><tr><th>(단위:{unit_label})</th>{hdr}</tr></thead><tbody>'
    for key, label, cls in ROWS:
        tc = f' class="{cls}"' if cls else ""
        cells = "".join(f"<td>{vals[y].get(key,'-')}</td>" for y in years)
        html += f"<tr{tc}><td>{label}</td>{cells}</tr>"
    html += "</tbody></table></div>"
    return html

# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════
st.title("📊 DART 재무 분석")
st.caption("금융감독원 전자공시(DART) 기반 요약 재무제표 자동 생성")
with st.sidebar:
    if st.button("로그아웃"): st.session_state.auth=False; st.rerun()

# STEP 1 ──────────────────────────────────────────────────────────────────────
st.markdown("#### STEP 1 · 기업 검색")
with st.form("sf"):
    c1,c2 = st.columns([5,1])
    with c1: q = st.text_input("기업명", placeholder="예: 삼성전자, 이노켐", label_visibility="collapsed")
    with c2: srch = st.form_submit_button("검색 🔍", use_container_width=True, type="primary")

if srch and q:
    try:
        with st.spinner("로딩..."):
            corp_df = get_corp_list()
        res = search_corp(q, corp_df).head(50).reset_index(drop=True)
        if res.empty: st.warning("결과 없음"); st.session_state.pop("rows",None)
        else:
            with st.spinner(f"기업 정보 ({len(res)}개)..."):
                rows=[]
                for _,row in res.iterrows():
                    info=get_corp_info(row["corp_code"]); ok=info.get("status")=="000"
                    rows.append({"_cc":row["corp_code"],"기업명":row["corp_name"],
                                 "대표자":info.get("ceo_nm","-") if ok else "-",
                                 "업종":_ind(info.get("induty_code","")) if ok else "-",
                                 "상장":CORP_CLS.get(info.get("corp_cls",""),"비상장")})
            st.session_state["rows"]=rows; st.session_state["si"]=-1
            for k in ("corp","step2","result"): st.session_state.pop(k,None)
    except Exception as e:
        st.error("오류: "+str(e))

if "rows" in st.session_state:
    rows = st.session_state["rows"]; si = st.session_state.get("si",-1)
    h1,h2,h3,h4 = st.columns([2.5,1.5,3.5,1.2])
    for hc,ht in zip([h1,h2,h3,h4],["기업명","대표자","업종","상장구분"]):
        hc.markdown(f"<div style='background:#2c3e50;color:#fff;padding:6px 10px;"
                    f"font-weight:600;font-size:0.83rem;text-align:center'>{ht}</div>",
                    unsafe_allow_html=True)
    for i,row in enumerate(rows):
        dc="row-sel" if i==si else "row-btn"
        r1,r2,r3,r4=st.columns([2.5,1.5,3.5,1.2]); clicked=False
        for rc,vl,k in [(r1,row["기업명"],f"a{i}"),(r2,row["대표자"],f"b{i}"),
                         (r3,row["업종"],f"c{i}"),(r4,row["상장"],f"d{i}")]:
            with rc:
                st.markdown(f'<div class="{dc}">',unsafe_allow_html=True)
                if st.button(vl,key=k,use_container_width=True): clicked=True
                st.markdown('</div>',unsafe_allow_html=True)
        if clicked:
            st.session_state["si"]=i; st.session_state["corp"]=row
            st.session_state["step2"]=True; st.session_state.pop("result",None)
            st.rerun()
    if 0<=si<len(rows):
        ch=rows[si]; st.success(f"✅ **{ch['기업명']}** | {ch['대표자']} | {ch['상장']}")

st.divider()

# STEP 2 ──────────────────────────────────────────────────────────────────────
if st.session_state.get("step2"):
    corp=st.session_state["corp"]; cc=corp["_cc"]; cn=corp["기업명"]
    st.markdown(f"#### STEP 2 · 조회 설정  —  **{cn}**")

    ss=st.session_state; all_yrs=[str(y) for y in range(CURRENT_YEAR,2009,-1)]; lat=CURRENT_YEAR-1
    for k,v in [("yr_f",str(lat-4)),("yr_t",str(lat)),("ap",None),("au","억원"),("afs","연결")]:
        if k not in ss: ss[k]=v

    col_u,col_f,col_p,col_y=st.columns([1.8,1.5,2.0,2.5])

    with col_u:
        st.markdown("**단위**")
        for ul in ["천원","백만원","억원","십억원"]:
            ia=(ss["au"]==ul)
            st.markdown(f'<div class="{"btn-active" if ia else ""}">',unsafe_allow_html=True)
            if st.button(ul,key=f"u{ul}",use_container_width=True): ss["au"]=ul; st.rerun()
            st.markdown('</div>',unsafe_allow_html=True)

    with col_f:
        st.markdown("**재무제표 구분**")
        for fl in ["연결","별도"]:
            ia=(ss["afs"]==fl)
            st.markdown(f'<div class="{"btn-active" if ia else ""}">',unsafe_allow_html=True)
            if st.button(fl,key=f"f{fl}",use_container_width=True): ss["afs"]=fl; st.rerun()
            st.markdown('</div>',unsafe_allow_html=True)
        fs_pref="CFS" if ss["afs"]=="연결" else "OFS"

    with col_p:
        st.markdown("**기간 선택**")
        for pl,yrs in [("5년",5),("10년",10),("20년",20)]:
            ia=(ss["ap"]==pl)
            st.markdown(f'<div class="{"btn-active" if ia else ""}">',unsafe_allow_html=True)
            if st.button(pl,key=f"p{pl}",use_container_width=True):
                ss["ap"]=pl; ss["yr_f"]=str(max(2010,lat-yrs+1)); ss["yr_t"]=str(lat); st.rerun()
            st.markdown('</div>',unsafe_allow_html=True)

    with col_y:
        st.markdown("**상세 기간**")
        fi=all_yrs.index(ss["yr_f"]) if ss["yr_f"] in all_yrs else len(all_yrs)-1
        ti=all_yrs.index(ss["yr_t"]) if ss["yr_t"] in all_yrs else 0
        yf=st.selectbox("시작",all_yrs,index=fi,key="sbf")
        yt=st.selectbox("종료",all_yrs,index=ti,key="sbt")
        if yf!=ss["yr_f"]: ss["yr_f"]=yf; ss["ap"]=None
        if yt!=ss["yr_t"]: ss["yr_t"]=yt; ss["ap"]=None

    if int(yf)>int(yt): st.warning("시작≤종료 필요"); st.stop()
    sel_yrs=[str(y) for y in range(int(yf),int(yt)+1)]
    st.caption(f"📅 {yf}~{yt}년 ({len(sel_yrs)}개) | 단위:{ss['au']} | {ss['afs']}우선")

    if st.button("📊 재무제표 출력", type="primary", use_container_width=True):
        yd,yft,dbg={},{},{}
        prog=st.progress(0)
        for i,yr in enumerate(sel_yrs):
            prog.progress((i+1)/len(sel_yrs), text=f"{yr}년...")
            res=analyze(cc,yr,fs_pref); d,fl,er,lg=res if len(res)==4 else (*res,[])
            dbg[yr]=lg
            if d: yd[yr]=d; yft[yr]=fl
            else: st.warning(f"{yr}년: {er}")
        prog.empty()
        if yd:
            ss["result"]={"yd":yd,"yft":yft,"cn":cn,"cc":cc,"dbg":dbg,
                          "mixed":any("연결" in f for f in yft.values()) and any("별도" in f for f in yft.values())}
        else:
            st.error("데이터 없음")
            for yr,lg in dbg.items():
                if lg:
                    with st.expander(f"🔍 {yr}년 로그"): st.code("\n".join(lg))

st.divider()

# STEP 3 ──────────────────────────────────────────────────────────────────────
if "result" in st.session_state:
    R=st.session_state["result"]; yd=R["yd"]; yft=R["yft"]
    cn=R["cn"]; cc=R["cc"]; mixed=R["mixed"]; ys=sorted(yd.keys())

    au  = st.session_state.get("au","억원")
    du  = DISP_UNIT.get(au,100_000_000)

    st.markdown(f"#### STEP 3 · 결과  —  **{cn}**")
    if mixed: st.warning("⚠️ 연결/별도 혼재: "+" | ".join(f"{y}:{yft[y]}" for y in ys))
    else:      st.info(f"📋 {list(yft.values())[0]} (전 연도)")

    doc_ys=[y for y,f in yft.items() if "문서" in f]
    if doc_ys:
        st.info(f"📄 {','.join(doc_ys)}년: XBRL 미제출 → 감사보고서 파싱")
        with st.expander("🔍 파싱 로그"):
            for y in doc_ys:
                lg=R.get("dbg",{}).get(y,[])
                if lg: st.markdown(f"**{y}년**"); st.code("\n".join(lg))

    info=get_corp_info(cc)
    if info.get("status")=="000":
        with st.expander("기업 정보",expanded=False):
            c1,c2,c3,c4=st.columns(4)
            c1.metric("기업명",info.get("corp_name","-")); c2.metric("대표자",info.get("ceo_nm","-"))
            c3.metric("설립일",info.get("est_dt","-")); c4.metric("결산월",(info.get("acc_mt") or "-")+"월")

    # 원단위 확인 expander
    with st.expander("🔬 단위 검증 (저장값 확인)",expanded=False):
        st.markdown("저장된 값은 모두 **원(KRW) 단위**입니다. 아래에서 정확성을 확인하세요.")
        vchk=[]
        for yr in ys:
            rv=yd[yr].get("매출액"); op=yd[yr].get("영업이익")
            vchk.append({"연도":yr,"매출액(원)":f"{rv:,}" if rv else "-",
                          f"매출액({au})":f"{rv/du:,.1f}" if rv else "-",
                          f"영업이익({au})":f"{op/du:,.1f}" if op else "-",
                          "데이터소스":yft.get(yr,"-")})
        st.dataframe(pd.DataFrame(vchk),hide_index=True,use_container_width=True)

    with st.expander("1단계: 원재료 (원 단위)",expanded=False):
        ri=(list(IS_IDS.keys())+["현금및현금성자산","단기금융상품","감가상각비","무형자산상각비"]+
            ["단기차입금","유동성장기차입금","유동성사채","단기리스부채","장기차입금","사채","장기리스부채","자산총계","부채총계","자본총계"])
        rr=[]
        for it in ri:
            rd={"계정":it}
            for y in ys: v=yd[y].get(it); rd[y]=f"{v:,}" if v else "미조회"
            rr.append(rd)
        st.dataframe(pd.DataFrame(rr),use_container_width=True,hide_index=True)

    with st.expander("2단계: EBITDA / 현금성자산 / 총차입금",expanded=False):
        for yr in ys:
            d=yd[yr]; st.markdown(f"**── {yr}년 ({yft.get(yr,'-')}) ──**")
            eb=d.get("EBITDA"); cs=d.get("현금성자산"); db=d.get("총차입금")
            ca,cb,cc2=st.columns(3)
            with ca: st.markdown("**EBITDA**"); st.code(f"{eb/du:,.1f} {au}" if eb else "없음",language=None)
            with cb: st.markdown("**현금성자산**"); st.code(f"{cs/du:,.1f} {au}" if cs else "없음",language=None)
            with cc2: st.markdown("**총차입금**"); st.code(f"{db/du:,.1f} {au}" if db else "없음",language=None)
            dp=d.get("_debt_parts",[])
            if dp:
                st.dataframe(pd.DataFrame([{"항목":k,f"금액({au})":f"{v/du:,.1f}"} for k,v in dp]
                             +[{"항목":"합계",f"금액({au})":f"{sum(v for _,v in dp)/du:,.1f}"}]),
                             use_container_width=False,hide_index=True)

    st.markdown(f"##### 최종 요약 재무제표 (단위: {au})")
    st.markdown(make_table_html(yd,du,au), unsafe_allow_html=True)

    with st.expander("교차 검증",expanded=False):
        vr=[{"연도":y,"재무제표":yft.get(y,"-"),
             f"매출액({au})":f"{yd[y].get('매출액',0)/du:,.1f}" if yd[y].get('매출액') else "-",
             f"EBITDA({au})":f"{yd[y].get('EBITDA',0)/du:,.1f}" if yd[y].get('EBITDA') else "-"}
            for y in ys]
        st.dataframe(pd.DataFrame(vr),use_container_width=True,hide_index=True)

    st.markdown("##### 손익 추이")
    fig=go.Figure()
    for acc,col in zip(["매출액","EBITDA","영업이익","당기순이익"],["#1f77b4","#9467bd","#2ca02c","#ff7f0e"]):
        vals=[yd[y].get(acc,0)/du if yd[y].get(acc) else None for y in ys]
        fig.add_trace(go.Bar(name=acc,x=ys,y=vals,marker_color=col,
            text=[f"{v:,.1f}" if v else "-" for v in vals],textposition="outside"))
    fig.update_layout(barmode="group",yaxis_title=au,
        legend=dict(orientation="h",yanchor="bottom",y=1.02),height=400,plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig,use_container_width=True)

    st.divider()
    crows=[]
    for key in ["매출액","매출원가","매출총이익","판관비","영업이익","당기순이익",
                "EBITDA","자산총계","현금성자산","부채총계","총차입금","자본총계"]:
        rd={"계정":key}
        for y in ys: v=yd[y].get(key); rd[y]=f"{v/du:,.1f}" if v else "-"
        crows.append(rd)
    csv=pd.DataFrame(crows).to_csv(index=False,encoding="utf-8-sig")
    st.download_button(f"⬇️ CSV (단위:{au})",csv,cn+"_재무.csv","text/csv")
