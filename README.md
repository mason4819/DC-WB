# Welcome Bot（輕量版 ProBot 歡迎系統）

為 **512MB RAM** 主機設計的 Discord 歡迎機器人：歡迎訊息、離開訊息、自動身分組、Pillow 歡迎圖片。
只使用 Python + discord.py + SQLite + Pillow，沒有大型框架；唯一的定時動作是每 5 秒更換一次機器人狀態（可關閉）。

## 1. 功能介紹

- **歡迎系統**：指定頻道、自訂訊息、純文字 / Embed、可選歡迎圖片（頭像、名稱、伺服器名稱、成員數）
- **離開系統**：指定頻道、自訂訊息、純文字 / Embed
- **自動身分組**：每個伺服器獨立設定；權限不足時不會崩潰，並會在頻道提醒管理員
- **歡迎圖片**：1200×406，圓形頭像、深色圓角卡片；內建多種備援字型，特殊字元、花體字、Emoji 名稱都不會變成方塊
- **雙主機備援（選用）**：Render 與 Wispbyte 都跑機器人，任何一台掛掉（包含 Render）另一台自動接手，平常互相分擔畫圖
- **後臺管理**：機器人擁有者可在 Discord 內查看所有伺服器、選擇要離開哪一個、封鎖伺服器、查看主機狀態
- **輪播狀態**：每 5 秒換一個狀態（含表情符號，也會顯示伺服器數與成員數），全部寫在 `cogs/status.py` 一個檔案裡
- **指令管理名單**：可指定哪些成員或身分組能使用設定指令，其他人（包含只有「管理伺服器」的人）不能操作
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
- `PORT`（選填）：有設定時才會啟動網頁服務（`/health`、`/status`）。Render 會自動提供；Wispbyte 會讀 `SERVER_PORT`。
- `OWNER_IDS`（選填）：能使用 `/後臺` 的 Discord 使用者 ID，多個用逗號隔開。不填就用 Developer Portal 裡的應用程式擁有者。
- `ADMIN_GUILD_ID`（選填）：填一個伺服器 ID，`/後臺` 指令就只會出現在那個伺服器。
- `NODE_NAME`（雙主機備援用）：這台主機的名字，例如 `render`、`wispbyte`。
- `PEER_URL` / `IMAGE_WORKER_URL` / `IMAGE_WORKER_SECRET`（雙主機備援用）：詳見第 15 節。
- `KEEP_BUSY` / `KEEP_BUSY_PERCENT`（選填）：閒置時的低用量計算，預設開啟、約 1% 單核，詳見第 15 節。

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
| `/管理員 新增成員 成員` · `/管理員 新增身分組 身分組` | 把成員或身分組加入「管理名單」（僅擁有者與管理員可用） |
| `/管理員 移除成員` · `/管理員 移除身分組` · `/管理員 列表` · `/管理員 清空` | 移除 / 查看 / 清空管理名單 |

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

**誰可以使用設定指令（`/歡迎` `/離開` `/自動身分組` `/設定`）**

| 伺服器狀態 | 可以使用的人 |
| --- | --- |
| **沒有設定管理名單**（預設） | 擁有者、管理員（Administrator）、擁有「管理伺服器」權限的成員 |
| **已設定管理名單** | 擁有者、管理員、**名單內的成員**、**擁有名單內身分組的成員**。只有「管理伺服器」權限**不再通過** |

- 擁有者與管理員永遠可以使用，不會被名單鎖在外面。
- 編輯名單的 `/管理員` 指令**只有擁有者與管理員**可以用，名單內的人不能再加人，避免權限被轉手。
- 一般成員用到時會看到「你沒有權限使用這個指令」，不會改到任何設定。

**建議設定方式**

```
/管理員 新增身分組 身分組:@版主
/管理員 新增成員 成員:@某位幹部
/管理員 列表
```

- 名單每種（成員 / 身分組）最多 25 個；不能加入機器人、`@everyone` 或整合服務管理的身分組。
- 設定指令對所有人都看得到（才能讓名單內的人使用），但沒有權限的人使用時會被擋下。想連選單都隱藏，可以到 Discord 的「伺服器設定 → 整合 → 這個機器人」針對指令調整可見對象。
- 使用雙主機備援時，名單存在 Turso，兩台主機共用；沒有設定 Turso 時存在本機資料庫。

**機器人本身需要的權限**：View Channels、Send Messages、Embed Links、Attach Files、Manage Roles。
**自動身分組**：機器人的最高身分組必須**高於**要發放的身分組。

## 11. 常見問題

**Slash Commands 沒有出現？** 確認邀請時勾了 `applications.commands`、`SYNC_COMMANDS=true`，等 1–2 分鐘或重開 Discord。

