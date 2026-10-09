# 婚禮開場影片產生器 — 李政宏 & 羅奕晴

把婚紗照自動做成婚宴現場播放用的 1080p 開場影片：

1. **片頭**：深色底 + 金色光點，「WELCOME TO OUR WEDDING / 李政宏 & 羅奕晴 / 日期」依序淡入
2. **相片輪播**：每張緩慢推移縮放（Ken Burns），相片間交叉淡化；直式照片自動配模糊背景置中
3. **章節卡 / 字幕**（選用）：例如「相遇 2019」，或在指定相片下方加一句話
4. **片尾**：「感謝您的蒞臨 / 婚宴即將開始，敬請入座」

有放音樂時，影片長度會自動配合音樂長度（自動算每張相片秒數），並在結尾淡出。

## 使用步驟

```bash
# 需求：Python 3.9+、Pillow、ffmpeg、中文襯線字型（建議 Noto Serif CJK TC）
pip install pillow
# Ubuntu: sudo apt install ffmpeg fonts-noto-cjk   macOS: brew install ffmpeg

# 1. 把相片放進 photos/（可含子資料夾，依檔名排序）
# 2. 把配樂放進 music/，並在 config.json 填 "music": "music/xxx.mp3"
# 3. 修改 config.json（日期、字幕、章節等）
python3 make_video.py --preview      # 先出 960x540 預覽版 output/preview.mp4
python3 make_video.py --still 5      # 抽看第 5 秒畫面
python3 make_video.py                # 正式版 output/wedding_opening.mp4
```

## config.json 重點欄位

| 欄位 | 說明 |
|---|---|
| `date_text` | 片頭日期，例如 `"2026.11.21"`，空白則不顯示 |
| `max_photos` | 相片上限（預設 40，超過會平均抽樣）；開場片建議 30–50 張、2–4 分鐘 |
| `order` | `"name"` 依檔名、`"exif"` 依拍攝時間 |
| `hero_photo` | 片頭/片尾背景用的相片檔名（預設第一張） |
| `music` / `fit_to_music` | 配樂路徑；`true` 時依音樂長度自動調整每張秒數 |
| `photo_seconds` | 沒有音樂時每張相片秒數 |
| `captions` | `{"IMG_0012.jpg": "第一次約會・2019"}` |
| `chapters` | `[{"before": "IMG_0030.jpg", "title": "求婚", "subtitle": "2025.02.14"}]` |
| `font_regular` / `font_bold` | 自訂字型路徑（Windows 可指定 `C:/Windows/Fonts/...`） |

## 現場播放建議

- 正式版為 H.264 / AAC、CRF 16、`+faststart`，一般筆電、投影機、婚宴場地播放器皆可播
- 事先到場地用實際設備試播一次（確認 16:9、音量、黑邊）
- 隨身碟多備一份，並準備一個無聲版本以防音響問題

## 個資提醒

`photos/`、`music/`、`output/` 已列入 `.gitignore`，相片不會被推上 GitHub。
