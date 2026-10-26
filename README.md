# Welcome Bot（輕量版 ProBot 歡迎系統）

為 **512MB RAM** 主機設計的 Discord 歡迎機器人：歡迎訊息、離開訊息、自動身分組、Pillow 歡迎圖片。
只使用 Python + discord.py + SQLite + Pillow，沒有任何背景輪詢、快取或大型框架。

## 1. 功能介紹

- **歡迎系統**：指定頻道、自訂訊息、純文字 / Embed、可選歡迎圖片（頭像、名稱、伺服器名稱、成員數）
- **離開系統**：指定頻道、自訂訊息、純文字 / Embed
- **自動身分組**：每個伺服器獨立設定；權限不足時不會崩潰，並會在頻道提醒管理員
- **歡迎圖片**：1200×500，圓形頭像、深色圓角卡片
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

訊息中可用 `\n` 換行。

| 指令 | 說明 |
| --- | --- |
| `/welcome setup channel [message] [style] [image]` | 一次完成：啟用 + 指定頻道（+ 訊息 / 樣式 / 圖片） |
| `/welcome enable` · `/welcome disable` | 啟用 / 停用歡迎系統 |
| `/welcome channel channel` | 更換歡迎頻道 |
| `/welcome message [text] [style]` | 設定訊息與樣式（Plain text / Embed）；不帶參數則顯示目前設定 |
| `/welcome image [enabled] [background] [reset]` | 開關圖片、上傳背景（PNG/JPG，5MB 內）、`reset` 還原預設背景 |
| `/welcome test` | 把測試歡迎訊息送到歡迎頻道（以你自己當作新成員） |
| `/welcome preview` | 只有你看得到的預覽 |
| `/leave setup channel [message] [style]` | 啟用離開系統並指定頻道 |
| `/leave enable` · `/leave disable` · `/leave channel` · `/leave message` · `/leave test` | 對應離開系統 |
| `/autorole set role` | 設定新成員自動取得的身分組（並自動啟用） |
| `/autorole remove` · `/autorole enable` · `/autorole disable` | 移除 / 啟用 / 停用 |
| `/config` | 顯示全部設定，並列出偵測到的權限問題 |

範例：

```
/welcome setup channel:#welcome message:歡迎 {user} 加入 {server}！你是第 {member_count} 位成員。 style:Embed image:True
```

### 歡迎圖片版面（預設橫幅）

- 上方紅色橫條與楓葉不被遮擋；左側深色圓角卡片放圓形頭像、`WELCOME`、使用者名稱、`to 伺服器名稱`、`Member #成員數`
- 卡片停在 x=900，不會蓋住右下角的 Logo
- 背景完整延伸至圖片底部；左側卡片不會蓋住右下角的 Logo
- 預設背景：`assets/backgrounds/default.png`

## 10. 權限設定

- 所有設定指令只有 **Administrator** 或 **Manage Server** 可使用；一般成員看不到，也會被程式再次擋下。
- 機器人需要：View Channels、Send Messages、Embed Links、Attach Files、Manage Roles。
- **自動身分組**：機器人的最高身分組必須**高於**要發放的身分組。

## 11. 常見問題

**Slash Commands 沒有出現？** 確認邀請時勾了 `applications.commands`、`SYNC_COMMANDS=true`，等 1–2 分鐘或重開 Discord。

**歡迎訊息沒有送出？** 執行 `/config` 看「Problems found」，或 `/welcome test` 會直接顯示原因。

**沒有 Embed Links / Attach Files 權限？** 自動改用純文字或略過圖片，不會崩潰。

**自動身分組失敗？** 多半是機器人身分組排在目標身分組下方，或缺少 Manage Roles。程式會在歡迎頻道（或系統頻道）提醒管理員，每 6 小時最多一次。

**中文名稱變成方塊？** `assets/fonts/` 已附 Noto Sans TC（SIL OFL 授權）。換字型請放含中文字形的單一 `.ttf` / `.otf`，檔名含 `Bold` 的用於粗體。

**資料表** `database.init_db()` 每次啟動執行 `CREATE TABLE IF NOT EXISTS`：`guild_settings`、`welcome_settings`、`leave_settings`、`autorole_settings`。

## 12. 512MB RAM 部署建議

- 只開 `guilds` + `members` Intent；關閉訊息與成員快取，不下載成員清單
- 完全事件驅動，沒有定時任務或輪詢；SQLite 即開即關
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

> ⚠️ **免費版不會保存資料**
> Render 免費方案的檔案系統是暫時的。每次重新部署（包含推送 GitHub 觸發的自動部署）或平台重啟，`bot.db` 與上傳的自訂背景都會消失，所有伺服器要重新設定。
> - **付費方案 + Persistent Disk**（Starter 加 1GB 磁碟）：在 `render.yaml` 取消註解 `disk` 與 `DATA_DIR=/var/data`，資料就會永久保留。
> - 使用免費版：設定只需幾個指令就能重建（`/welcome setup ...`），並盡量避免頻繁推送程式碼。

免費方案每月 750 小時，剛好夠一個服務 24 小時運行；RAM 同為 512MB。

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
├── cogs/               # welcome.py, leave.py, autorole.py, settings.py
├── utils/              # image.py, formatter.py, messaging.py
├── assets/             # backgrounds/default.png, fonts/（Noto Sans TC）
└── data/               # bot.db 與自訂背景（自動建立）
```
