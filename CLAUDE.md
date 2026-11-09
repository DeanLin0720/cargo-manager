# CLAUDE.md — Cargo Manager

Rust cargo 的 PySide6 深色模式 GUI 管理工具。單一檔案 `cargo_manager.py`，作者 DeanLin。

## 溝通與輸出

- 一律使用**繁體中文**回覆；程式內的 UI 字串、註解也用繁體中文。
- 修改後只交付變更內容與簡短說明，不重述整個程式。
- 改完 UI 時，用 offscreen 截圖自行確認（見「測試」）。

## 執行環境

- 目標平台：**Windows**（開發者在 Windows 上執行與打包），程式也需能在 Linux 上跑以便測試。
- Python 3.9+（使用 `X | None` 型別標註需 3.10+，目前程式已使用，視為最低 3.10）、PySide6。
- 需要已安裝 Rust（rustup / cargo）。

## 固定慣例（使用者指定，不可更動）

1. **只有深色模式**：`apply_dark()` = Fusion 樣式 + QPalette + `DARK_QSS`。強調色為橘色 `#e8a15c` / `#d27a35` / `#b65f1f`。新元件一律沿用 QSS，不要加淺色主題。
2. **視窗標題只顯示 `APP_NAME`（"Cargo Manager"），不顯示版本**。版本 `APP_VERSION` 只出現在「關於」頁。
3. **不要在視窗內左上角放標題文字 / logo 標籤**。
4. **圖示**：只從「執行檔所在資料夾」的 `./logo/cargo_manager.ico`（其次 `.png`）讀取，由 `app_icon()` 處理；找不到就不顯示圖示。**不要**內建或繪製圖示、不要改用 `_MEIPASS`。`.ico` 由使用者自行提供（Rust 螃蟹圖）。
5. **設定存在程式同目錄的 `cargo_manager.ini`**（`QSettings` IniFormat，`app_dir()`），不用登錄檔。
6. **執行檔路徑副檔名一律小寫 `.exe`**：所有路徑經過 `norm_exe()`（`shutil.which` 在 Windows 會回傳 `.EXE`）。新增任何路徑來源時也要套用。
7. **PyInstaller 打包指令寫在檔案開頭 docstring**；使用 `-D`（資料夾模式），`logo` 資料夾在打包後手動複製到 exe 同層：
   ```
   pyinstaller -D -w --name cargo_manager --icon logo/cargo_manager.ico cargo_manager.py
   ```
8. **主分頁順序**：① `新增專案` ② `專案管理` ③ `關於`。分頁標籤**不加圖案**（含 emoji）。
   - 主分頁 `QTabWidget` 的 objectName 為 `main`；選中時橘色光條在**文字下方**（`border-bottom: 3px solid #e8a15c`）。
   - 分頁標籤選中時**文字顏色不變**，不要在 `QTabBar::tab:selected` 設 `color`。
   - 專案路徑列、專案資訊卡、子分頁（建置/執行、相依套件、工具鏈/安裝、設定、指令說明）**只在「專案管理」頁**。
   - 啟動時有上次專案 → 停在「專案管理」；否則停在「新增專案」。開啟 / 建立專案後自動切到「專案管理」（`open_project` 內 `setCurrentWidget(self.page_manage)`）。
9. 底部「輸出」區與自訂 cargo 指令列為所有主分頁共用（在 `QSplitter` 下半部）。

## 程式架構（cargo_manager.py）

| 區塊 | 內容 |
|---|---|
| `app_dir()` | exe（打包後）或 .py 所在資料夾 |
| `norm_exe()` / `find_tool()` | 路徑副檔名小寫化；找 cargo/rustup（PATH → `~/.cargo/bin`） |
| `app_icon()` | 讀 `./logo/cargo_manager.ico/.png` |
| `AnsiConverter` | cargo 彩色輸出（ANSI SGR）→ HTML span，跨行保留狀態 |
| `DARK_QSS` / `apply_dark()` | 深色主題 |
| `EN` / `t()` / `collect_i18n()` / `apply_i18n()` | 語系：以繁中原文為 key 的英文對照表；`collect_i18n` 掃描靜態文字，`MainWindow._retranslate()` 切換時重繪 |
| `AddDependencyDialog` | `cargo add` 對話框，`build_args()` 組參數（版本 / features / dev / build / optional / path / git） |
| `MainWindow._build_ui()` | 主版面：主分頁 + 輸出區 |
| `_tab_new / _tab_build / _tab_deps / _tab_toolchain / _tab_settings / _tab_help / _tab_about` | 各分頁 |
| `CARGO_HELP` | 指令說明內容（分類、子指令、用途、範例）；分類與用途的英文放在 `EN` |
| `_load_settings / _save_settings` | ini 讀寫（關窗時存） |
| `open_project / refresh_project / _on_metadata` | 用 `cargo metadata --no-deps --format-version 1` 讀專案；workspace 成員填入 `-p` 下拉 |
| `_fill_package_views` | 依選取套件填 `--bin` 清單與相依表 |
| `run_process` | **主程序**：單一 QProcess、合併輸出、顯示在輸出區、`on_done(code)` 回呼；同時只能跑一個 |
| `run_quiet` | **背景查詢**：獨立 QProcess、收集 stdout/stderr 後 `callback(code, out, err)`，不佔用主程序（metadata、search、版本偵測） |
| `run_cargo / run_rustup` | 包裝 `run_process`，自動帶入專案目錄 |
| `stop_process` | Windows 用 `taskkill /T /F` 連子程序一起結束 |
| `_common_opts` | 建置選項 → 參數（-p/--workspace、--release、features、target、-v、額外參數） |
| `run_action(key)` | 建置頁按鈕對應的 cargo 子指令 |
| `_run_queue` | 依序執行多個 cargo 指令（移除不同類型相依時用） |
| `split_args / quote_arg` | 類 shell 參數切割（不處理反斜線跳脫，以支援 Windows 路徑） |

