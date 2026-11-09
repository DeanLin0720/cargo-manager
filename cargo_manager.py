# -*- coding: utf-8 -*-
"""
Cargo Manager — Rust cargo 圖形化管理工具 (PySide6, 深色模式)

功能:
  * 開啟 / 最近專案、顯示 Cargo.toml 資訊 (支援 workspace 成員選擇)
  * build / run / check / test / bench / clippy / fmt / doc / clean / update / tree
    - release、features、all-features、no-default-features、target triple、額外參數、run 參數
  * 自訂 cargo 指令
  * 相依套件: 列表 (cargo metadata)、cargo add / remove / update -p、crates.io 搜尋 (cargo search)
  * 新專案: cargo new / cargo init (bin / lib, edition, vcs)
  * 工具鏈: rustup show / update / toolchain / target add、cargo install / uninstall
  * 輸出視窗支援 ANSI 色彩、可中止執行中的程序
  * 指令說明: 可搜尋的 cargo 指令用途與範例
  * 介面語系: 繁體中文 / English (主分頁右上角切換)
  * 設定存於程式同目錄的 cargo_manager.ini

需求: Python 3.9+、PySide6、已安裝 Rust (rustup / cargo)

圖示: 放在程式 (exe) 所在資料夾的 ./logo/cargo_manager.ico (或 .png)

PyInstaller 打包指令:
  pyinstaller -D -w --name cargo_manager --icon logo/cargo_manager.ico cargo_manager.py
  打包後把 logo 資料夾複製到 dist/cargo_manager/ (與 cargo_manager.exe 同層)
"""

import html
import json
import os
import re
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QUrl
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QPalette,
                           QTextCursor)
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

APP_NAME = "Cargo Manager"
APP_VERSION = "1.2.0"
IS_WIN = sys.platform.startswith("win")
EXE_SUFFIX = ".exe" if IS_WIN else ""


# ──────────────────────────────────────────────────────────── 路徑 / 設定
def app_dir() -> Path:
    """程式所在資料夾 (打包後為 exe 所在處)。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def norm_exe(path: str) -> str:
    """副檔名統一小寫 (shutil.which 在 Windows 會依 PATHEXT 回傳 .EXE)。"""
    path = (path or "").strip()
    root, ext = os.path.splitext(path)
    return root + ext.lower() if ext else path


def find_tool(name: str) -> str:
    """找 cargo / rustup: PATH → ~/.cargo/bin。"""
    found = shutil.which(name)
    if found:
        return norm_exe(str(Path(found)))
    home_bin = Path.home() / ".cargo" / "bin" / (name + EXE_SUFFIX)
    if home_bin.exists():
        return str(home_bin)
    return ""


# ──────────────────────────────────────────────────────────── 圖示
def app_icon() -> QIcon:
    """讀取程式 (exe) 所在資料夾的 ./logo/cargo_manager.ico 或 .png。"""
    for name in ("cargo_manager.ico", "cargo_manager.png"):
        f = app_dir() / "logo" / name
        if f.exists():
            return QIcon(str(f))
    return QIcon()


# ──────────────────────────────────────────────────────────── ANSI → HTML
ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
ANSI_OTHER_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

ANSI_COLORS = {
    30: "#5c6370", 31: "#ff6b6b", 32: "#7ee787", 33: "#e5c07b",
    34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#d7dae0",
    90: "#7f848e", 91: "#ff8787", 92: "#a5f3a0", 93: "#ffd580",
    94: "#82c4ff", 95: "#e09cf5", 96: "#7fdbe6", 97: "#ffffff",
}


class AnsiConverter:
    """把含 ANSI SGR 的單行文字轉成 HTML, 保留跨行狀態。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.fg = None
        self.bold = False

    def _span_open(self):
        style = []
        if self.fg:
            style.append(f"color:{self.fg}")
        if self.bold:
            style.append("font-weight:bold")
        return f'<span style="{";".join(style)}">' if style else ""

    def convert(self, line: str) -> str:
        line = ANSI_OTHER_RE.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", line)
        out = []
        pos = 0
        opened = self._span_open()
        out.append(opened)
        for m in ANSI_RE.finditer(line):
            out.append(html.escape(line[pos:m.start()]))
            pos = m.end()
            codes = [int(c) for c in m.group(1).split(";") if c.isdigit()] or [0]
            for c in codes:
                if c == 0:
                    self.reset()
                elif c == 1:
                    self.bold = True
                elif c == 22:
                    self.bold = False
                elif c == 39:
                    self.fg = None
                elif c in ANSI_COLORS:
                    self.fg = ANSI_COLORS[c]
            if opened:
                out.append("</span>")
            opened = self._span_open()
            out.append(opened)
        out.append(html.escape(line[pos:]))
        if opened:
            out.append("</span>")
        text = "".join(out)
        # 保留前導空白
        return text.replace("  ", "&nbsp;&nbsp;") or "&nbsp;"


# ──────────────────────────────────────────────────────────── 深色主題
DARK_QSS = """
* { font-size: 10pt; }
QMainWindow, QWidget { background: #1e1f22; color: #dcdfe4; }
QGroupBox {
    border: 1px solid #393b40; border-radius: 6px; margin-top: 12px; padding: 8px 6px 6px 6px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #e8a15c; }
QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
    background: #2b2d30; border: 1px solid #43454a; border-radius: 4px;
    padding: 4px; selection-background-color: #2f65ca;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #e8a15c; }
QComboBox QAbstractItemView { background: #2b2d30; selection-background-color: #2f65ca; }
QPushButton {
    background: #34363b; border: 1px solid #4a4c52; border-radius: 4px; padding: 6px 12px;
}
QPushButton:hover { background: #3f4147; border-color: #e8a15c; }
QPushButton:pressed { background: #2a2b2f; }
QPushButton:disabled { color: #6f737a; background: #2a2b2e; border-color: #36383c; }
QPushButton#primary { background: #b65f1f; border-color: #d27a35; color: white; font-weight: bold; }
QPushButton#primary:hover { background: #cc6d26; }
QPushButton#danger { background: #7a2b2b; border-color: #a33a3a; color: white; }
QPushButton#danger:hover { background: #943434; }
QTabWidget::pane { border: 1px solid #393b40; border-radius: 4px; top: -1px; }
QTabBar::tab {
    background: #2b2d30; border: 1px solid #393b40; padding: 6px 14px;
    border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px;
}
QTabBar::tab:selected { background: #1e1f22; color: #e8a15c; border-bottom-color: #1e1f22; }
QTabWidget#main > QTabBar::tab { padding: 8px 26px; font-size: 11pt; font-weight: bold; }
QTabWidget#main > QTabBar::tab:selected { border-bottom: 3px solid #e8a15c; }
QHeaderView::section {
    background: #2b2d30; color: #c9ccd1; border: none; border-right: 1px solid #393b40;
    border-bottom: 1px solid #393b40; padding: 4px;
}
QTableWidget { gridline-color: #393b40; alternate-background-color: #26282b; }
QPushButton#danger:disabled, QPushButton#primary:disabled {
    color: #6f737a; background: #2a2b2e; border-color: #36383c; font-weight: normal;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 14px; height: 14px; background: #2b2d30; border: 1px solid #6a6d73;
}
QCheckBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #e8a15c; }
QCheckBox::indicator:checked { background: #d27a35; border-color: #e8a15c; }
QRadioButton::indicator:checked {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                stop:0 #ffffff, stop:0.35 #ffffff, stop:0.45 #d27a35, stop:1 #d27a35);
    border-color: #e8a15c;
}
QScrollBar:vertical { background: #1e1f22; width: 12px; }
QScrollBar::handle:vertical { background: #4a4c52; border-radius: 5px; min-height: 24px; }
QScrollBar:horizontal { background: #1e1f22; height: 12px; }
QScrollBar::handle:horizontal { background: #4a4c52; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: #2b2d30; }
QLabel#muted { color: #8b8f96; }
QFrame#card { background: #25272a; border: 1px solid #393b40; border-radius: 6px; }
QToolTip { background: #2b2d30; color: #dcdfe4; border: 1px solid #4a4c52; }
QPushButton#lang_zh_TW, QPushButton#lang_en { padding: 3px 10px; }
QPushButton#lang_zh_TW { border-top-right-radius: 0; border-bottom-right-radius: 0; }
QPushButton#lang_en { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }
QPushButton#lang_zh_TW:checked, QPushButton#lang_en:checked {
    background: #b65f1f; border-color: #d27a35; color: white; font-weight: bold;
}
"""