**歡迎訊息沒有送出？** 執行 `/設定` 看「Problems found」，或 `/歡迎 測試` 會直接顯示原因。

**沒有 Embed Links / Attach Files 權限？** 自動改用純文字或略過圖片，不會崩潰。

**自動身分組失敗？** 多半是機器人身分組排在目標身分組下方，或缺少 Manage Roles。程式會在歡迎頻道（或系統頻道）提醒管理員，每 6 小時最多一次。

**名稱出現方塊或缺字？** 已內建 Noto Sans 繁中 / 簡中 / 日文 / 韓文 / 符號 / Emoji（SIL OFL 授權）。仍缺的文字種類，請把對應字型放進 `assets/fonts/`，詳見第 9 節。檔名含 `Bold` 的檔案會用於粗體。

**資料表** `database.init_db()` 每次啟動執行 `CREATE TABLE IF NOT EXISTS`：`guild_settings`、`welcome_settings`、`leave_settings`、`autorole_settings`、`access_list`（管理名單）、`node_lease`（雙主機備援）。

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

## 15. 雙主機備援：Render ⇄ Wispbyte（選用）

**目標**：兩台主機都跑完整的機器人。平常只有一台連 Discord（使用中），另一台待命；使用中的那台掛掉，不管是 Render 還是 Wispbyte，待命的那台會自動接手。

**怎麼運作**

- 兩台共用同一個 Turso 資料庫，裡面有一筆「租約」記錄現在誰是使用中。租約每 10 秒續約一次，45 秒沒續就視為失效。
- 使用中的主機如果連續 30 秒確認不了租約（例如連不上資料庫），會**自己先**斷開 Discord，確保另一台接手時不會同時有兩個機器人在回應，避免重複發歡迎訊息。我用模擬測試驗證過：主機掛掉、關機、資料庫斷線、程式崩潰、手動讓出時都沒有出現兩台同時在線的時刻。
- 正常關機或重新部署（Render 收到 SIGTERM）會立刻釋放租約，約 10 秒內換手；突然斷線約需 **45 到 60 秒**。這段空窗期加入或離開的成員不會收到訊息。
- 待命的主機仍然運作網頁服務：使用中的主機會把畫歡迎圖片的工作交給它（`/render`），它連不上就改在本機畫。
- 兩台每 4 分鐘互相 ping 對方的 `/health`。這會讓睡著的 Render 免費服務保持清醒（Render 只認外部 HTTP 請求）。**UptimeRobot 還是建議保留**，因為 Wispbyte 掛掉時需要有人叫醒 Render。
- 換手後，新的使用中主機會先清掉記憶體快取，避免用到過期的設定。

**需求**：必須使用 Turso（第 14 節），否則兩台無法共用設定與租約。**沒有設定 Turso 時絕對不要在兩台主機同時執行**，會重複回應。

**兩台主機都要上傳整個 WelcomeBot 資料夾並執行 `python bot.py`。**

1. 產生共用密鑰（只用在兩台主機互相呼叫畫圖）：`python -c "import secrets; print(secrets.token_hex(32))"`
2. **Render → Environment**：

| Key | Value |
| --- | --- |
| `DISCORD_TOKEN` | 機器人 Token |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | 與 Wispbyte 相同 |
| `NODE_NAME` | `render` |
| `PEER_URL` | Wispbyte 的網址 `http://IP:Port` |
| `IMAGE_WORKER_URL` | 同上（讓 Render 把畫圖交給 Wispbyte；Render 只有 0.1 CPU，建議填） |
| `IMAGE_WORKER_SECRET` | 步驟 1 的密鑰 |
| `OWNER_IDS` | 你的 Discord 使用者 ID |
| `PYTHON_VERSION` | `3.11.9` |

3. **Wispbyte**：啟動檔填 `bot.py`，套件用完整的 `requirements.txt`，環境變數：

| Key | Value |
| --- | --- |
| `DISCORD_TOKEN` | 同一個 Token |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | 與 Render 相同 |
| `NODE_NAME` | `wispbyte` |
| `PEER_URL` | Render 的網址 `https://xxx.onrender.com` |
| `IMAGE_WORKER_SECRET` | 同一組密鑰（讓它能替 Render 畫圖） |
| `SYNC_COMMANDS` | `false` |
| `OWNER_IDS` | 同上 |

   Wispbyte 比較有餘裕時，**不要**填 `IMAGE_WORKER_URL`，讓它自己畫；Wispbyte 的實際 Port 變數請依面板為準（程式會讀 `PORT`、`SERVER_PORT`、`WORKER_PORT`）。
