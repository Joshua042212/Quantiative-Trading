# 台股資料系統（Taiwan Stock Data Platform）

這個專案整合前端儀表板、後端 API 與台股資料爬蟲流程，用來管理 K 線、月營收、新聞與公司基本資料。

提供台股資料蒐集、清洗、儲存與視覺化的一站式平台。

內建每日排程更新，支援 K 線、月營收、財報、新聞與 ETF 溢折價。

## 功能清單

- 還原股價優先的 K 線查詢流程
- 月營收雙路徑抓取（最新月與歷史補抓）
- TTM 本益比計算與視覺化
- 夜間批次排程與異常記錄
- 前端儀表板整合多面板查詢

## 文件導覽

- 系統元件詳解：backend/COMPONENTS.txt
- 啟動腳本：start-dev.ps1、start.ps1
- 排程安裝：setup_scheduler.ps1

## 執行環境需求

請先在電腦安裝以下軟體：

- Python 3.10 以上（建議 3.11+）
- Node.js 20 以上（建議 LTS）
- npm（通常隨 Node.js 一起安裝）
- PostgreSQL 14 以上（本機或可連線的伺服器）
- Windows PowerShell 5.1 以上（用於 .ps1 腳本）

## Python 套件

後端套件清單在 requirements.txt，安裝方式如下：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

主要後端套件（節錄）：

- fastapi
- uvicorn
- SQLAlchemy
- psycopg2-binary
- python-dotenv
- pandas
- numpy
- yfinance
- finmind
- requests
- pandas-ta

## 前端套件

前端套件由 package.json 管理，安裝方式如下：

```powershell
npm install
```

主要前端套件（節錄）：

- react
- react-dom
- react-router-dom
- lightweight-charts
- vite
- typescript

## 環境變數設定

請先複製 .env.example 為 .env，再填入自己的資料：

```powershell
Copy-Item .env.example .env
```

.env 至少需要：

- DATABASE_URL：PostgreSQL 連線字串

可選設定：

- FINMIND_API_TOKEN：提高 FinMind API 使用穩定度與額度

範例：

```env
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/stock_db
FINMIND_API_TOKEN=your_token_here
```

## 啟動方式

### 一鍵啟動前後端

```powershell
npm run start
```

會執行 start.ps1，同時開啟：

- Backend API： http://localhost:8000
- Frontend： http://localhost:5173

### 開發模式啟動

```powershell
npm run dev:up
```

會執行 start-dev.ps1。

## Windows 每日排程安裝

以系統管理員身分開啟 PowerShell 後執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1
```

預設會建立工作排程：

- Task Name：StockDB_DailyUpdate
- Schedule：每日 20:00
- Command：backend/scheduled_tasks.py --backfill-days 5

## 第一次在新電腦部署建議順序

1. 安裝 Python、Node.js、PostgreSQL。
2. 建立資料庫（例如 stock_db）並確認可連線。
3. 設定 .env（至少 DATABASE_URL）。
4. 安裝 Python 套件：pip install -r requirements.txt。
5. 安裝前端套件：npm install。
6. 啟動：npm run start。

## GitHub 上傳前提醒

- 機密設定請放在 .env（不要提交到 GitHub）
- 專案請保留 .env.example 供他人快速設定
- 本機環境與建置產物已由 .gitignore 排除
