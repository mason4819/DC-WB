# Welcome Bot（輕量版 ProBot 歡迎系統）

為 **512MB RAM** 主機設計的 Discord 歡迎機器人：歡迎訊息、離開訊息、自動身分組、Pillow 歡迎圖片。
只使用 Python + discord.py + SQLite + Pillow，沒有大型框架；唯一的定時動作是每 5 秒更換一次機器人狀態（可關閉）。

## 1. 功能介紹

- **歡迎系統**：指定頻道、自訂訊息、純文字 / Embed、可選歡迎圖片（頭像、名稱、伺服器名稱、成員數）
- **離開系統**：指定頻道、自訂訊息、純文字 / Embed
- **自動身分組**：每個伺服器獨立設定；權限不足時不會崩潰，並會在頻道提醒管理員
- **歡迎圖片**：1200×406，圓形頭像、深色圓角卡片；內建多種備援字型，特殊字元、花體字、Emoji 名稱都不會變成方塊
- **輪播狀態**：每 5 秒換一個狀態（含表情符號，也會顯示伺服器數與成員數），全部寫在 `cogs/status.py` 一個檔案裡
- **全中文介面**：所有 Slash 指令、選項與機器人回覆都是中文
- **訊息變數**：`{user}` `{username}` `{displayname}` `{server}` `{member_count}` `{user_id}`
- 所有設定存在 SQLite，重啟後保留；每個伺服器完全獨立

## 2. 系統需求

- Python **3.11 以上**
- 512MB RAM 即可（啟動後約 50–80MB，產生圖片時瞬間增加數 MB）
- 可連外網路（連線 Discord）

## 3. Python 安裝方式

**Windows**：到 <https://www.python.org/downloads/> 下載 3.11+，安裝時勾選 *Add python.exe to PATH*。

**Ubuntu / Debian VPS**：

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
python3 --version   # 需要 3.11 以上
```

## 4. 建立 Discord Bot

1. 開啟 <https://discord.com/developers/applications> → **New Application**
2. 左側 **Bot** → **Reset Token** → 複製 Token（只會顯示一次，不要外流）
3. 左側 **OAuth2 → URL Generator**：
   - Scopes：`bot`、`applications.commands`
   - Bot Permissions：View Channels、Send Messages、Embed Links、Attach Files、Manage Roles
4. 開啟產生的網址，把機器人邀請到你的伺服器

快速邀請連結（把 `YOUR_CLIENT_ID` 換成 Application ID）：

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot%20applications.commands&permissions=268487680
```

## 5. 必須開啟的 Intents

在 Developer Portal → **Bot → Privileged Gateway Intents**：

| Intent | 是否需要 |
| --- | --- |
| **SERVER MEMBERS INTENT** | ✅ **必須開啟**（偵測成員加入 / 離開） |
| PRESENCE INTENT | ❌ 不需要 |
| MESSAGE CONTENT INTENT | ❌ 不需要 |

沒開 Server Members Intent 時，程式會顯示明確錯誤並結束。

## 6. `.env` 設定

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

編輯 `.env`：

```
DISCORD_TOKEN=你的機器人Token
SYNC_COMMANDS=true
LOG_LEVEL=INFO
```

- `SYNC_COMMANDS`：第一次啟動需要 `true` 來註冊 Slash Commands；之後可改成 `false`。
- `DATA_DIR`（選填）：資料庫與自訂背景的資料夾，預設是專案內的 `data/`。
- `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`（選填）：兩個都填就改用 Turso 雲端資料庫保存設定，詳見第 14 節。
- `STATUS_ROTATION`（選填）：填 `false` 可關閉狀態輪播。
- `PORT`（選填）：有設定時才會啟動健康檢查網頁（Render 會自動提供）。

## 7. 安裝 requirements

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 8. 啟動方式

```bash
python bot.py
```

資料表會在第一次啟動時自動建立（`data/bot.db`），不需要手動初始化。

## 9. Slash Command 使用方式

所有指令都是中文，輸入 `/歡迎`、`/離開`、`/自動身分組`、`/設定` 後 Discord 會自動列出選項。訊息內容中可用 `
` 換行。

| 指令 | 說明 |
| --- | --- |
| `/歡迎 快速設定 頻道 [訊息] [樣式] [圖片]` | 一次完成：啟用 + 指定頻道（+ 訊息 / 樣式 / 圖片） |
| `/歡迎 啟用` · `/歡迎 停用` | 啟用 / 停用歡迎系統 |
| `/歡迎 頻道 頻道` | 更換歡迎頻道 |
| `/歡迎 訊息 [內容] [樣式]` | 設定訊息與樣式（嵌入式訊息 / 純文字）；不填則顯示目前設定 |
| `/歡迎 圖片 [啟用] [背景] [還原預設]` | 開關歡迎圖片、上傳自訂背景（PNG/JPG，5MB 內）、還原預設背景 |
| `/歡迎 測試` | 把測試歡迎訊息發送到歡迎頻道（以你自己當作新成員） |
| `/歡迎 預覽` | 只有你看得到的預覽 |
| `/離開 快速設定 頻道 [訊息] [樣式]` | 啟用離開系統並指定頻道 |
| `/離開 啟用` · `/離開 停用` · `/離開 頻道` · `/離開 訊息` · `/離開 測試` | 對應離開系統 |
| `/自動身分組 設定 身分組` | 設定新成員自動取得的身分組（並自動啟用） |
| `/自動身分組 移除` · `/自動身分組 啟用` · `/自動身分組 停用` | 移除 / 啟用 / 停用 |
| `/設定` | 顯示全部設定，並列出偵測到的權限問題 |

