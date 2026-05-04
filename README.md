## 台股資料系統（Taiwan Stock Data Platform）<br>
#這個專案整合了前端儀表板、後端 API 與台股資料爬蟲流程，用來管理 K 線、月營收、新聞與公司基本資料。
#提供台股資料蒐集、清洗、儲存與視覺化的一站式平台。
#內建每日排程更新，支援 K 線、月營收、財報、新聞與 ETF 溢折價。

功能清單：
還原股價優先的 K 線查詢流程<br>
月營收雙路徑抓取（最新月與歷史補抓）<br>
TTM 本益比計算與視覺化<br>
夜間批次排程與異常記錄<br>
前端儀表板整合多面板查詢<br>
文件導覽：<br>
系統元件詳解：COMPONENTS.txt<br>
啟動腳本：start-dev.ps1, start.ps1<br>
排程安裝：setup_scheduler.ps1<br>
