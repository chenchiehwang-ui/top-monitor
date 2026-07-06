# -*- coding: utf-8 -*-
"""
逃頂監測儀表板:自動抓數腳本(GitHub Actions 每日執行)· v3
- NDX 改用 FRED NASDAQ100 序列(同一把 key,無封鎖問題)
- FINRA 保證金三層防線:直連 → r.jina.ai 代理 → manual.json 手填值
- 修正 utcnow 棄用警告
"""
import json, os, re, datetime
import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
FRED_KEY = os.environ.get("FRED_API_KEY", "")
DATA_FILE = "data.json"
FINRA_URL = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


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
    """Fed 準備金餘額。WRESBAL 單位=百萬美元 → 轉兆美元。"""
    obs = fred_obs("WRESBAL", 1)
    return {"value": round(float(obs[0]["value"]) / 1_000_000, 3), "asof": obs[0]["date"]}


def get_m2_ndx_gap():
    """M2 年增率 − NDX 年增率(pp)。兩者皆走 FRED。"""
    m2 = fred_obs("M2SL", 15)
    m2_yoy = (float(m2[0]["value"]) / float(m2[12]["value"]) - 1) * 100

    ndx = fred_obs("NASDAQ100", 300)  # 日線,約含 14 個月
    latest_v = float(ndx[0]["value"])
    latest_d = datetime.date.fromisoformat(ndx[0]["date"])
    target = latest_d - datetime.timedelta(days=365)
    base = None
    for o in ndx:  # 新→舊,找一年前(含)最近的一筆
        if datetime.date.fromisoformat(o["date"]) <= target:
            base = float(o["value"])
            break
    if base is None:
        base = float(ndx[-1]["value"])  # 資料不足一年時取最舊一筆
    ndx_yoy = (latest_v / base - 1) * 100

    return {"value": round(m2_yoy - ndx_yoy, 1),
            "m2_yoy": round(m2_yoy, 1), "ndx_yoy": round(ndx_yoy, 1),
            "asof": m2[0]["date"]}


def get_buffett():
    eq = fred_obs("NCBEILQ027S", 1)   # 百萬美元,季度
    gdp = fred_obs("GDP", 1)          # 十億美元,季度
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


def _parse_margin_pairs(text_or_html, is_html):
    """從 FINRA 頁面(HTML)或 jina 轉出的純文字中,取最新兩個月的
    (月份, debit balances) 並回傳環比。"""
    pairs = []
    if is_html:
        soup = BeautifulSoup(text_or_html, "html.parser")
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                for cell in cells[1:]:
                    s = cell.replace(",", "").replace("$", "")
                    try:
                        pairs.append((cells[0], float(s)))
                        break
                    except ValueError:
                        continue
            if len(pairs) >= 2:
                break
    else:
        # jina 輸出:找 markdown 表格列,如 | May-26 | 1,234,567 | ...
        for line in text_or_html.splitlines():
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue
            m = re.match(r"^[A-Za-z]{3,9}[- ]?'?\d{2,4}$", cells[0])
            if not m:
                continue
            s = cells[1].replace(",", "").replace("$", "")
            try:
                pairs.append((cells[0], float(s)))
            except ValueError:
                continue
            if len(pairs) >= 2:
                break
    if len(pairs) < 2 or pairs[1][1] == 0:
        raise ValueError("無法解析出兩個月的數據")
    mom = (pairs[0][1] / pairs[1][1] - 1) * 100
    return {"value": round(mom, 1), "asof": pairs[0][0]}


def get_margin_mom():
    """FINRA 保證金借款月環比,三層防線。"""
    # 第一層:直連(帶完整瀏覽器標頭)
    try:
        r = requests.get(FINRA_URL, headers=UA, timeout=30)
        r.raise_for_status()
        return _parse_margin_pairs(r.text, is_html=True)
    except Exception as e1:
        print(f"       margin 直連失敗:{e1}")

    # 第二層:r.jina.ai 代理(將頁面轉為純文字)
    try:
        r = requests.get("https://r.jina.ai/" + FINRA_URL, timeout=60)
        r.raise_for_status()
        return _parse_margin_pairs(r.text, is_html=False)
    except Exception as e2:
        print(f"       margin 代理失敗:{e2}")

    # 第三層:manual.json 手填值
    if os.path.exists("manual.json"):
        with open("manual.json", encoding="utf-8") as f:
            m = json.load(f).get("margin_mom", {})
        if isinstance(m.get("value"), (int, float)):
            return {"value": m["value"], "asof": str(m.get("asof", "手填")),
                    "note": "自動來源被擋,使用 manual.json 手填值"}
    raise ValueError("直連403、代理失敗、manual.json 亦無 margin_mom 手填值")


def get_skew_index():
    """CBOE SKEW 指數,官方歷史 CSV。"""
    import io, csv
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
            item["fetched"] = now_utc().strftime("%Y-%m-%d %H:%M UTC")
            auto[key] = item
            print(f"[ok]   {key} = {item['value']} (asof {item.get('asof')})")
        except Exception as e:
            prev = old.get(key, {})
            prev["ok"] = False
            prev["error"] = str(e)[:120]
            auto[key] = prev
            print(f"[FAIL] {key}: {e}")

    out = {
        "generated": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "auto": auto,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("data.json written.")


if __name__ == "__main__":
    main()
