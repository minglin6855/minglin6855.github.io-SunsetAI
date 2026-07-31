# AI 火燒雲預測（無 JavaScript）

一頁式 GitHub Pages 專案。瀏覽器端只有 HTML + CSS，氣象資料由 GitHub Actions 定時呼叫 Open-Meteo，使用 ECMWF、GFS、ICON 三模式計算。

## 功能

- ECMWF IFS 0.25°、GFS Global、DWD ICON Global
- 三模式各自評分
- 主指數採三模式中位數
- 信心指數依高雲、中雲、低雲、降水與總分分歧計算
- 手機與桌機自適應
- 前端完全不使用 JavaScript
- 每小時由 GitHub Actions 自動更新

## 安裝

1. 把本專案所有檔案放進 GitHub Pages repository。
2. 修改 `config.json`：
   - `location_name`
   - `latitude`
   - `longitude`
3. 到 GitHub repository：
   - Settings → Pages
   - Source 選擇 `Deploy from a branch`
   - Branch 選擇 `main` / root
4. 到 Actions 頁面，手動執行一次 `Update sunset forecast`。
5. 確認 repository 的 Actions 具有寫入權限：
   - Settings → Actions → General
   - Workflow permissions → Read and write permissions

## 本機產生頁面

```bash
python -m pip install -r requirements.txt
python generate.py
```

產生後直接開啟 `index.html`。

## 調整地點

`config.json` 範例：

```json
{
  "location_name": "淡水",
  "latitude": 25.1676,
  "longitude": 121.4450,
  "timezone": "Asia/Taipei"
}
```

## 評分說明

這是可解釋的規則式模型，不是經過歷史照片訓練的機器學習模型。

主要評估：

- 高雲與中雲是否適合染色
- 低雲是否可能遮蔽西方地平線
- 日落時段降水
- 能見度、濕度與風速

主火燒雲指數取三模式中位數，避免單一模式的異常預報過度影響結果。

## 信心指數

信心指數不是火燒雲機率，而是三模式一致程度：

- 80–100：高信心
- 60–79：中信心
- 0–59：低信心

公式與 Nekolens 的概念相同，但其完整權重未公開，因此本專案數值不保證與該網站完全一致。

## 注意

Open-Meteo 的欄位或模型代號未來可能調整。`generate.py` 已針對多模型欄位名稱加入容錯；若 API 結構重大變更，需同步修改解析邏輯。