def apply_dark(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    c = {
        QPalette.Window: "#1e1f22", QPalette.WindowText: "#dcdfe4",
        QPalette.Base: "#2b2d30", QPalette.AlternateBase: "#26282b",
        QPalette.ToolTipBase: "#2b2d30", QPalette.ToolTipText: "#dcdfe4",
        QPalette.Text: "#dcdfe4", QPalette.Button: "#34363b",
        QPalette.ButtonText: "#dcdfe4", QPalette.BrightText: "#ff6b6b",
        QPalette.Highlight: "#2f65ca", QPalette.HighlightedText: "#ffffff",
        QPalette.Link: "#e8a15c", QPalette.PlaceholderText: "#7a7e85",
    }
    for role, col in c.items():
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#6f737a"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6f737a"))
    app.setPalette(pal)
    app.setStyleSheet(DARK_QSS)


# ──────────────────────────────────────────────────────────── 語系
LANG_ZH = "zh_TW"
LANG_EN = "en"
_current_lang = LANG_ZH

# 以繁體中文原文為 key; {name} 為執行期代入的欄位
EN = {
    # 新增相依對話框
    "新增相依套件 (cargo add)": "Add Dependency (cargo add)",
    "例如 serde、tokio": "e.g. serde, tokio",
    "留空 = 最新版, 例如 1.0 或 ^0.12": "Leave empty for latest, e.g. 1.0 or ^0.12",
    "以逗號或空白分隔, 例如 derive, full": "Comma or space separated, e.g. derive, full",
    "一般 (dependencies)": "Normal (dependencies)",
    "開發 (--dev)": "Development (--dev)",
    "建置 (--build)": "Build (--build)",
    "不使用預設 features (--no-default-features)": "No default features (--no-default-features)",
    "本地路徑 (選填, --path)": "Local path (optional, --path)",
    "Git URL (選填, --git)": "Git URL (optional, --git)",
    "套件名稱": "Package name",
    "版本": "Version",
    "類型": "Type",
    "請輸入套件名稱或路徑。": "Please enter a package name or path.",
    # 主分頁 / 專案列
    "✨  新增專案": "✨  New Project",
    "📂  專案管理": "📂  Projects",
    "ℹ️  關於": "ℹ️  About",
    "專案:": "Project:",
    "選擇含 Cargo.toml 的資料夾": "Select a folder containing Cargo.toml",
    "瀏覽…": "Browse…",
    "重新整理": "Refresh",
    "檔案總管": "File Explorer",
    "尚未開啟專案": "No project open",
    "套件 (-p):": "Package (-p):",
    "建置 / 執行": "Build / Run",
    "相依套件": "Dependencies",
    "工具鏈 / 安裝": "Toolchain / Install",
    "設定": "Settings",
    # 輸出區
    "就緒": "Ready",
    "輸出": "Output",
    "自動捲動": "Auto-scroll",
    "■ 停止": "■ Stop",
    "清除": "Clear",
    "自訂指令, 例如: build --example demo   (Enter 執行)":
        "Custom command, e.g. build --example demo   (Enter to run)",
    "執行": "Run",
    # 建置 / 執行
    "指令": "Commands",
    "編譯並執行": "Compile and run",
    "編譯": "Compile",
    "快速型別檢查, 不產生執行檔": "Fast type check, no binary produced",
    "執行測試": "Run tests",
    "執行 benchmark": "Run benchmarks",
    "Lint 檢查": "Lint check",
    "自動修正 clippy 建議": "Automatically apply clippy suggestions",
    "格式化程式碼": "Format code",
    "檢查格式": "Check formatting",
    "產生並開啟文件": "Generate and open documentation",
    "清除 target": "Remove the target folder",
    "更新 Cargo.lock": "Update Cargo.lock",
    "顯示相依樹": "Show dependency tree",
    "套用編譯器建議修正": "Apply compiler-suggested fixes",
    "選項": "Options",
    "--workspace (全部成員)": "--workspace (all members)",
    "-v 詳細輸出": "-v verbose output",
    "模式": "Mode",
    "例如 serde,tracing": "e.g. serde,tracing",
    "預設 (host), 例如 x86_64-pc-windows-gnu": "Default (host), e.g. x86_64-pc-windows-gnu",
    "載入已安裝": "Load installed",
    "預設 (自動)": "Default (auto)",
    "其他 cargo 參數, 例如 --jobs 4 --locked": "Other cargo arguments, e.g. --jobs 4 --locked",
    "額外參數": "Extra args",
    "run / test 的程式參數 (放在 -- 之後)": "Program args for run / test (placed after --)",
    "程式參數": "Program args",
    "環境變數, 例如 RUST_LOG=debug RUST_BACKTRACE=1":
        "Environment variables, e.g. RUST_LOG=debug RUST_BACKTRACE=1",
    "環境變數": "Environment",
    # 相依套件
    "＋ 新增": "+ Add",
    "－ 移除選取": "- Remove selected",
    "更新選取 (update -p)": "Update selected (update -p)",
    "重新讀取": "Reload",
    "名稱": "Name",
    "版本需求": "Version req",
    "來源": "Source",
    "搜尋 crates.io (cargo search)": "Search crates.io (cargo search)",
    "關鍵字, 例如 http client": "Keywords, e.g. http client",
    "搜尋": "Search",
    "最新版本": "Latest version",
    "說明": "Description",
    "雙擊加入專案": "Double-click to add to the project",
    "一般": "Normal",
    "開發": "Dev",
    "建置": "Build",
    # 新增專案
    "建立新專案": "Create New Project",
    "專案要建立在哪個資料夾": "Folder to create the project in",
    "上層資料夾": "Parent folder",
    "專案名稱": "Project name",
    "例如 hello_world": "e.g. hello_world",
    "執行檔 (--bin)": "Binary (--bin)",
    "函式庫 (--lib)": "Library (--lib)",
    "版本控制": "Version control",
    "cargo new (建立新資料夾)": "cargo new (create a new folder)",
    "cargo init (在現有資料夾初始化)": "cargo init (initialize an existing folder)",
    "cargo new 會在「上層資料夾/專案名稱」建立專案；cargo init 則直接在「上層資料夾」初始化 "
    "(專案名稱可留空, 預設用資料夾名稱)。完成後會自動開啟該專案。":
        "cargo new creates the project in \"Parent folder/Project name\"; cargo init initializes "
        "the \"Parent folder\" itself (the project name is optional and defaults to the folder "
        "name). The project opens automatically when finished.",
    # 工具鏈 / 安裝
    "安裝 toolchain": "Install toolchain",
    "設為預設": "Set as default",
    "此專案 override set": "Override set (this project)",
    "此專案 override unset": "Override unset (this project)",
    "例如 wasm32-unknown-unknown": "e.g. wasm32-unknown-unknown",
    "cargo install (全域工具)": "cargo install (global tools)",
    "已安裝清單": "Installed list",
    "例如 cargo-edit, ripgrep, cargo-watch": "e.g. cargo-edit, ripgrep, cargo-watch",
    "版本:": "Version:",
    "選填": "Optional",
    "安裝": "Install",
    "解除安裝": "Uninstall",
    "從目前專案安裝 (install --path .)": "Install from current project (install --path .)",
    # 關於
    "版本 {v}": "Version {v}",
    "Rust cargo 圖形化管理工具 — 建立專案、建置執行、管理相依套件與工具鏈。":
        "A graphical manager for Rust cargo: create projects, build and run, "
        "and manage dependencies and toolchains.",
    "環境資訊": "Environment",
    "作者": "Author",
    "偵測中…": "Detecting…",
    "未偵測到": "Not found",
    "設定檔": "Config file",
    "相關連結": "Links",
    "Rust 官方網站": "Rust website",
    "rustup 安裝": "Install rustup",
    # 設定
    "工具路徑": "Tool Paths",
    "自動偵測": "Auto-detect",
    "儲存並檢查": "Save and Check",
    "清除最近專案": "Clear recent projects",
    "每次執行指令前清除輸出": "Clear output before each command",
    "設定檔: {path}": "Config file: {path}",
    # 指令說明
    "指令說明": "Command Guide",
    "搜尋指令或用途, 例如 build、相依": "Search commands or purposes, e.g. build, dependency",
    "分類": "Category",
    "子指令": "Subcommand",
    "用途": "Purpose",
    "範例": "Example",
    "建置與執行": "Build and Run",
    "測試與品質": "Test and Quality",
    "文件": "Documentation",
    "建立專案": "Create Project",
    "安裝工具": "Install Tools",
    "發布": "Publishing",
    "資訊查詢": "Information",
    "編譯目前套件及其相依": "Compile the current package and its dependencies",
    "編譯並執行二進位檔或範例": "Compile and run a binary or example",
    "檢查程式是否有錯誤, 不產生執行檔, 比 build 快": "Check for errors without producing a binary; faster than build",
    "刪除 target 資料夾內的建置產物": "Delete build artifacts in the target folder",
    "編譯套件, 並把額外選項傳給 rustc": "Compile a package and pass extra options to rustc",
    "執行單元測試、整合測試與文件測試": "Run unit, integration, and documentation tests",
    "執行效能測試 (benchmark)": "Run benchmarks",
    "檢查常見錯誤與可改進的寫法": "Catch common mistakes and suggest improvements",
    "自動套用 clippy 的修正建議": "Automatically apply clippy's suggested fixes",
    "依 rustfmt 規則格式化程式碼": "Format code with rustfmt",
    "只檢查格式, 不修改檔案 (常用於 CI)": "Check formatting without changing files (common in CI)",
    "自動套用編譯器的修正建議, 也用於升級 edition": "Apply compiler-suggested fixes; also used to migrate editions",
    "產生套件文件, 可用 --open 開啟": "Build package documentation; --open opens it",
    "產生文件, 並把自訂選項傳給 rustdoc": "Build documentation and pass custom options to rustdoc",
    "在新資料夾建立 Cargo 專案": "Create a Cargo project in a new folder",
    "在現有資料夾初始化 Cargo 專案": "Initialize a Cargo project in an existing folder",
    "加入相依套件到 Cargo.toml": "Add dependencies to Cargo.toml",
    "從 Cargo.toml 移除相依套件": "Remove dependencies from Cargo.toml",
    "依版本需求更新 Cargo.lock 內的相依版本": "Update dependency versions in Cargo.lock within the version requirements",
    "以樹狀圖顯示相依關係": "Show the dependency graph as a tree",
    "在 crates.io 搜尋套件": "Search for packages on crates.io",
    "顯示套件的版本、features 等資訊": "Show a package's versions, features, and other details",
    "預先下載相依套件, 之後可離線建置": "Download dependencies ahead of time for offline builds",
    "把所有相依套件複製到專案內的資料夾": "Copy all dependencies into a folder in the project",
    "產生 Cargo.lock, 不進行建置": "Generate Cargo.lock without building",
    "編譯並安裝 Rust 執行檔到 ~/.cargo/bin": "Compile and install a Rust binary into ~/.cargo/bin",
    "移除以 cargo install 安裝的執行檔": "Remove a binary installed with cargo install",
    "登入套件庫並儲存 API token": "Log in to a registry and save the API token",
    "從本機移除套件庫的 API token": "Remove the registry API token from this machine",
    "把套件打包成可發布的 .crate 檔": "Assemble the package into a distributable .crate file",
    "把套件上傳到 crates.io": "Upload the package to crates.io",
    "管理 crate 在套件庫上的擁有者": "Manage a crate's owners on the registry",
    "撤回已發布的版本, 新專案將不再選用": "Withdraw a published version so new projects no longer select it",
    "以 JSON 輸出專案與相依的中繼資料": "Output project and dependency metadata as JSON",
    "顯示套件的完整識別名稱": "Print a package's fully qualified specification",
    "以 JSON 顯示 Cargo.toml 的位置": "Show the location of Cargo.toml as JSON",
    "顯示建置報告, 例如未來不相容的警告": "Show build reports, such as future incompatibilities",
    "顯示 cargo 版本": "Show the cargo version",
    "顯示指定指令的完整說明": "Show full help for a command",
    # 執行期訊息
    "選擇 {name}": "Select {name}",
    "⚠ 找不到 cargo，請至「設定」指定路徑，或安裝 Rust: https://rustup.rs":
        "⚠ cargo not found. Set its path in Settings, or install Rust: https://rustup.rs",
    "仍有指令在執行，確定要結束？": "A command is still running. Quit anyway?",
    "選擇 Cargo 專案資料夾": "Select Cargo Project Folder",
    "資料夾內沒有 Cargo.toml:\n{path}": "No Cargo.toml in this folder:\n{path}",
    "讀取中…": "Loading…",
    "讀取 Cargo.toml 失敗": "Failed to read Cargo.toml",
    "解析 metadata 失敗: {ex}": "Failed to parse metadata: {ex}",
    "(預設)": "(default)",
    "{n} 個相依": "{n} dependencies",
    "{n} 個成員": "{n} members",
    "找不到 VS Code 的 code 指令 (請確認已加入 PATH)。":
        "VS Code's code command was not found (make sure it is on PATH).",
    "已有指令在執行中，請等待完成或按「停止」。":
        "A command is already running. Wait for it to finish or press Stop.",
    "找不到執行檔，請至「設定」指定 cargo / rustup 路徑。":
        "Executable not found. Set the cargo / rustup path in Settings.",
    "✖ 無法啟動程序: {err}": "✖ Failed to start process: {err}",
    "■ 程序已中止": "■ Process terminated",
    "✔ 完成 (exit 0)": "✔ Done (exit 0)",
    "✖ 失敗 (exit {code})": "✖ Failed (exit {code})",
    "執行中: {text}": "Running: {text}",
    "無法啟動: {err}": "Failed to start: {err}",
    "請先開啟含 Cargo.toml 的專案資料夾。": "Please open a project folder containing Cargo.toml first.",
    "請先輸入名稱。": "Please enter a name first.",
    "確定要清除 target 資料夾？": "Remove the target folder?",
    "找不到 rustup。": "rustup not found.",
    "已載入 {n} 個已安裝 target": "Loaded {n} installed target(s)",
    "請先開啟專案並輸入 toolchain。": "Please open a project and enter a toolchain first.",
    "請先開啟專案。": "Please open a project first.",
    "請先在表格中選取要移除的套件。": "Please select the packages to remove in the table first.",
    "確定移除: {names} ?": "Remove: {names} ?",
    "請先在表格中選取套件 (未選取請用「建置」頁的 update)。":
        "Please select packages in the table first (to update everything, use update on the Build / Run tab).",
    "搜尋 crates.io: {q} …": "Searching crates.io: {q} …",
    "搜尋失敗:": "Search failed:",
    "找不到符合的 crate": "No matching crates found",
    "選擇資料夾": "Select Folder",
    "請選擇有效的上層資料夾。": "Please select a valid parent folder.",
    "請輸入專案名稱。": "Please enter a project name.",
    "資料夾已存在:\n{target}\n請改用 cargo init。": "Folder already exists:\n{target}\nUse cargo init instead.",
    "請選擇有效的資料夾。": "Please select a valid folder.",
    "此資料夾已經有 Cargo.toml。": "This folder already has a Cargo.toml.",
    "請輸入 crate 名稱。": "Please enter a crate name.",
    "確定解除安裝 {name} ?": "Uninstall {name} ?",
}


# 指令說明內容: (分類, [(子指令, 用途, 範例), ...]); 分類與用途的英文放在 EN
CARGO_HELP = [
    ("建置與執行", [
        ("build (b)", "編譯目前套件及其相依", "cargo build --release"),
        ("run (r)", "編譯並執行二進位檔或範例", "cargo run --example demo"),
        ("check (c)", "檢查程式是否有錯誤, 不產生執行檔, 比 build 快", "cargo check --all-targets"),
        ("clean", "刪除 target 資料夾內的建置產物", "cargo clean --release"),
        ("rustc", "編譯套件, 並把額外選項傳給 rustc", "cargo rustc --release -- -C target-cpu=native"),
    ]),
    ("測試與品質", [
        ("test (t)", "執行單元測試、整合測試與文件測試", "cargo test my_test -- --nocapture"),
        ("bench", "執行效能測試 (benchmark)", "cargo bench"),
        ("clippy", "檢查常見錯誤與可改進的寫法", "cargo clippy --all-targets"),
        ("clippy --fix", "自動套用 clippy 的修正建議", "cargo clippy --fix --allow-dirty"),
        ("fmt", "依 rustfmt 規則格式化程式碼", "cargo fmt --all"),
        ("fmt --check", "只檢查格式, 不修改檔案 (常用於 CI)", "cargo fmt --check"),
        ("fix", "自動套用編譯器的修正建議, 也用於升級 edition", "cargo fix --edition"),
    ]),
    ("文件", [
        ("doc (d)", "產生套件文件, 可用 --open 開啟", "cargo doc --no-deps --open"),
        ("rustdoc", "產生文件, 並把自訂選項傳給 rustdoc", "cargo rustdoc -- --document-private-items"),
    ]),
    ("建立專案", [
        ("new", "在新資料夾建立 Cargo 專案", "cargo new hello_world --bin"),
        ("init", "在現有資料夾初始化 Cargo 專案", "cargo init --lib"),
    ]),
    ("相依套件", [
        ("add", "加入相依套件到 Cargo.toml", "cargo add serde --features derive"),
        ("remove (rm)", "從 Cargo.toml 移除相依套件", "cargo remove serde"),
        ("update", "依版本需求更新 Cargo.lock 內的相依版本", "cargo update -p serde"),
        ("tree", "以樹狀圖顯示相依關係", "cargo tree --depth 1"),
        ("search", "在 crates.io 搜尋套件", "cargo search http client"),
        ("info", "顯示套件的版本、features 等資訊", "cargo info serde"),
        ("fetch", "預先下載相依套件, 之後可離線建置", "cargo fetch --locked"),
        ("vendor", "把所有相依套件複製到專案內的資料夾", "cargo vendor"),
        ("generate-lockfile", "產生 Cargo.lock, 不進行建置", "cargo generate-lockfile"),
    ]),
    ("安裝工具", [
        ("install", "編譯並安裝 Rust 執行檔到 ~/.cargo/bin", "cargo install ripgrep --locked"),
        ("uninstall", "移除以 cargo install 安裝的執行檔", "cargo uninstall ripgrep"),
    ]),
    ("發布", [
        ("login", "登入套件庫並儲存 API token", "cargo login"),
        ("logout", "從本機移除套件庫的 API token", "cargo logout"),
        ("package", "把套件打包成可發布的 .crate 檔", "cargo package --list"),
        ("publish", "把套件上傳到 crates.io", "cargo publish --dry-run"),
        ("owner", "管理 crate 在套件庫上的擁有者", "cargo owner --list my_crate"),
        ("yank", "撤回已發布的版本, 新專案將不再選用", "cargo yank my_crate@1.0.0"),
    ]),
    ("資訊查詢", [
        ("metadata", "以 JSON 輸出專案與相依的中繼資料", "cargo metadata --no-deps --format-version 1"),
        ("pkgid", "顯示套件的完整識別名稱", "cargo pkgid"),
        ("locate-project", "以 JSON 顯示 Cargo.toml 的位置", "cargo locate-project"),
        ("report", "顯示建置報告, 例如未來不相容的警告", "cargo report future-incompatibilities"),
        ("version", "顯示 cargo 版本", "cargo version --verbose"),
        ("help", "顯示指定指令的完整說明", "cargo help build"),
    ]),
]


def current_language() -> str:
    return _current_lang


def set_language(lang: str):
    global _current_lang
    _current_lang = lang if lang in (LANG_ZH, LANG_EN) else LANG_ZH


def t(text: str) -> str:
    """依目前語系回傳字串; 對照表沒有的字串原樣回傳。"""
    return EN.get(text, text) if _current_lang == LANG_EN else text


def collect_i18n(root: QWidget) -> list:
    """掃描 root 底下已建立的靜態文字, 回傳 (setter, 中文原文) 清單 (只收錄對照表內的字串)。"""
    items = []

    def add(setter, text):
        if text in EN:
            items.append((setter, text))

    for w in root.findChildren(QWidget):
        add(w.setToolTip, w.toolTip())
        if isinstance(w, (QLabel, QAbstractButton)):
            add(w.setText, w.text())
        elif isinstance(w, QGroupBox):
            add(w.setTitle, w.title())
        elif isinstance(w, QLineEdit):
            add(w.setPlaceholderText, w.placeholderText())
        elif isinstance(w, QComboBox):
            for i in range(w.count()):
                add(partial(w.setItemText, i), w.itemText(i))
        elif isinstance(w, QTabWidget):
            for i in range(w.count()):
                add(partial(w.setTabText, i), w.tabText(i))
        elif isinstance(w, QTableWidget):
            for i in range(w.columnCount()):
                header = w.horizontalHeaderItem(i)
                if header:
                    add(header.setText, header.text())
    return items


def apply_i18n(items: list):
    for setter, text in items:
        setter(t(text))


# ──────────────────────────────────────────────────────────── 新增相依對話框
class AddDependencyDialog(QDialog):
    def __init__(self, parent=None, name="", version=""):
        super().__init__(parent)
        self.setWindowTitle(t("新增相依套件 (cargo add)"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.ed_name = QLineEdit(name)
        self.ed_name.setPlaceholderText("例如 serde、tokio")
        self.ed_version = QLineEdit(version)
        self.ed_version.setPlaceholderText("留空 = 最新版, 例如 1.0 或 ^0.12")
        self.ed_features = QLineEdit()
        self.ed_features.setPlaceholderText("以逗號或空白分隔, 例如 derive, full")
        self.cb_kind = QComboBox()
        self.cb_kind.addItems(["一般 (dependencies)", "開發 (--dev)", "建置 (--build)"])
        self.chk_optional = QCheckBox("optional (--optional)")
        self.chk_no_default = QCheckBox("不使用預設 features (--no-default-features)")
        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText("本地路徑 (選填, --path)")
        self.ed_git = QLineEdit()
        self.ed_git.setPlaceholderText("Git URL (選填, --git)")
        form.addRow("套件名稱", self.ed_name)
        form.addRow("版本", self.ed_version)
        form.addRow("Features", self.ed_features)
        form.addRow("類型", self.cb_kind)
        form.addRow("", self.chk_optional)
        form.addRow("", self.chk_no_default)
        form.addRow("Path", self.ed_path)
        form.addRow("Git", self.ed_git)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        apply_i18n(collect_i18n(self))

    def _accept(self):
        if not self.ed_name.text().strip() and not self.ed_path.text().strip():
            QMessageBox.warning(self, APP_NAME, t("請輸入套件名稱或路徑。"))
            return
        self.accept()

    def build_args(self) -> list:
        args = ["add"]
        name = self.ed_name.text().strip()
        ver = self.ed_version.text().strip()
        path = self.ed_path.text().strip()
        git = self.ed_git.text().strip()
        if path:
            args += ["--path", path]
            if name:  # workspace 路徑內有多個 crate 時指定名稱
                args.append(name)
        elif git:
            args += ["--git", git]
            if name:
                args.append(name)
        else:
            args.append(f"{name}@{ver}" if ver else name)
        kind = self.cb_kind.currentIndex()
        if kind == 1:
            args.append("--dev")
        elif kind == 2:
            args.append("--build")
        feats = [f for f in re.split(r"[,\s]+", self.ed_features.text().strip()) if f]
        if feats:
            args += ["--features", ",".join(feats)]
        if self.chk_optional.isChecked():
            args.append("--optional")
        if self.chk_no_default.isChecked():
            args.append("--no-default-features")
        return args


# ──────────────────────────────────────────────────────────── 主視窗
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())

        self.settings = QSettings(str(app_dir() / "cargo_manager.ini"), QSettings.IniFormat)
        self.proc: QProcess | None = None
        self.quiet_procs = set()
        self.ansi = AnsiConverter()
        self._partial = ""
        self.metadata = None
        self.busy_buttons = []
        self._status = ("就緒", {})  # 目前狀態列的 (中文範本, 代入值), 切換語系時重繪
        self._info = ("尚未開啟專案", {})  # 同上; None 表示由 self.metadata 繪製

        self._build_ui()
        self._i18n = collect_i18n(self)
        self._load_settings()
        self._check_tools()

    # ───────────────────────── UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)

        # ── 專案管理頁 (專案列 + 資訊卡 + 子分頁)
        manage = QWidget()
        mroot = QVBoxLayout(manage)
        mroot.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        top.addWidget(QLabel("專案:"))
        self.cb_project = QComboBox()
        self.cb_project.setEditable(True)
        self.cb_project.setMinimumWidth(420)
        self.cb_project.setInsertPolicy(QComboBox.NoInsert)
        self.cb_project.lineEdit().setPlaceholderText("選擇含 Cargo.toml 的資料夾")
        self.cb_project.activated.connect(lambda _: self.open_project(self.cb_project.currentText()))
        self.cb_project.lineEdit().returnPressed.connect(lambda: self.open_project(self.cb_project.currentText()))
        top.addWidget(self.cb_project, 1)
        b = QPushButton("瀏覽…")
        b.clicked.connect(self.browse_project)
        top.addWidget(b)
        b = QPushButton("重新整理")
        b.clicked.connect(self.refresh_project)
        top.addWidget(b)
        b = QPushButton("檔案總管")
        b.clicked.connect(self.open_in_explorer)
        top.addWidget(b)
        b = QPushButton("VS Code")
        b.clicked.connect(self.open_in_vscode)
        top.addWidget(b)
        mroot.addLayout(top)

        # 專案資訊卡
        card = QFrame()
        card.setObjectName("card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 6, 10, 6)
        self.lbl_info = QLabel()
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self.lbl_info, 1)
        cl.addWidget(QLabel("套件 (-p):"))
        self.cb_package = QComboBox()
        self.cb_package.setMinimumWidth(180)
        self.cb_package.currentIndexChanged.connect(self._fill_package_views)
        cl.addWidget(self.cb_package)
        mroot.addWidget(card)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_build(), "建置 / 執行")
        self.tabs.addTab(self._tab_deps(), "相依套件")
        self.tabs.addTab(self._tab_toolchain(), "工具鏈 / 安裝")
        self.tabs.addTab(self._tab_settings(), "設定")
        self.tabs.addTab(self._tab_help(), "指令說明")
        mroot.addWidget(self.tabs, 1)

        # ── 主分頁: 專案管理 / 新專案
        self.main_tabs = QTabWidget()
        self.main_tabs.setObjectName("main")
        self.page_new = self._tab_new()
        self.page_manage = manage
        self.main_tabs.addTab(self.page_new, "✨  新增專案")
        self.main_tabs.addTab(self.page_manage, "📂  專案管理")
        self.main_tabs.addTab(self._tab_about(), "ℹ️  關於")
        self.main_tabs.setCornerWidget(self._lang_switch(), Qt.TopRightCorner)

        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter, 1)
        splitter.addWidget(self.main_tabs)

        # 輸出區
        out_box = QWidget()
        ol = QVBoxLayout(out_box)
        ol.setContentsMargins(0, 6, 0, 0)
        oh = QHBoxLayout()
        self.lbl_status = QLabel()
        self.lbl_status.setObjectName("muted")
        oh.addWidget(QLabel("輸出"))
        oh.addSpacing(10)
        oh.addWidget(self.lbl_status, 1)
        self.chk_autoscroll = QCheckBox("自動捲動")
        self.chk_autoscroll.setChecked(True)
        oh.addWidget(self.chk_autoscroll)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_process)
        oh.addWidget(self.btn_stop)
        b = QPushButton("清除")
        b.clicked.connect(lambda: self.console.clear())
        oh.addWidget(b)
        ol.addLayout(oh)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(30000)
        self.console.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Cascadia Mono" if IS_WIN else "Monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.console.setFont(mono)
        self.console.setStyleSheet("QPlainTextEdit{background:#16171a;}")
        ol.addWidget(self.console, 1)

        # 自訂指令
        ch = QHBoxLayout()
        ch.addWidget(QLabel("cargo"))
        self.ed_custom = QLineEdit()
        self.ed_custom.setPlaceholderText("自訂指令, 例如: build --example demo   (Enter 執行)")
        self.ed_custom.returnPressed.connect(self.run_custom)
        ch.addWidget(self.ed_custom, 1)
        b = QPushButton("執行")
        b.clicked.connect(self.run_custom)
        self.busy_buttons.append(b)
        ch.addWidget(b)
        ol.addLayout(ch)

        splitter.addWidget(out_box)
        splitter.setSizes([520, 320])
        self.resize(1150, 860)

    def _lang_switch(self):
        """右上角的語系切換鈕 (文字固定, 不隨語系變動)。"""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 2, 4)
        lay.setSpacing(0)
        group = QButtonGroup(box)
        self.lang_buttons = {}
        for code, label in ((LANG_ZH, "中文"), (LANG_EN, "EN")):
            b = QPushButton(label)
            b.setObjectName(f"lang_{code}")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=code: self._switch_language(c))
            group.addButton(b)
            lay.addWidget(b)
            self.lang_buttons[code] = b
        return box

    def _switch_language(self, lang: str):
        if lang == current_language():
            return
        set_language(lang)
        self._retranslate()
        self._refresh_about()

    def _retranslate(self):
        """把目前語系套用到整個主視窗 (靜態文字 + 依狀態產生的文字)。"""
        apply_i18n(self._i18n)
        self.lang_buttons[current_language()].setChecked(True)
        self.lbl_about_ver.setText(t("版本 {v}").format(v=APP_VERSION))
        self.lbl_cfg_path.setText(t("設定檔: {path}").format(path=app_dir() / "cargo_manager.ini"))
        self._fill_help()
        for lb, text, url in self._about_links:
            lb.setText(f'<a style="color:#e8a15c;text-decoration:none" href="{url}">{t(text)}</a>'
                       f'&nbsp;&nbsp;<span style="color:#7a7e85">{url}</span>')
        self._set_status(self._status[0], **self._status[1])
        if self._info:
            self._set_info(self._info[0], **self._info[1])
        else:
            self._show_project_info()
        if self.cb_package.count():
            self.cb_package.setItemText(0, t("(預設)"))
        for r in range(self.tbl_deps.rowCount()):
            kind = self.tbl_deps.item(r, 0).data(Qt.UserRole)[2]
            self.tbl_deps.item(r, 2).setText(self._dep_kind_label(kind))

    def _set_status(self, template: str, **values):
        self._status = (template, values)
        self.lbl_status.setText(t(template).format(**values))

    def _set_info(self, template: str, **values):
        self._info = (template, values)
        self.lbl_info.setText(t(template).format(**values))

    def _btn(self, text, slot, primary=False, tip=""):
        b = QPushButton(text)
        if primary:
            b.setObjectName("primary")
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(slot)
        self.busy_buttons.append(b)
        return b

    def _tab_build(self):
        w = QWidget()
        lay = QHBoxLayout(w)

        # 指令按鈕
        g = QGroupBox("指令")
        gl = QGridLayout(g)
        cmds = [
            ("▶ run", "run", True, "編譯並執行"),
            ("build", "build", True, "編譯"),
            ("check", "check", False, "快速型別檢查, 不產生執行檔"),
            ("test", "test", False, "執行測試"),
            ("bench", "bench", False, "執行 benchmark"),
            ("clippy", "clippy", False, "Lint 檢查"),
            ("clippy --fix", "clippy-fix", False, "自動修正 clippy 建議"),
            ("fmt", "fmt", False, "格式化程式碼"),
            ("fmt --check", "fmt-check", False, "檢查格式"),
            ("doc --open", "doc", False, "產生並開啟文件"),
            ("clean", "clean", False, "清除 target"),
            ("update", "update", False, "更新 Cargo.lock"),
            ("tree", "tree", False, "顯示相依樹"),
            ("fix", "fix", False, "套用編譯器建議修正"),
        ]
        for i, (text, key, prim, tip) in enumerate(cmds):
            gl.addWidget(self._btn(text, lambda _=False, k=key: self.run_action(k), prim, tip), i // 2, i % 2)
        lay.addWidget(g)

        # 選項
        o = QGroupBox("選項")
        ol = QFormLayout(o)
        self.chk_release = QCheckBox("--release")
        self.chk_all_features = QCheckBox("--all-features")
        self.chk_no_default = QCheckBox("--no-default-features")
        self.chk_workspace = QCheckBox("--workspace (全部成員)")
        self.chk_verbose = QCheckBox("-v 詳細輸出")
        row = QHBoxLayout()
        for c in (self.chk_release, self.chk_all_features, self.chk_no_default):
            row.addWidget(c)
        row.addStretch()
        ol.addRow("模式", row)
        row2 = QHBoxLayout()
        row2.addWidget(self.chk_workspace)
        row2.addWidget(self.chk_verbose)
        row2.addStretch()
        ol.addRow("", row2)
        self.ed_features = QLineEdit()
        self.ed_features.setPlaceholderText("例如 serde,tracing")
        ol.addRow("Features", self.ed_features)
        self.cb_target = QComboBox()
        self.cb_target.setEditable(True)
        self.cb_target.addItem("")
        self.cb_target.lineEdit().setPlaceholderText("預設 (host), 例如 x86_64-pc-windows-gnu")
        trow = QHBoxLayout()
        trow.addWidget(self.cb_target, 1)
        b = QPushButton("載入已安裝")
        b.clicked.connect(self.load_targets)
        trow.addWidget(b)
        ol.addRow("Target", trow)
        self.cb_bin = QComboBox()
        self.cb_bin.setEditable(True)
        self.cb_bin.lineEdit().setPlaceholderText("預設 (自動)")
        ol.addRow("--bin", self.cb_bin)
        self.ed_extra = QLineEdit()
        self.ed_extra.setPlaceholderText("其他 cargo 參數, 例如 --jobs 4 --locked")
        ol.addRow("額外參數", self.ed_extra)
        self.ed_run_args = QLineEdit()
        self.ed_run_args.setPlaceholderText("run / test 的程式參數 (放在 -- 之後)")
        ol.addRow("程式參數", self.ed_run_args)
        self.ed_env = QLineEdit()
        self.ed_env.setPlaceholderText("環境變數, 例如 RUST_LOG=debug RUST_BACKTRACE=1")
        ol.addRow("環境變數", self.ed_env)
        lay.addWidget(o, 1)
        return w

    def _tab_deps(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(self._btn("＋ 新增", self.add_dependency, True))
        bar.addWidget(self._btn("－ 移除選取", self.remove_dependency))
        bar.addWidget(self._btn("更新選取 (update -p)", self.update_dependency))
        bar.addWidget(self._btn("重新讀取", self.refresh_project))
        bar.addStretch()
        lay.addLayout(bar)

        self.tbl_deps = QTableWidget(0, 6)
        self.tbl_deps.setHorizontalHeaderLabels(["名稱", "版本需求", "類型", "Features", "Optional", "來源"])
        self.tbl_deps.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_deps.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_deps.setAlternatingRowColors(True)
        self.tbl_deps.verticalHeader().setVisible(False)
        hh = self.tbl_deps.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        lay.addWidget(self.tbl_deps, 1)

        # 搜尋
        sg = QGroupBox("搜尋 crates.io (cargo search)")
        sl = QVBoxLayout(sg)
        sr = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("關鍵字, 例如 http client")
        self.ed_search.returnPressed.connect(self.search_crates)
        sr.addWidget(self.ed_search, 1)
        b = QPushButton("搜尋")
        b.clicked.connect(self.search_crates)
        sr.addWidget(b)
        sl.addLayout(sr)
        self.tbl_search = QTableWidget(0, 3)
        self.tbl_search.setHorizontalHeaderLabels(["名稱", "最新版本", "說明"])
        self.tbl_search.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_search.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_search.verticalHeader().setVisible(False)
        self.tbl_search.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tbl_search.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_search.doubleClicked.connect(self.add_from_search)
        self.tbl_search.setToolTip("雙擊加入專案")
        self.tbl_search.setMaximumHeight(200)
        sl.addWidget(self.tbl_search)
        lay.addWidget(sg)
        return w

    def _tab_new(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("建立新專案")
        f = QFormLayout(g)
        prow = QHBoxLayout()
        self.ed_new_parent = QLineEdit()
        self.ed_new_parent.setPlaceholderText("專案要建立在哪個資料夾")
        prow.addWidget(self.ed_new_parent, 1)
        b = QPushButton("瀏覽…")
        b.clicked.connect(self._browse_new_parent)
        prow.addWidget(b)
        f.addRow("上層資料夾", prow)
        self.ed_new_name = QLineEdit()
        self.ed_new_name.setPlaceholderText("例如 hello_world")
        f.addRow("專案名稱", self.ed_new_name)
        trow = QHBoxLayout()
        self.rb_bin = QRadioButton("執行檔 (--bin)")
        self.rb_lib = QRadioButton("函式庫 (--lib)")
        self.rb_bin.setChecked(True)
        trow.addWidget(self.rb_bin)
        trow.addWidget(self.rb_lib)
        trow.addStretch()
        f.addRow("類型", trow)
        self.cb_edition = QComboBox()
        self.cb_edition.addItems(["2024", "2021", "2018", "2015"])
        f.addRow("Edition", self.cb_edition)
        self.cb_vcs = QComboBox()
        self.cb_vcs.addItems(["git", "none", "hg", "pijul", "fossil"])
        f.addRow("版本控制", self.cb_vcs)
        brow = QHBoxLayout()
        brow.addWidget(self._btn("cargo new (建立新資料夾)", self.cargo_new, True))
        brow.addWidget(self._btn("cargo init (在現有資料夾初始化)", self.cargo_init))
        brow.addStretch()
        f.addRow("", brow)
        lay.addWidget(g)
        hint = QLabel("cargo new 會在「上層資料夾/專案名稱」建立專案；cargo init 則直接在「上層資料夾」初始化 "
                      "(專案名稱可留空, 預設用資料夾名稱)。完成後會自動開啟該專案。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()
        return w

    def _tab_help(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.ed_help_filter = QLineEdit()
        self.ed_help_filter.setPlaceholderText("搜尋指令或用途, 例如 build、相依")
        self.ed_help_filter.setClearButtonEnabled(True)
        self.ed_help_filter.textChanged.connect(self._filter_help)
        lay.addWidget(self.ed_help_filter)
        self.tbl_help = QTableWidget(0, 4)
        self.tbl_help.setHorizontalHeaderLabels(["分類", "子指令", "用途", "範例"])
        self.tbl_help.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_help.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_help.setAlternatingRowColors(True)
        self.tbl_help.verticalHeader().setVisible(False)
        hh = self.tbl_help.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.tbl_help, 1)
        return w

    def _fill_help(self):
        rows = [(cat, *cmd) for cat, cmds in CARGO_HELP for cmd in cmds]
        self.tbl_help.setRowCount(len(rows))
        for r, (cat, cmd, purpose, example) in enumerate(rows):
            texts = (t(cat), cmd, t(purpose), example)
            for c, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if c == 1:
                    item.setForeground(QColor("#e8a15c"))
                self.tbl_help.setItem(r, c, item)
        self._filter_help()

    def _filter_help(self, *_):
        q = self.ed_help_filter.text().strip().lower()
        for r in range(self.tbl_help.rowCount()):
            text = " ".join(self.tbl_help.item(r, c).text() for c in range(4)).lower()
            self.tbl_help.setRowHidden(r, q not in text)

    def _tab_toolchain(self):
        w = QWidget()
        lay = QHBoxLayout(w)

        g = QGroupBox("rustup")
        gl = QGridLayout(g)
        gl.addWidget(self._btn("rustup show", lambda: self.run_rustup(["show"])), 0, 0)
        gl.addWidget(self._btn("rustup update", lambda: self.run_rustup(["update"]), True), 0, 1)
        gl.addWidget(self._btn("toolchain list", lambda: self.run_rustup(["toolchain", "list"])), 1, 0)
        gl.addWidget(self._btn("target list (installed)",
                               lambda: self.run_rustup(["target", "list", "--installed"])), 1, 1)
        gl.addWidget(self._btn("component list (installed)",
                               lambda: self.run_rustup(["component", "list", "--installed"])), 2, 0)
        gl.addWidget(self._btn("rustc --version", self.show_versions), 2, 1)
        gl.addWidget(QLabel("Toolchain:"), 3, 0)
        self.ed_toolchain = QLineEdit()
        self.ed_toolchain.setPlaceholderText("stable / nightly / 1.80.0")
        gl.addWidget(self.ed_toolchain, 3, 1)
        gl.addWidget(self._btn("安裝 toolchain", lambda: self._rustup_with(self.ed_toolchain, ["toolchain", "install"])), 4, 0)
        gl.addWidget(self._btn("設為預設", lambda: self._rustup_with(self.ed_toolchain, ["default"])), 4, 1)
        gl.addWidget(self._btn("此專案 override set", self.override_set), 5, 0)
        gl.addWidget(self._btn("此專案 override unset", self.override_unset), 5, 1)
        gl.addWidget(QLabel("Target:"), 6, 0)
        self.ed_add_target = QLineEdit()
        self.ed_add_target.setPlaceholderText("例如 wasm32-unknown-unknown")
        gl.addWidget(self.ed_add_target, 6, 1)
        gl.addWidget(self._btn("target add", lambda: self._rustup_with(self.ed_add_target, ["target", "add"])), 7, 0)
        gl.addWidget(self._btn("target remove", lambda: self._rustup_with(self.ed_add_target, ["target", "remove"])), 7, 1)
        gl.setRowStretch(8, 1)
        lay.addWidget(g, 1)

        g2 = QGroupBox("cargo install (全域工具)")
        g2l = QGridLayout(g2)
        g2l.addWidget(self._btn("已安裝清單", lambda: self.run_cargo(["install", "--list"], need_project=False)), 0, 0, 1, 2)
        g2l.addWidget(QLabel("Crate:"), 1, 0)
        self.ed_install = QLineEdit()
        self.ed_install.setPlaceholderText("例如 cargo-edit, ripgrep, cargo-watch")
        g2l.addWidget(self.ed_install, 1, 1)
        g2l.addWidget(QLabel("版本:"), 2, 0)
        self.ed_install_ver = QLineEdit()
        self.ed_install_ver.setPlaceholderText("選填")
        g2l.addWidget(self.ed_install_ver, 2, 1)
        self.chk_install_locked = QCheckBox("--locked")
        self.chk_install_locked.setChecked(True)
        self.chk_install_force = QCheckBox("--force")
        r = QHBoxLayout()
        r.addWidget(self.chk_install_locked)
        r.addWidget(self.chk_install_force)
        r.addStretch()
        g2l.addLayout(r, 3, 1)
        g2l.addWidget(self._btn("安裝", self.cargo_install, True), 4, 0)
        g2l.addWidget(self._btn("解除安裝", self.cargo_uninstall), 4, 1)
        g2l.addWidget(self._btn("從目前專案安裝 (install --path .)",
                                lambda: self.run_cargo(["install", "--path", "."])), 5, 0, 1, 2)
        g2l.setRowStretch(6, 1)
        lay.addWidget(g2, 1)
        return w

    def _tab_about(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(30, 24, 30, 24)

        head = QHBoxLayout()
        logo = QLabel()
        ic = app_icon()
        if not ic.isNull():
            logo.setPixmap(ic.pixmap(96, 96))
            logo.setFixedSize(104, 104)
            head.addWidget(logo, 0, Qt.AlignTop)
            head.addSpacing(16)
        tv = QVBoxLayout()
        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 22pt; font-weight: bold; color: #e8a15c;")
        tv.addWidget(name)
        self.lbl_about_ver = QLabel()
        self.lbl_about_ver.setStyleSheet("font-size: 11pt;")
        tv.addWidget(self.lbl_about_ver)
        desc = QLabel("Rust cargo 圖形化管理工具 — 建立專案、建置執行、管理相依套件與工具鏈。")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        tv.addWidget(desc)
        tv.addStretch()
        head.addLayout(tv, 1)
        outer.addLayout(head)
        outer.addSpacing(10)

        g = QGroupBox("環境資訊")
        f = QFormLayout(g)
        f.setHorizontalSpacing(20)
        from PySide6 import __version__ as pyside_ver
        f.addRow("作者", QLabel("DeanLin"))
        f.addRow("Python", QLabel(sys.version.split()[0]))
        f.addRow("PySide6", QLabel(pyside_ver))
        self.lbl_about_cargo = QLabel()
        self.lbl_about_rustc = QLabel()
        self.lbl_about_rustup = QLabel()
        for lb in (self.lbl_about_cargo, self.lbl_about_rustc, self.lbl_about_rustup):
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow("cargo", self.lbl_about_cargo)
        f.addRow("rustc", self.lbl_about_rustc)
        f.addRow("rustup", self.lbl_about_rustup)
        cfg = QLabel(str(app_dir() / "cargo_manager.ini"))
        cfg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addRow("設定檔", cfg)
        outer.addWidget(g)

        g2 = QGroupBox("相關連結")
        l2 = QVBoxLayout(g2)
        links = [
            ("Rust 官方網站", "https://www.rust-lang.org/"),
            ("The Cargo Book", "https://doc.rust-lang.org/cargo/"),
            ("crates.io", "https://crates.io/"),
            ("docs.rs", "https://docs.rs/"),
            ("rustup 安裝", "https://rustup.rs/"),
        ]
        self._about_links = []
        for text, url in links:
            lb = QLabel()
            lb.setOpenExternalLinks(True)
            l2.addWidget(lb)
            self._about_links.append((lb, text, url))
        outer.addWidget(g2)
        outer.addStretch()
        return w

    def _refresh_about(self):
        """更新「關於」頁的 cargo / rustc / rustup 版本。"""
        about_labels = (self.lbl_about_cargo, self.lbl_about_rustc, self.lbl_about_rustup)
        for label in about_labels:
            if not label.text():
                label.setText(t("偵測中…"))

        def setter(label):
            def cb(code, out, err):
                text = (out.strip() or err.strip()).splitlines()
                label.setText(text[0] if code == 0 and text else t("未偵測到"))
            return cb
        cargo = self.cargo_path()
        rustup = self.rustup_path()
        rustc = find_tool("rustc")
        if not rustc and cargo:
            cand = Path(cargo).with_name("rustc" + EXE_SUFFIX)
            rustc = str(cand) if cand.exists() else ""
        for prog, label in ((cargo, self.lbl_about_cargo), (rustc, self.lbl_about_rustc),
                            (rustup, self.lbl_about_rustup)):
            if prog:
                self.run_quiet(prog, ["--version"], None, setter(label))
            else:
                label.setText(t("未偵測到"))

    def _tab_settings(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("工具路徑")
        f = QFormLayout(g)
        r1 = QHBoxLayout()
        self.ed_cargo_path = QLineEdit()
        r1.addWidget(self.ed_cargo_path, 1)
        b = QPushButton("瀏覽…")
        b.clicked.connect(lambda: self._browse_exe(self.ed_cargo_path, "cargo"))
        r1.addWidget(b)
        f.addRow("cargo", r1)
        r2 = QHBoxLayout()
        self.ed_rustup_path = QLineEdit()
        r2.addWidget(self.ed_rustup_path, 1)
        b = QPushButton("瀏覽…")
        b.clicked.connect(lambda: self._browse_exe(self.ed_rustup_path, "rustup"))
        r2.addWidget(b)
        f.addRow("rustup", r2)
        r3 = QHBoxLayout()
        b = QPushButton("自動偵測")
        b.clicked.connect(self._auto_detect)
        r3.addWidget(b)
        b = QPushButton("儲存並檢查")
        b.setObjectName("primary")
        b.clicked.connect(lambda: (self._save_settings(), self._check_tools()))
        r3.addWidget(b)
        b = QPushButton("清除最近專案")
        b.clicked.connect(self._clear_recent)
        r3.addWidget(b)
        r3.addStretch()
        f.addRow("", r3)
        lay.addWidget(g)
        self.chk_clear_on_run = QCheckBox("每次執行指令前清除輸出")
        lay.addWidget(self.chk_clear_on_run)
        self.lbl_cfg_path = QLabel()
        self.lbl_cfg_path.setObjectName("muted")
        self.lbl_cfg_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_cfg_path)
        lay.addStretch()
        return w

    # ───────────────────────── 設定
    def _load_settings(self):
        s = self.settings
        set_language(s.value("ui/language", LANG_ZH))
        self._retranslate()
        self.ed_cargo_path.setText(norm_exe(s.value("tools/cargo", "")) or find_tool("cargo"))
        self.ed_rustup_path.setText(norm_exe(s.value("tools/rustup", "")) or find_tool("rustup"))
        recent = s.value("recent", []) or []
        if isinstance(recent, str):
            recent = [recent]
        self.cb_project.addItems([r for r in recent if r])
        self.chk_release.setChecked(s.value("opt/release", False, type=bool))
        self.chk_verbose.setChecked(s.value("opt/verbose", False, type=bool))
        self.ed_extra.setText(s.value("opt/extra", ""))
        self.ed_env.setText(s.value("opt/env", ""))
        self.ed_new_parent.setText(s.value("new/parent", ""))
        self.cb_edition.setCurrentText(s.value("new/edition", "2024"))
        self.chk_clear_on_run.setChecked(s.value("opt/clear_on_run", False, type=bool))
        geo = s.value("window/geometry")
        if geo:
            self.restoreGeometry(geo)
        last = s.value("last_project", "")
        if last and (Path(last) / "Cargo.toml").exists():
            self.open_project(last, quiet=True)

    def _save_settings(self):
        s = self.settings
        self.ed_cargo_path.setText(norm_exe(self.ed_cargo_path.text()))
        self.ed_rustup_path.setText(norm_exe(self.ed_rustup_path.text()))
        s.setValue("tools/cargo", self.ed_cargo_path.text())
        s.setValue("tools/rustup", self.ed_rustup_path.text())
        s.setValue("recent", [self.cb_project.itemText(i) for i in range(self.cb_project.count())])
        s.setValue("last_project", self.project_dir() or "")
        s.setValue("opt/release", self.chk_release.isChecked())
        s.setValue("opt/verbose", self.chk_verbose.isChecked())
        s.setValue("opt/extra", self.ed_extra.text())
        s.setValue("opt/env", self.ed_env.text())
        s.setValue("opt/clear_on_run", self.chk_clear_on_run.isChecked())
        s.setValue("new/parent", self.ed_new_parent.text())
        s.setValue("new/edition", self.cb_edition.currentText())
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("ui/language", current_language())
        s.sync()

    def _auto_detect(self):
        self.ed_cargo_path.setText(find_tool("cargo"))
        self.ed_rustup_path.setText(find_tool("rustup"))

    def _clear_recent(self):
        cur = self.cb_project.currentText()
        self.cb_project.clear()
        if cur:
            self.cb_project.addItem(cur)
        self._save_settings()

    def _browse_exe(self, edit: QLineEdit, name: str):
        flt = f"{name}.exe (*.exe)" if IS_WIN else "All (*)"
        p, _ = QFileDialog.getOpenFileName(self, t("選擇 {name}").format(name=name), edit.text() or str(Path.home()), flt)
        if p:
            edit.setText(norm_exe(str(Path(p))))

    def _check_tools(self):
        cargo = self.cargo_path()
        if not cargo:
            self.log_info(t("⚠ 找不到 cargo，請至「設定」指定路徑，或安裝 Rust: https://rustup.rs"), "#ff6b6b")
            self._refresh_about()
            return
        self._refresh_about()
        self.run_quiet(cargo, ["--version"], None,
                       lambda code, out, err: self.log_info(f"✔ {out.strip() or err.strip()}", "#7ee787"))

    def closeEvent(self, e):
        self._save_settings()
        if self.proc and self.proc.state() != QProcess.NotRunning:
            if QMessageBox.question(self, APP_NAME, t("仍有指令在執行，確定要結束？")) != QMessageBox.Yes:
                e.ignore()
                return
            self.stop_process()
        super().closeEvent(e)

    # ───────────────────────── 專案
    def cargo_path(self) -> str:
        return self.ed_cargo_path.text().strip() or find_tool("cargo")

    def rustup_path(self) -> str:
        return self.ed_rustup_path.text().strip() or find_tool("rustup")

    def project_dir(self) -> str:
        p = self.cb_project.currentText().strip()
        return p if p and (Path(p) / "Cargo.toml").exists() else ""

    def browse_project(self):
        start = self.cb_project.currentText() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, t("選擇 Cargo 專案資料夾"), start)
        if d:
            self.open_project(d)

    def open_project(self, path: str, quiet=False):
        path = str(Path(path.strip()).resolve()) if path.strip() else ""
        if not path or not (Path(path) / "Cargo.toml").exists():
            if not quiet:
                QMessageBox.warning(self, APP_NAME, t("資料夾內沒有 Cargo.toml:\n{path}").format(path=path))
            return
        idx = self.cb_project.findText(path)
        if idx >= 0:
            self.cb_project.removeItem(idx)
        self.cb_project.insertItem(0, path)
        while self.cb_project.count() > 15:
            self.cb_project.removeItem(self.cb_project.count() - 1)
        self.cb_project.setCurrentIndex(0)
        self.main_tabs.setCurrentWidget(self.page_manage)
        if not self.ed_new_parent.text():
            self.ed_new_parent.setText(str(Path(path).parent))
        self._save_settings()
        self.refresh_project()

    def refresh_project(self):
        d = self.project_dir()
        if not d:
            return
        cargo = self.cargo_path()
        if not cargo:
            return
        self._set_info("讀取中…")
        self.run_quiet(cargo, ["metadata", "--no-deps", "--format-version", "1"], d, self._on_metadata)

    def _on_metadata(self, code, out, err):
        if code != 0:
            self._set_info("讀取 Cargo.toml 失敗")
            self.log_raw(err)
            return
        try:
            meta = json.loads(out)
        except json.JSONDecodeError as ex:
            self._set_info("解析 metadata 失敗: {ex}", ex=ex)
            return
        self.metadata = meta
        pkgs = meta.get("packages", [])
        cur_pkg = self.cb_package.currentData()
        self.cb_package.blockSignals(True)
        self.cb_package.clear()
        self.cb_package.addItem(t("(預設)"), "")
        for p in pkgs:
            self.cb_package.addItem(f"{p['name']} {p['version']}", p["name"])
        i = self.cb_package.findData(cur_pkg) if cur_pkg else -1
        if i < 0 and len(pkgs) > 1:
            # workspace: 預選 manifest 在目前資料夾的套件
            here = str(Path(self.project_dir()) / "Cargo.toml")
            for j, p in enumerate(pkgs):
                if os.path.normcase(p["manifest_path"]) == os.path.normcase(here):
                    i = j + 1
        self.cb_package.setCurrentIndex(max(i, 0))
        self.cb_package.blockSignals(False)
        self._show_project_info()
        self._fill_package_views()

    def _show_project_info(self):
        self._info = None
        pkgs = self.metadata.get("packages", [])
        if len(pkgs) == 1:
            p = pkgs[0]
            desc = p.get("description") or ""
            deps = t("{n} 個相依").format(n=len(p.get("dependencies", [])))
            self.lbl_info.setText(
                f"<b style='color:#e8a15c'>{html.escape(p['name'])}</b> v{html.escape(p['version'])}"
                f" &nbsp;|&nbsp; edition {p.get('edition', '?')}"
                f" &nbsp;|&nbsp; {deps}"
                + (f" &nbsp;|&nbsp; <span style='color:#8b8f96'>{html.escape(desc)}</span>" if desc else ""))
        else:
            root = self.metadata.get("workspace_root", "")
            members = t("{n} 個成員").format(n=len(pkgs))
            self.lbl_info.setText(
                f"<b style='color:#e8a15c'>Workspace</b> &nbsp;|&nbsp; {members}"
                f" &nbsp;|&nbsp; <span style='color:#8b8f96'>{html.escape(root)}</span>")

    @staticmethod
    def _dep_kind_label(kind) -> str:
        return t({None: "一般", "dev": "開發", "build": "建置"}.get(kind, kind or ""))

    def _current_package(self):
        if not self.metadata:
            return None
        pkgs = self.metadata.get("packages", [])
        name = self.cb_package.currentData()
        if name:
            for p in pkgs:
                if p["name"] == name:
                    return p
        return pkgs[0] if len(pkgs) == 1 else None

    def _fill_package_views(self, *_):
        p = self._current_package()
        # bin 清單
        cur_bin = self.cb_bin.currentText()
        self.cb_bin.clear()
        self.cb_bin.addItem("")
        if p:
            for t in p.get("targets", []):
                if "bin" in t.get("kind", []):
                    self.cb_bin.addItem(t["name"])
        self.cb_bin.setCurrentText(cur_bin if self.cb_bin.findText(cur_bin) >= 0 else "")
        # 相依表
        self.tbl_deps.setRowCount(0)
        if not p:
            return
        deps = sorted(p.get("dependencies", []), key=lambda d: (str(d.get("kind")), d["name"]))
        self.tbl_deps.setRowCount(len(deps))
        for r, d in enumerate(deps):
            src = d.get("path") or d.get("source") or ""
            if d.get("target"):
                src = f"[{d['target']}] {src}"
            name = d["name"] if not d.get("rename") else f"{d['rename']} → {d['name']}"
            vals = [name, d.get("req", ""), self._dep_kind_label(d.get("kind")),
                    ", ".join(d.get("features", [])), "✓" if d.get("optional") else "", src]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, (d.get("rename") or d["name"], d["name"], d.get("kind")))
                self.tbl_deps.setItem(r, c, it)

    def open_in_explorer(self):
        d = self.project_dir()
        if d:
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def open_in_vscode(self):
        d = self.project_dir()
        if not d:
            return
        code = shutil.which("code")
        if not code:
            QMessageBox.warning(self, APP_NAME, t("找不到 VS Code 的 code 指令 (請確認已加入 PATH)。"))
            return
        try:
            subprocess.Popen([code, d], shell=IS_WIN)
        except OSError as ex:
            QMessageBox.warning(self, APP_NAME, str(ex))

    # ───────────────────────── 輸出
    def log_raw(self, text: str):
        for line in text.splitlines():
            self.console.appendHtml(self.ansi.convert(line))
        self._scroll()

    def log_info(self, text: str, color="#e8a15c"):
        self.console.appendHtml(f'<span style="color:{color}">{html.escape(text)}</span>')
        self._scroll()

    def _scroll(self):
        if self.chk_autoscroll.isChecked():
            self.console.moveCursor(QTextCursor.End)
            self.console.ensureCursorVisible()

    def _on_output(self):
        if not self.proc:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        data = data.replace("\r\n", "\n")
        text = self._partial + data
        lines = text.split("\n")
        self._partial = lines.pop()
        for line in lines:
            # 進度列 (\r) 只保留最後一段
            if "\r" in line:
                line = line.split("\r")[-1]
            self.console.appendHtml(self.ansi.convert(line))
        if lines:
            self._scroll()

    # ───────────────────────── 執行程序
    def _env(self, extra_env: dict | None = None, color=True) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CARGO_TERM_COLOR", "always" if color else "never")
        cargo_bin = str(Path.home() / ".cargo" / "bin")
        path = env.value("PATH", "")
        if cargo_bin not in path:
            env.insert("PATH", cargo_bin + os.pathsep + path)
        for k, v in (extra_env or {}).items():
            env.insert(k, v)
        return env

    def _parse_env(self) -> dict:
        out = {}
        for tok in split_args(self.ed_env.text()):
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k.strip()] = v
        return out

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.state() != QProcess.NotRunning)

    def run_process(self, program: str, args: list, cwd: str | None, on_done=None, env_extra=None):
        if self.is_running():
            QMessageBox.information(self, APP_NAME, t("已有指令在執行中，請等待完成或按「停止」。"))
            return
        if not program:
            QMessageBox.warning(self, APP_NAME, t("找不到執行檔，請至「設定」指定 cargo / rustup 路徑。"))
            return
        if self.chk_clear_on_run.isChecked():
            self.console.clear()
        self.ansi.reset()
        self._partial = ""
        shown = " ".join(quote_arg(a) for a in [Path(program).stem] + args)
        self.log_info(f"\n$ {shown}" + (f"    [{cwd}]" if cwd else ""), "#61afef")

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.setProcessEnvironment(self._env(env_extra))
        if cwd:
            self.proc.setWorkingDirectory(cwd)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(lambda code, status: self._on_finished(code, status, on_done))
        self.proc.errorOccurred.connect(self._on_error)
        self._set_busy(True, shown)
        self.proc.start(program, args)

    def _on_error(self, err):
        if err == QProcess.FailedToStart:
            self.log_info(t("✖ 無法啟動程序: {err}").format(err=self.proc.errorString()), "#ff6b6b")
            self._set_busy(False)

    def _on_finished(self, code, status, on_done):
        if self._partial:
            self.console.appendHtml(self.ansi.convert(self._partial))
            self._partial = ""
        if status == QProcess.CrashExit:
            self.log_info(t("■ 程序已中止"), "#e5c07b")
        elif code == 0:
            self.log_info(t("✔ 完成 (exit 0)"), "#7ee787")
        else:
            self.log_info(t("✖ 失敗 (exit {code})").format(code=code), "#ff6b6b")
        self._set_busy(False)
        if on_done:
            on_done(code)

    def _set_busy(self, busy: bool, text=""):
        self.btn_stop.setEnabled(busy)
        for b in self.busy_buttons:
            b.setEnabled(not busy)
        if busy:
            self._set_status("執行中: {text}", text=text)
        else:
            self._set_status("就緒")

    def stop_process(self):
        if not self.is_running():
            return
        pid = self.proc.processId()
        if IS_WIN and pid:
            # 一併結束 cargo run 啟動的子程序
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            self.proc.terminate()
            if not self.proc.waitForFinished(2000):
                self.proc.kill()

    def run_quiet(self, program: str, args: list, cwd: str | None, callback):
        """背景執行並收集輸出 (不佔用主程序)。callback(code, stdout, stderr)"""
        if not program:
            return
        p = QProcess(self)
        p.setProcessEnvironment(self._env(color=False))
        if cwd:
            p.setWorkingDirectory(cwd)
        self.quiet_procs.add(p)

        def done(code, _status):
            try:
                out = bytes(p.readAllStandardOutput()).decode("utf-8", errors="replace")
                err = bytes(p.readAllStandardError()).decode("utf-8", errors="replace")
            except RuntimeError:  # 視窗關閉時程序已被銷毀
                return
            self.quiet_procs.discard(p)
            p.deleteLater()
            callback(code, out, err)

        def failed(e):
            if e == QProcess.FailedToStart:
                self.quiet_procs.discard(p)
                callback(-1, "", t("無法啟動: {err}").format(err=p.errorString()))

        p.finished.connect(done)
        p.errorOccurred.connect(failed)
        p.start(program, args)

    def run_cargo(self, args: list, need_project=True, on_done=None, env_extra=None):
        cwd = self.project_dir()
        if need_project and not cwd:
            QMessageBox.warning(self, APP_NAME, t("請先開啟含 Cargo.toml 的專案資料夾。"))
            return
        self.run_process(self.cargo_path(), args, cwd or None, on_done, env_extra)

    def run_rustup(self, args: list):
        self.run_process(self.rustup_path(), args, self.project_dir() or None)

    def _rustup_with(self, edit: QLineEdit, args: list):
        v = edit.text().strip()
        if not v:
            QMessageBox.warning(self, APP_NAME, t("請先輸入名稱。"))
            return
        self.run_rustup(args + [v])

    # ───────────────────────── 建置動作
    def _common_opts(self, with_features=True, with_target=True, with_profile=True) -> list:
        a = []
        if self.chk_workspace.isChecked():
            a.append("--workspace")
        else:
            pkg = self.cb_package.currentData()
            if pkg:
                a += ["-p", pkg]
        if with_profile and self.chk_release.isChecked():
            a.append("--release")
        if with_features:
            if self.chk_all_features.isChecked():
                a.append("--all-features")
            else:
                feats = [f for f in re.split(r"[,\s]+", self.ed_features.text().strip()) if f]
                if feats:
                    a += ["--features", ",".join(feats)]
            if self.chk_no_default.isChecked():
                a.append("--no-default-features")
        if with_target:
            t = self.cb_target.currentText().strip()
            if t:
                a += ["--target", t]
        if self.chk_verbose.isChecked():
            a.append("-v")
        a += split_args(self.ed_extra.text())
        return a

    def run_action(self, key: str):
        env = self._parse_env()
        run_args = split_args(self.ed_run_args.text())
        bin_name = self.cb_bin.currentText().strip()
        if key == "run":
            args = ["run"] + self._common_opts()
            if bin_name:
                args += ["--bin", bin_name]
            if run_args:
                args += ["--"] + run_args
        elif key in ("build", "check", "bench", "fix"):
            args = [key] + self._common_opts()
            if bin_name and key in ("build", "check"):
                args += ["--bin", bin_name]
            if key == "fix":
                args.append("--allow-dirty")
        elif key == "test":
            args = ["test"] + self._common_opts()
            if run_args:
                args += ["--"] + run_args
        elif key == "clippy":
            args = ["clippy"] + self._common_opts() + ["--all-targets"]
        elif key == "clippy-fix":
            args = ["clippy", "--fix", "--allow-dirty", "--allow-staged"] + self._common_opts()
        elif key in ("fmt", "fmt-check"):
            args = ["fmt"]
            if self.chk_workspace.isChecked():
                args.append("--all")
            elif self.cb_package.currentData():
                args += ["-p", self.cb_package.currentData()]
            if key == "fmt-check":
                args.append("--check")
        elif key == "doc":
            args = ["doc", "--open", "--no-deps"] + self._common_opts(with_target=False)
        elif key == "clean":
            if QMessageBox.question(self, APP_NAME, t("確定要清除 target 資料夾？")) != QMessageBox.Yes:
                return
            args = ["clean"]
            if self.chk_release.isChecked():
                args.append("--release")
        elif key == "update":
            args = ["update"]
        elif key == "tree":
            args = ["tree"]
            if self.cb_package.currentData() and not self.chk_workspace.isChecked():
                args += ["-p", self.cb_package.currentData()]
        else:
            return
        self.run_cargo(args, env_extra=env)

    def run_custom(self):
        text = self.ed_custom.text().strip()
        if not text:
            return
        args = split_args(text)
        if args and args[0] == "cargo":
            args = args[1:]
        self.run_cargo(args, need_project=False, env_extra=self._parse_env(),
                       on_done=lambda code: self.refresh_project())

    def load_targets(self):
        rustup = self.rustup_path()
        if not rustup:
            QMessageBox.warning(self, APP_NAME, t("找不到 rustup。"))
            return

        def done(code, out, err):
            cur = self.cb_target.currentText()
            self.cb_target.clear()
            self.cb_target.addItem("")
            for line in out.splitlines():
                if line.strip():
                    self.cb_target.addItem(line.strip())
            self.cb_target.setCurrentText(cur)
            self.log_info(t("已載入 {n} 個已安裝 target").format(n=self.cb_target.count() - 1), "#8b8f96")

        self.run_quiet(rustup, ["target", "list", "--installed"], self.project_dir() or None, done)

    def show_versions(self):
        rustup = self.rustup_path()
        cargo = self.cargo_path()
        self.run_quiet(cargo, ["--version", "--verbose"], None,
                       lambda c, o, e: self.log_info(o.strip() or e.strip(), "#d7dae0"))
        rustc = find_tool("rustc")
        if rustc:
            self.run_quiet(rustc, ["--version", "--verbose"], None,
                           lambda c, o, e: self.log_info(o.strip() or e.strip(), "#d7dae0"))
        if rustup:
            self.run_quiet(rustup, ["--version"], None,
                           lambda c, o, e: self.log_info((o.strip() or e.strip()).splitlines()[0]
                                                         if (o.strip() or e.strip()) else "", "#d7dae0"))

    def override_set(self):
        d = self.project_dir()
        tc = self.ed_toolchain.text().strip()
        if not d or not tc:
            QMessageBox.warning(self, APP_NAME, t("請先開啟專案並輸入 toolchain。"))
            return
        self.run_process(self.rustup_path(), ["override", "set", tc], d)

    def override_unset(self):
        d = self.project_dir()
        if d:
            self.run_process(self.rustup_path(), ["override", "unset"], d)

    # ───────────────────────── 相依套件
    def _selected_deps(self):
        rows = sorted({i.row() for i in self.tbl_deps.selectedIndexes()})
        return [self.tbl_deps.item(r, 0).data(Qt.UserRole) for r in rows]

    def _pkg_args(self):
        pkg = self.cb_package.currentData()
        return ["-p", pkg] if pkg else []

    def add_dependency(self, name="", version=""):
        if not self.project_dir():
            QMessageBox.warning(self, APP_NAME, t("請先開啟專案。"))
            return
        dlg = AddDependencyDialog(self, name if isinstance(name, str) else "", version)
        if dlg.exec() != QDialog.Accepted:
            return
        self.run_cargo(dlg.build_args() + self._pkg_args(), on_done=lambda c: self.refresh_project())

    def remove_dependency(self):
        deps = self._selected_deps()
        if not deps:
            QMessageBox.information(self, APP_NAME, t("請先在表格中選取要移除的套件。"))
            return
        names = ", ".join(d[0] for d in deps)
        if QMessageBox.question(self, APP_NAME, t("確定移除: {names} ?").format(names=names)) != QMessageBox.Yes:
            return
        # 依類型分組
        groups = {}
        for key, _real, kind in deps:
            groups.setdefault(kind, []).append(key)
        queue = []
        for kind, keys in groups.items():
            a = ["remove"] + keys + self._pkg_args()
            if kind == "dev":
                a.append("--dev")
            elif kind == "build":
                a.append("--build")
            queue.append(a)
        self._run_queue(queue)

    def update_dependency(self):
        deps = self._selected_deps()
        if not deps:
            QMessageBox.information(self, APP_NAME, t("請先在表格中選取套件 (未選取請用「建置」頁的 update)。"))
            return
        args = ["update"]
        for _key, real, _kind in deps:
            args += ["-p", real]
        self.run_cargo(args, on_done=lambda c: self.refresh_project())

    def _run_queue(self, queue: list):
        if not queue:
            self.refresh_project()
            return
        first, rest = queue[0], queue[1:]
        self.run_cargo(first, on_done=lambda c: self._run_queue(rest) if c == 0 else self.refresh_project())

    def search_crates(self):
        q = self.ed_search.text().strip()
        if not q:
            return
        self.tbl_search.setRowCount(0)
        self._set_status("搜尋 crates.io: {q} …", q=q)
        self.run_quiet(self.cargo_path(), ["search", q, "--limit", "30"], None, self._on_search)

    def _on_search(self, code, out, err):
        if not self.is_running():
            self._set_status("就緒")
        if code != 0:
            self.log_info(t("搜尋失敗:"), "#ff6b6b")
            self.log_raw(err)
            return
        pat = re.compile(r'^([\w\-]+)\s*=\s*"([^"]+)"\s*(?:#\s*(.*))?$')
        rows = [m.groups() for m in (pat.match(l.strip()) for l in out.splitlines()) if m]
        self.tbl_search.setRowCount(len(rows))
        for r, (n, v, d) in enumerate(rows):
            for c, val in enumerate((n, v, d or "")):
                self.tbl_search.setItem(r, c, QTableWidgetItem(val))
        if not rows:
            self.log_info(t("找不到符合的 crate"), "#8b8f96")

    def add_from_search(self, index):
        r = index.row()
        self.add_dependency(self.tbl_search.item(r, 0).text(), "")

    # ───────────────────────── 新專案
    def _browse_new_parent(self):
        d = QFileDialog.getExistingDirectory(self, t("選擇資料夾"), self.ed_new_parent.text() or str(Path.home()))
        if d:
            self.ed_new_parent.setText(d)

    def _new_common(self):
        a = ["--lib" if self.rb_lib.isChecked() else "--bin",
             "--edition", self.cb_edition.currentText(),
             "--vcs", self.cb_vcs.currentText()]
        return a

    def cargo_new(self):
        parent = self.ed_new_parent.text().strip()
        name = self.ed_new_name.text().strip()
        if not parent or not Path(parent).is_dir():
            QMessageBox.warning(self, APP_NAME, t("請選擇有效的上層資料夾。"))
            return
        if not name:
            QMessageBox.warning(self, APP_NAME, t("請輸入專案名稱。"))
            return
        target = Path(parent) / name
        if target.exists():
            QMessageBox.warning(self, APP_NAME, t("資料夾已存在:\n{target}\n請改用 cargo init。").format(target=target))
            return
        self.run_process(self.cargo_path(), ["new", name] + self._new_common(), parent,
                         on_done=lambda c: c == 0 and self.open_project(str(target)))

    def cargo_init(self):
        parent = self.ed_new_parent.text().strip()
        name = self.ed_new_name.text().strip()
        if not parent or not Path(parent).is_dir():
            QMessageBox.warning(self, APP_NAME, t("請選擇有效的資料夾。"))
            return
        if (Path(parent) / "Cargo.toml").exists():
            QMessageBox.warning(self, APP_NAME, t("此資料夾已經有 Cargo.toml。"))
            return
        args = ["init"] + self._new_common()
        if name:
            args += ["--name", name]
        self.run_process(self.cargo_path(), args, parent,
                         on_done=lambda c: c == 0 and self.open_project(parent))

    # ───────────────────────── cargo install
    def cargo_install(self):
        name = self.ed_install.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, t("請輸入 crate 名稱。"))
            return
        args = ["install", name]
        v = self.ed_install_ver.text().strip()
        if v:
            args += ["--version", v]
        if self.chk_install_locked.isChecked():
            args.append("--locked")
        if self.chk_install_force.isChecked():
            args.append("--force")
        self.run_cargo(args, need_project=False)

    def cargo_uninstall(self):
        name = self.ed_install.text().strip()
        if not name:
            QMessageBox.warning(self, APP_NAME, t("請輸入 crate 名稱。"))
            return
        if QMessageBox.question(self, APP_NAME, t("確定解除安裝 {name} ?").format(name=name)) == QMessageBox.Yes:
            self.run_cargo(["uninstall", name], need_project=False)


# ──────────────────────────────────────────────────────────── 參數處理
def split_args(text: str) -> list:
    """類 shell 的參數切割, 支援雙/單引號 (Windows 路徑反斜線不轉義)。"""
    out, cur, quote, has = [], [], None, False
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            quote = ch
            has = True
        elif ch.isspace():
            if cur or has:
                out.append("".join(cur))
                cur, has = [], False
        else:
            cur.append(ch)
    if cur or has:
        out.append("".join(cur))
    return out


def quote_arg(a: str) -> str:
    return f'"{a}"' if (not a or any(c.isspace() for c in a)) else a


# ──────────────────────────────────────────────────────────── main
def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeanLin.CargoManager")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    apply_dark(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