範例：

```
/歡迎 快速設定 頻道:#歡迎 訊息:歡迎 {user} 加入 {server}！你是第 {member_count} 位成員。 樣式:嵌入式訊息 (Embed) 圖片:True
```

### 歡迎圖片版面（預設橫幅）

- 尺寸 1200×406，與預設橫幅比例相同，不會變形
- 上方紅色橫條與楓葉不被遮擋；左側深色圓角卡片放圓形頭像、`WELCOME`、顯示名稱、`歡迎加入 伺服器名稱`、`第 N 位成員`
- 卡片停在 x=900，不會蓋住右下角的 Logo
- 預設背景：`assets/backgrounds/default.png`，可用 `/歡迎 圖片 背景:` 為各伺服器換成自己的圖

### 特殊字元與備援字型

Discord 名稱常含花體字、符號、Emoji 或其他語言。程式會：

1. 先把花體字轉成一般字母（例如 `𝓜𝓪𝓼𝓸𝓷` → `Mason`），並移除組合符號與零寬字元
2. 逐字挑選 `assets/fonts/` 內**真的有該字形**的字型來畫：繁中 → 簡中 → 日文 → 韓文 → 符號 → 特殊符號 → Emoji（單色）
3. 所有字型都沒有的字會直接略過，不會畫出方塊；整個名稱都畫不出來時，改用帳號名稱

想支援更多文字（阿拉伯文、泰文等）：把對應的 Noto Sans `.ttf` / `.otf` 放進 `assets/fonts/` 即可，程式會自動使用，不用改程式碼。字型只有用到時才會載入，不會增加平常的記憶體用量。

## 10. 權限設定

- 所有設定指令只有 **Administrator** 或 **Manage Server** 可使用；一般成員看不到，也會被程式再次擋下。
- 機器人需要：View Channels、Send Messages、Embed Links、Attach Files、Manage Roles。
- **自動身分組**：機器人的最高身分組必須**高於**要發放的身分組。

### 修改輪播狀態

打開 `cogs/status.py`，編輯最上方的 `STATUSES` 清單即可：

```python
(WATCHING, "👀 誰剛進來了？"),      # 顯示成「正在看 👀 誰剛進來了？」
(PLAYING,  "🏠 {servers} 個伺服器的大門"),  # {servers} 會自動換成伺服器數
```

- 類型有 `PLAYING`（正在玩）、`WATCHING`（正在看）、`LISTENING`（正在聽）
- 可用變數：`{servers}` 伺服器數、`{members}` 全部成員數
- 間隔由 `STATUS_INTERVAL_SECONDS` 控制。Discord 限制約 20 秒內 5 次狀態更新，**不要低於 5 秒**
- 機器人帳號無法使用「自訂狀態」的表情符號欄位，所以表情符號直接寫在文字裡

## 11. 常見問題

**Slash Commands 沒有出現？** 確認邀請時勾了 `applications.commands`、`SYNC_COMMANDS=true`，等 1–2 分鐘或重開 Discord。

**歡迎訊息沒有送出？** 執行 `/設定` 看「Problems found」，或 `/歡迎 測試` 會直接顯示原因。

**沒有 Embed Links / Attach Files 權限？** 自動改用純文字或略過圖片，不會崩潰。

**自動身分組失敗？** 多半是機器人身分組排在目標身分組下方，或缺少 Manage Roles。程式會在歡迎頻道（或系統頻道）提醒管理員，每 6 小時最多一次。

**名稱出現方塊或缺字？** 已內建 Noto Sans 繁中 / 簡中 / 日文 / 韓文 / 符號 / Emoji（SIL OFL 授權）。仍缺的文字種類，請把對應字型放進 `assets/fonts/`，詳見第 9 節。檔名含 `Bold` 的檔案會用於粗體。

**資料表** `database.init_db()` 每次啟動執行 `CREATE TABLE IF NOT EXISTS`：`guild_settings`、`welcome_settings`、`leave_settings`、`autorole_settings`。

## 12. 512MB RAM 部署建議

