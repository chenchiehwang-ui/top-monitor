# 逃頂監測儀表板(GitHub Actions 自動更新版)

每個交易日台北時間早上 06:30,GitHub 自動抓取最新數據並更新網頁。手機開網址即是最新狀態。

## 自動 / 手動分工

| 項目 | 方式 | 來源 |
|---|---|---|
| 保證金借款月環比 | 自動(爬 FINRA) | finra.org |
| S&P 前十權重 | 自動(爬 Slickcharts) | slickcharts.com |
| 巴菲特指標 | 自動(FRED Z.1 季度近似) | FRED |
| M2 vs NDX 增速差 | 自動 | FRED + Yahoo |
| H.4.1 準備金 | 自動 | FRED WRESBAL |
| CBOE SKEW(參考) | 自動 | Yahoo |
| 0DTE 佔比 | 手動,約每月 | CBOE Insights |
| 內部人賣買比 | 手動,約每週~每月 | openinsider.com |
| 遠期 P/E | 手動,每週五 | FactSet Earnings Insight |
| 25Δ 偏度差 | 手動 | Market Chameleon |
| 雲廠商 CapEx 環比 | 手動,財報季 | 四大雲廠商財報 |

手動項目:直接在 GitHub 網頁編輯 `manual.json`(手機也可操作),commit 後儀表板即讀到新值。頁面上直接改數字只存在該瀏覽器(localStorage),要跨裝置同步請改 manual.json。

## 部署步驟(一次性,約 10 分鐘)

1. **建 repo**:GitHub 新建 public repository(例如 `top-monitor`),把本資料夾所有檔案上傳,**保持 `.github/workflows/update.yml` 的路徑結構**。
2. **申請 FRED API key**(免費):https://fred.stlouisfed.org/docs/api/api_key.html 註冊後取得一串 key。
3. **設定 Secret**:repo → Settings → Secrets and variables → Actions → New repository secret,名稱填 `FRED_API_KEY`,值貼上 key。
4. **開啟寫入權限**:Settings → Actions → General → Workflow permissions → 選 **Read and write permissions** → Save。
5. **開啟 Pages**:Settings → Pages → Source 選 **Deploy from a branch** → Branch 選 `main`、資料夾 `/ (root)` → Save。網址會是 `https://你的帳號.github.io/top-monitor/`。
6. **手動跑第一次**:Actions 分頁 → 選 `update-data` → Run workflow。成功後 repo 會多出 `data.json`,網頁即顯示真實數據。

## 維護

- 排程寫在 `update.yml` 的 cron(目前:週一至五 22:30 UTC)。
- 爬蟲類來源(FINRA、Slickcharts)網頁改版時該項會抓取失敗,儀表板顯示紅色「來源失效」並沿用舊值——把錯誤訊息(Actions log 裡)貼給 Claude 修 `fetch_data.py` 即可。
- 閾值想調整:改 `index.html` 開頭的 `IND` 陣列。

## 已知限制

- 巴菲特指標用 Z.1 季度數據近似(Wilshire 5000 系列已停更),數值口徑與影片來源可能略有差異,重點看趨勢與相對水位。
- 「觸發 5/7 → 4–8 週見頂」的回測樣本只有 1999–2000 一次,請視為脆弱度儀器,不凌駕既有價格觸發規則。