### 撰寫新功能的原則

- 會佔用輸出區、需要使用者看到過程的 → `run_cargo` / `run_process`；只需要結果的查詢 → `run_quiet`。
- 執行期間需停用的按鈕用 `self._btn(...)` 建立（會加入 `busy_buttons`）。
- 環境變數：`_env()` 會設 `CARGO_TERM_COLOR=always`（主程序）/ `never`（quiet），並把 `~/.cargo/bin` 加入 PATH。
- 新增會持久化的選項時，同步更新 `_load_settings` / `_save_settings`。
- `run_quiet` 的 callback 需容忍 QProcess 已被銷毀（視窗關閉時），已用 `try/except RuntimeError` 保護。
- 語系：程式內字串仍寫繁體中文，並在 `EN` 加上英文對照（英文不可含 `&`，會被當成快捷鍵）。靜態元件文字建好後會自動納入切換；執行期才產生的文字用 `t("...{x}").format(x=...)`（不要用 f-string 當 key）；會停留在畫面上的動態文字要能在 `_retranslate()` 重繪（狀態列 `_set_status`、專案資訊 `_set_info`）。

## 功能清單（目前 v1.2.1）

- **語系**：主分頁列右上角切換繁體中文 / English，立即生效，存於 ini 的 `ui/language`（預設繁中）；已輸出的記錄與 cargo 輸出不翻譯。

- **新增專案**：`cargo new` / `cargo init`，bin/lib、edition 2015–2024、vcs。
- **專案管理**
  - 專案列：最近 15 個專案、瀏覽、重新整理、檔案總管、VS Code（`code` 指令）。
  - 建置/執行：run、build、check、test、bench、clippy、clippy --fix、fmt、fmt --check、doc --open、clean、update、tree、fix；選項 release / all-features / no-default-features / workspace / -v / features / target（可載入 rustup 已安裝 target）/ --bin / 額外參數 / 程式參數（`--` 後）/ 環境變數。
  - 相依套件：表格（名稱、版本需求、類型、features、optional、來源）、新增、移除、`update -p`、crates.io 搜尋（`cargo search`，雙擊加入）。
  - 工具鏈/安裝：rustup show/update/toolchain/target/component、default、override set/unset、target add/remove；`cargo install`（--locked/--force）/ uninstall / `install --path .`。
  - 設定：cargo / rustup 路徑（自動偵測、瀏覽、儲存並檢查）、清除最近專案、執行前清除輸出。
  - 指令說明：cargo 各指令的分類、用途與範例表格，可依關鍵字即時篩選。
- **關於**：圖示、名稱、版本、簡介、作者、Python/PySide6/cargo/rustc/rustup 版本、設定檔路徑、相關連結。
- 輸出區：ANSI 彩色、自動捲動、停止、清除；底部自訂 `cargo ...` 指令。

## 測試

在無顯示器環境用 offscreen 平台跑：

```bash
QT_QPA_PLATFORM=offscreen python3 test_script.py
```

測試腳本做法：`sys.path` 加入程式目錄後 `import cargo_manager as cm`，建立 `QApplication` → `cm.apply_dark(app)` → `cm.MainWindow()`；用 `QEventLoop + QTimer.singleShot` 等待，以 `w.is_running()` 與 `w.quiet_procs` 判斷是否閒置；可把 `QMessageBox.question` monkeypatch 成回傳 `QMessageBox.Yes`。截圖用 `w.grab().save("x.png")` 檢查畫面。

建議回歸流程：`cargo_new` 建專案 → 確認 `lbl_info` / `cb_bin` → `run_action("run")` → `cargo add` 後相依表有資料 → `remove_dependency` → `search_crates`。

測試前刪除 `cargo_manager.ini` 以免沿用舊設定；測試用的專案資料夾不要留在程式目錄。

## 版本

- 修改功能時同步更新 `APP_VERSION`（目前 `1.2.1`）與本檔「功能清單」。