- 只開 `guilds` + `members` Intent；關閉訊息與成員快取，不下載成員清單
- 除了狀態輪播（每 5 秒送出一個很小的 Gateway 訊息）外，完全事件驅動；SQLite 即開即關
- 圖片一次只渲染一張，用完立即 `close()`
- 單一進程、venv、日誌只輸出 stdout、第一次成功後 `SYNC_COMMANDS=false`

## 13. 部署到 Render + UptimeRobot + GitHub

**流程**：程式碼放 GitHub → Render 自動部署 → UptimeRobot 定時 ping 保持喚醒。

1. **上傳到 GitHub**（`.env` 已被 `.gitignore` 排除）：
   ```bash
   git init && git add . && git commit -m "Welcome bot"
   git branch -M main
   git remote add origin https://github.com/你的帳號/WelcomeBot.git
   git push -u origin main
   ```
   建議使用 **Private** 儲存庫。
2. **Render**：Dashboard → **New → Blueprint** → 選該儲存庫（讀取 `render.yaml`）。
   或手動建立 **Web Service**：Build `pip install -r requirements.txt`、Start `python bot.py`、Plan Free。
   在 **Environment** 新增 `DISCORD_TOKEN`（必填）與 `PYTHON_VERSION=3.11.9`。
3. 部署完成後複製網址（例如 `https://welcome-bot-xxxx.onrender.com`），開啟 `/health` 應顯示 `OK`。
4. **UptimeRobot**：Add New Monitor → **HTTP(s)** → URL 填 `https://welcome-bot-xxxx.onrender.com/health` → 間隔 5 分鐘。
   Render 免費版閒置 15 分鐘會休眠，5 分鐘一次的 ping 可避免休眠。

> ⚠️ **Render 免費版的磁碟會在每次重新部署時清空**
> 只用本機 SQLite 時，`bot.db` 會跟著消失，所有伺服器要重新設定。解法是改用第 14 節的 **Turso**（免費），設定存在雲端，重新部署也不受影響。
> 另一個做法是付費方案 + Persistent Disk：在 `render.yaml` 取消註解 `disk` 與 `DATA_DIR=/var/data`。
> 注意：用 `/歡迎 圖片 背景:` 上傳的自訂背景圖仍存在本機磁碟，免費版重新部署後會消失並自動改回預設橫幅（預設橫幅放在程式碼裡，不受影響）。

免費方案每月 750 小時，剛好夠一個服務 24 小時運行；RAM 同為 512MB。

## 14. 使用 Turso 雲端資料庫（建議搭配 Render 免費版）

Turso 是雲端 SQLite，免費方案不需信用卡（目前為 100 個資料庫、5GB、每月 5 億次讀取、1000 萬次寫入，額度以官網為準）。
本專案只存各伺服器的設定，用量遠低於上限。

**設計**：機器人透過 Turso 的 HTTP 介面連線，不需要額外安裝套件。每個伺服器的設定讀取一次後就放在記憶體，成員加入 / 離開時不會連線資料庫；只有管理員改設定時才寫入。沒設定 Turso 變數時，自動使用本機 SQLite。

**取得連線資料**

1. 到 <https://turso.tech> 註冊（可用 GitHub 登入），建立一個資料庫，名稱隨意（例如 `welcomebot`）。
2. 在資料庫頁面複製 **URL**，格式像 `libsql://welcomebot-你的帳號.turso.io`。
3. 在資料庫清單點該資料庫的 **Get Token → Generate Token**，複製 Token。
   （改用命令列的話：`turso db show 資料庫名稱 --url` 與 `turso db tokens create 資料庫名稱`。）

**填到 Render → Environment**

| Key | Value |
| --- | --- |
| `TURSO_DATABASE_URL` | `libsql://welcomebot-你的帳號.turso.io` |
| `TURSO_AUTH_TOKEN` | 剛剛產生的 Token（需保密） |

存檔後會自動重新部署，Logs 看到 `Database ready (Turso)` 就代表成功；資料表會自動建立，不用手動操作。

**注意**
- Token 跟 Discord Token 一樣，不要放進 GitHub 或截圖。外流時到 Turso 重新產生並更新 Render 即可。
- Turso 暫時連不上時，指令會回覆「目前無法讀取或儲存設定」，已讀取過的設定仍可繼續發送歡迎訊息，機器人不會崩潰。
- 免費方案的額度與規則可能調整，請以 Turso 官網為準。

## 專案結構

```
WelcomeBot/
├── bot.py              # 入口、錯誤處理、Intents
├── config.py           # 路徑、限制、預設文字
├── database.py         # SQLite（4 張資料表）
├── keepalive.py        # Render / UptimeRobot 健康檢查
├── render.yaml         # Render Blueprint
├── requirements.txt
├── .env.example
├── cogs/               # welcome.py, leave.py, autorole.py, settings.py, status.py
├── utils/              # image.py, formatter.py, messaging.py
├── assets/             # backgrounds/default.png, fonts/（Noto Sans 多語系備援字型）
└── data/               # bot.db 與自訂背景（自動建立）
```
