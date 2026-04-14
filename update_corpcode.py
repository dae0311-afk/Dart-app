"""
로컬 PC에서 1회 실행 → data/corpcode.csv 생성 → GitHub에 커밋
"""
import os, zipfile, io, requests, xml.etree.ElementTree as ET, pandas as pd

API_KEY = input("DART API Key 입력: ").strip()

print("다운로드 중...")
r = requests.get(
    "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=" + API_KEY,
    timeout=120
)
z    = zipfile.ZipFile(io.BytesIO(r.content))
root = ET.fromstring(z.read("CORPCODE.xml"))

corps = [{"corp_code": item.findtext("corp_code",""),
          "corp_name":  item.findtext("corp_name",""),
          "stock_code": item.findtext("stock_code","").strip()}
         for item in root.findall("list")]

os.makedirs("data", exist_ok=True)
pd.DataFrame(corps).to_csv("data/corpcode.csv", index=False, encoding="utf-8-sig")
print(f"완료: data/corpcode.csv ({len(corps):,}개 기업)")
print("→ 이 파일을 GitHub에 커밋하면 타임아웃 해결됩니다.")
