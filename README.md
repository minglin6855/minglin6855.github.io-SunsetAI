# TWILIGHT RADAR — 手機優化無 JavaScript 版

這是 GitHub Pages 一頁式火燒雲預報：

- 手機優先 UI
- 北部／中部／南部／東部區域切換
- 攝影點與原版相同
- 區域切換只使用 HTML radio + CSS
- 前端完全不含 JavaScript
- Python + GitHub Actions 每小時更新
- ECMWF／GFS／ICON 三模式
- 火燒雲主指數使用三模式中位數
- 信心指數使用三模式分歧度

## 部署

把所有檔案上傳到 GitHub Pages repository，並在：

1. Settings → Pages：選擇 main / root
2. Settings → Actions → General：Workflow permissions 設為 Read and write
3. Actions → Update sunset forecast：手動執行一次

## 修改地點

編輯 `config.json` 的 `regions`。每個攝影點需包含：

```json
{
  "name": "淡水",
  "detail": "漁人碼頭／沙崙",
  "latitude": 25.18,
  "longitude": 121.41
}
```

## 相容性

區域切換使用 CSS `:has()`。現代 Chrome、Edge、Safari 與 Firefox 均支援。
舊瀏覽器若不支援，會自動顯示全部區域，資料仍可正常閱讀。


## v1.1 相容性修正

- 移除 `visibility`，避免部分模式不提供該欄位時整個多模式請求失敗。
- Open-Meteo HTTP 錯誤內容會直接顯示在 GitHub Actions log。
- 加強 ECMWF、GFS、ICON 回傳欄位名稱容錯。
- 火燒雲評分重新分配降水、濕度與風速權重。


## v1.2 修正

- 修正 Open-Meteo 多模式回傳時 `daily.sunset` 可能帶模型後綴的問題。
- 可辨識 `sunset`、`sunset_ecmwf_ifs025`、`sunset_gfs_global`、`sunset_icon_global` 等欄位。
- 優先採用 ECMWF 的日落時間；未提供時自動使用其他模式。
- GitHub Actions log 現在會顯示完整例外類型與可用欄位。
