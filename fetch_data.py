# -*- coding: utf-8 -*-
"""
逃頂監測儀表板:自動抓數腳本(GitHub Actions 每日執行)· v2
修正:H.4.1 單位(FRED WRESBAL 原始單位為百萬美元)
更換:NDX 改用 Stooq、SKEW 改用 CBOE 官方 CSV(Yahoo 封鎖 GitHub 機房 IP)
強化:FINRA 保證金表格解析改為全頁掃描
"""
import json, os, io, csv, datetime
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
FRED_KEY = os.environ.get("FRED_API_KEY", "")
DATA_FILE = "data.json"


def fred_obs(series, limit=1):
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series, "api_key": FRED_KEY, "file_type": "json",
                "sort_order": "desc", "limit": limit},
        timeout=30)
    r.raise_for_status()
    return [o for o in r.json()["observations"] if o["value"] != "."]


# ---------- 各項抓取 ----------

def get_h41():
    """Fed 準備金餘額。WRESBAL 單位=百萬美元 → 除以 1,000,000 轉兆美元。"""
    obs = fred_obs("WRESBAL", 1)
    return {"value": round(float(obs[0]["value"]) / 1_000_000, 3), "asof": obs[0]["date"]}


def _stooq_monthly_closes(symbol):
    """Stooq 月線 CSV:Date,Open,High,Low,Close,Volume(舊→新)。"""
    r = requests.get(f"https://stooq.com/q/d/l/?s={symbol}&i=m", headers=UA, timeout=30)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    closes = [float(row["Close"]) for row in rows if row.get("Close") not in (None, "", "N/A")]
    if len(closes) < 14:
        raise ValueError(f"Stooq {symbol} 資料筆數不足")
    return closes


def get_m2_ndx_gap():
    """M2 年增率 − NDX 年增率(pp)。NDX 來源:Stooq(^ndx)。"""
    m2 = fred_obs("M2SL", 15)
    m2_yoy = (float(m2[0]["value"]) / float(m2[12]["value"]) - 1) * 100
    closes = _stooq_monthly_closes("%5Endx")
    ndx_yoy = (closes[-1] / closes[-13] - 1) * 100
    return {"value": round(m2_yoy - ndx_yoy, 1),
            "m2_yoy": round(m2_yoy, 1), "ndx_yoy": round(ndx_yoy, 1),
            "asof": m2[0]["date"]}


def get_buffett():
    """Z.1 企業股權(百萬)/ 名目GDP(十億)。季度、約落後一季。"""
    eq = fred_obs("NCBEILQ027S", 1)
    gdp = fred_obs("GDP", 1)
    v = (float(eq[0]["value"]) / 1000.0) / float(gdp[0]["value"]) * 100
    return {"value": round(v, 1), "asof": eq[0]["date"]}


def get_top10():
    r = requests.get("https://www.slickcharts.com/sp500", headers=UA, timeout=30)
    r.raise_for_status()
    body = BeautifulSoup(r.text, "html.parser").find("table").find("tbody")
    total, n = 0.0, 0
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        total += float(tds[3].get_text(strip=True).replace("%", ""))
        n += 1
        if n == 10:
            break
    if n < 10:
        raise ValueError("Slickcharts 表格列數不足")
    return {"value": round(total, 1), "asof": datetime.date.today().isoformat()}


def get_margin_mom():
    """FINRA 保證金借款月環比:掃描整頁所有表格,找含 debit/margin 字樣者,
    取每列第一個可解析的數字欄位,以最新兩個月計算環比。"""
    r = requests.get(
        "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics",
        headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    tables = soup.find_all("table")
    if not tables:
        raise ValueError("頁面上找不到任何表格(可能改為JS載入)")

    def score(t):
        txt = t.get_text(" ", strip=True).lower()
        return ("debit" in txt) * 2 + ("margin" in txt)

    tables.sort(key=score, reverse=True)
    for table in tables:
        vals, months = [], []
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            for cell in cells[1:]:
                s = cell.replace(",", "").replace("$", "").replace("%", "")
                try:
                    v = float(s)
                except ValueError:
                    continue
                vals.append(v)
                months.append(cells[0])
                break
            if len(vals) == 2:
                break
        if len(vals) == 2 and vals[1] != 0:
            mom = (vals[0] / vals[1] - 1) * 100
            # 若表格為舊→新排序(月環比算出來像年累積),取絕對值合理性檢查
            return {"value": round(mom, 1), "asof": months[0]}
    raise ValueError("FINRA 表格解析失敗(頁面可能改版)")


def get_skew_index():
    """CBOE SKEW 指數,官方歷史 CSV。"""
    r = requests.get(
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv",
        headers=UA, timeout=30)
    r.raise_for_status()
    rows = [row for row in csv.reader(io.StringIO(r.text)) if row]
    header = [h.strip().upper() for h in rows[0]]
    col = header.index("SKEW") if "SKEW" in header else 1
    last = rows[-1]
    return {"value": round(float(last[col]), 1), "asof": last[0]}


FETCHERS = {
    "margin_mom": get_margin_mom,
    "top10": get_top10,
    "buffett": get_buffett,
    "m2_ndx_gap": get_m2_ndx_gap,
    "h41": get_h41,
    "skew_index": get_skew_index,
}


def main():
    old = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            old = json.load(f).get("auto", {})

    auto = {}
    for key, fn in FETCHERS.items():
        try:
            item = fn()
            item["ok"] = True
            item["fetched"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            auto[key] = item
            print(f"[ok]   {key} = {item['value']} (asof {item.get('asof')})")
        except Exception as e:
            prev = old.get(key, {})
            prev["ok"] = False
            prev["error"] = str(e)[:120]
            auto[key] = prev
            print(f"[FAIL] {key}: {e}")

    out = {
        "generated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "auto": auto,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("data.json written.")


if __name__ == "__main__":
    main()
