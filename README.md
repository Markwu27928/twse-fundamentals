# 台股財報評分

每個交易日收盤後，自動從證交所與櫃買中心開放資料抓取全市場（上市＋上櫃）財報，依規則評分並給出買進／觀望／賣出標籤，在手機網頁上檢視。

- **全市場**：可搜尋、依標籤／產業篩選、依評分／市值／營收年增／本益比排序
- **各產業前三名**：可切換「依評分」或「依市值」

## 部署（沿用你原本選股工具的做法）

1. 建立新的 GitHub repo（可設為私有），把本資料夾內容全部上傳
2. Settings → Actions → General → Workflow permissions 選 **Read and write permissions**
3. Actions 頁籤 → 「更新財報評分」→ **Run workflow**，手動跑第一次
4. Settings → Pages → Source 選 `main` 分支、資料夾 `/docs`，網址就是手機要開的頁面
   （私有 repo 使用 Pages 需要付費方案；若是免費帳號，repo 需設為公開）

之後平日台灣時間 17:00 自動更新。

## 調整評分規則

全部在 `config/scoring.json`：

- 每個指標 `bad` 得 0 分、`good` 得 100 分，中間線性內插；`bad` 大於 `good` 代表越低越好
- `weight` 是權重；缺資料的指標會自動略過並重新配分
- `buy_above` / `sell_below`：買進與賣出的分數門檻
- `buy_min_coverage`：資料涵蓋度低於此值時，最高只給「觀望」
- 有「本期虧損」「本業虧損」「負債比過高」等風險旗標時，不會給買進

改完推上去，下一次排程（或手動 Run workflow）就會套用。

## 已知限制

- ROE 以累計淨利依季別年化估算，不是精確的 TTM 數字
- 營收年增使用月營收資料的「累計營收年增率」
- 金融保險業財報格式不同，目前只用估值與營收指標評分，標示「資料涵蓋度低」
- 各公司公布財報的時間不同，同一天資料的「資料期間」可能不一致（卡片內有顯示）
- 這是規則評分，不是股價預測，不構成投資建議

## 測試

```
python tests/test_pipeline.py   # 解析、評分、排名（模擬資料）
python tests/test_web.py        # 手機版網頁（需 playwright）
```

## 結構

```
src/fetch.py            抓開放資料 -> data/companies.json
src/score.py            評分、排名 -> docs/data.json
docs/index.html         手機網頁
config/scoring.json     評分規則
.github/workflows/      排程
```