4. **確認**：用瀏覽器開兩台的 `/status`，會顯示 `{"node": "...", "role": "leader"}` 或 `"standby"`，應該一台 leader、一台 standby。也可以在 Discord 用 `/後臺 狀態` 查看。

**測試切換**：在 Discord 執行 `/後臺 讓出主機`，使用中的主機會離線幾分鐘，另一台約 10 秒內接手。

**限制**

- 自訂背景圖存在各主機的本機磁碟，換手後新主機找不到時會改用預設橫幅。
- 沒有網頁服務的主機（面板沒給 Port）仍然可以參與備援，只是不能幫忙畫圖、也不能被對方 ping。
- 我無法確認 Wispbyte 面板的實際選項與網路設定，請以面板為準。

### 只當畫圖幫手（不備援）

如果只想讓 Wispbyte 幫忙畫圖、不跑機器人：在 Wispbyte 執行 `python worker.py`（套件用 `requirements-worker.txt`，只需 `IMAGE_WORKER_SECRET`），Render 設定 `IMAGE_WORKER_URL` 與 `IMAGE_WORKER_SECRET` 即可。連不上時自動改在本機畫。

### 閒置時的低用量計算

兩台主機在沒事做時，會在背景用約 1% 單核做質數搜尋這類沒有實際用途的計算（`busywork.py`）。它會在真正畫圖時自動暫停。

- 這**不能**阻止 Render 睡著，Render 只看外部 HTTP 請求，保持清醒靠的是 UptimeRobot 與對方主機的 ping。
- 它會用掉真實的 CPU。Render 只有 0.1 核，1% 單核約等於它配額的一成；如果覺得變慢，在 Render 設 `KEEP_BUSY=false`。
- 比例由 `KEEP_BUSY_PERCENT` 控制，上限固定 5%，也可以全部關閉。

## 16. 後臺管理（機器人擁有者專用）

在 Discord 內操作，不需要架網頁；全部回覆只有你自己看得到。能用的人是 `OWNER_IDS` 裡的使用者（沒填則是 Developer Portal 的應用程式擁有者），其他人會被擋下。建議再設定 `ADMIN_GUILD_ID`，讓這些指令只出現在你自己的伺服器。

| 指令 | 說明 |
| --- | --- |
| `/後臺 伺服器 [關鍵字]` | 列出機器人所在的所有伺服器（依人數排序、分頁）。從下拉選單選一個，看詳細資料，再選擇離開方式 |
| `/後臺 狀態` | 這台主機的角色、記憶體、延遲、資料庫、租約、對方主機是否連得到 |
| `/後臺 封鎖 伺服器id` | 封鎖：目前在裡面就離開，之後被邀請也會自動離開 |
| `/後臺 解除封鎖` · `/後臺 封鎖列表` | 解除 / 查看封鎖名單 |
| `/後臺 讓出主機 [分鐘]` | 暫時把 Discord 連線交給另一台主機（雙主機備援時） |
| `/後臺 同步指令` · `/後臺 清除快取` | 重新登錄斜線指令 / 清除記憶體快取 |

**選擇要離開哪個伺服器**：`/後臺 伺服器` → 下拉選單選伺服器 → 看到擁有者、人數、機器人權限、歡迎 / 離開 / 自動身分組設定 → 三種離開方式，每種都會再問一次確認：

- **離開伺服器**：只離開，設定保留，之後重新邀請可以繼續用。
- **離開並清除設定**：離開，並刪除該伺服器在資料庫裡的設定與自訂背景。
- **離開並封鎖**：離開、清除設定，並加入封鎖名單。

伺服器很多時，用關鍵字（名稱或 ID）篩選。面板 5 分鐘後會失效，重新輸入指令即可。

> 這是 Discord 內的管理介面。原本的需求有「不要 Web Dashboard」，所以沒有做網頁後臺；需要的話可以另外加。

## 專案結構

```
WelcomeBot/
├── bot.py              # 入口、錯誤處理、Intents
├── config.py           # 路徑、限制、預設文字
├── database.py         # SQLite（4 張資料表）
├── ha.py               # 雙主機備援（租約、自動切換、互相 ping）
├── webapp.py           # /health、/status、/render（供對方主機畫圖）
├── busywork.py         # 閒置時的低用量計算
├── worker.py           # 選用：只畫圖、不跑機器人的幫手
├── requirements-worker.txt
├── render.yaml         # Render Blueprint
├── requirements.txt
├── .env.example
├── cogs/               # welcome.py, leave.py, autorole.py, settings.py, access.py, backend.py, status.py
├── utils/              # image.py, formatter.py, messaging.py, access.py, remote.py
├── assets/             # backgrounds/default.png, fonts/（Noto Sans 多語系備援字型）
└── data/               # bot.db 與自訂背景（自動建立）
```
