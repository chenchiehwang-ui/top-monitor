# -*- coding: utf-8 -*-
"""
逃頂監測儀表板:自動抓數腳本(由 GitHub Actions 每日執行)
可自動項目:保證金借款MoM、S&P前十權重、巴菲特指標(Z.1近似)、
          M2−NDX增速差、H.4.1準備金、CBOE SKEW(參考值)
手動項目(改 manual.json):0DTE佔比、內部人賣買比、遠期P/E、25Δ偏度差、雲capex
任一抓取失敗時,保留 data.json 中的舊值並標記 ok=false。
"""
import json, os, datetime
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
FRED_KEY = os.environ.get("FRED_API_KEY", "")
DATA_FILE = "data.json"


def fred_obs(series, limit=1):
    """回傳 FRED 觀測值(新→舊),已濾除缺值。"""
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series, "api_key": FRED_KEY, "file_type": "json",
                "sort_order": "desc", "limit": limit},
        timeout=30)
    r.raise_for_status()
    return [o for o in r.json()["observations"] if o["value"] != "."]


def yahoo_chart(symbol, rng, interval):
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": rng, "interval": interval}, headers=UA, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]


# ---------- 各項抓取 ----------

def get_h41():
    """Fed 準備金餘額(WRESBAL,十億美元→兆美元),每週三數據週四發布。"""
    obs = fred_obs("WRESBAL", 1)
    return {"value": round(float(obs[0]["value"]) / 1000.0, 3), "asof": obs[0]["date"]}


def get_m2_ndx_gap():
    """M2 年增率 − NDX 年增率(pp)。負得越深 = 流動性與股價背離越嚴重。"""
    m2 = fred_obs("M2SL", 15)
    m2_yoy = (float(m2[0]["value"]) / float(m2[12]["value"]) - 1) * 100
    closes = yahoo_chart("%5ENDX", "2y", "1mo")
    ndx_yoy = (closes[-1] / closes[-13] - 1) * 100
    return {"value": round(m2_yoy - ndx_yoy, 1),
            "m2_yoy": round(m2_yoy, 1), "ndx_yoy": round(ndx_yoy, 1),
            "asof": m2[0]["date"]}


def get_buffett():
    """巴菲特指標近似:Z.1 企業股權(NCBEILQ027S,百萬)/ 名目GDP(十億)。
    季度數據、約落後一季;僅作水位參考。"""
    eq = fred_obs("NCBEILQ027S", 1)
    gdp = fred_obs("GDP", 1)
    v = (float(eq[0]["value"]) / 1000.0) / float(gdp[0]["value"]) * 100
    return {"value": round(v, 1), "asof": eq[0]["date"]}


def get_top10():
    """Slickcharts 前十大權重加總。"""
    r = requests.get("https://www.slickcharts.com/sp500", headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    body = soup.find("table").find("tbody")
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
    """FINRA 保證金借款(debit balances)月環比。頁面改版時會失敗並保留舊值。"""
    r = requests.get(
        "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics",
        headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.find("table").find_all("tr")
    vals, months = [], []
    for tr in rows[1:6]:
        tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(tds) >= 2:
            try:
                vals.append(float(tds[1].replace(",", "").replace("$", "")))
                months.append(tds[0])
            except ValueError:
                continue
        if len(vals) == 2:
            break
    if len(vals) < 2:
        raise ValueError("FINRA 表格解析失敗")
    mom = (vals[0] / vals[1] - 1) * 100
    return {"value": round(mom, 1), "asof": months[0]}


def get_skew_index():
    """CBOE SKEW 指數(參考值,非 25Δ 偏度差)。"""
    closes = yahoo_chart("%5ESKEW", "1mo", "1d")
    return {"value": round(closes[-1], 1), "asof": datetime.date.today().isoformat()}


# ---------- 主流程 ----------

FETCHERS = {
    "margin_mom": get_margin_mom,
    "odte_ref": None,            # 無免費API,手動
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
        if fn is None:
            continue
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
