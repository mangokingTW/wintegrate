# Framework Comparisons

How `wintegrate` compares to other Windows automation libraries, and how to choose the right tool for your application stack.

---

## Comparison Matrix

| Feature / Dimension | `wintegrate` (Python) | `FlaUI` (.NET / C#) | `pywinauto` (Python) | `tauri-driver` / WebDriver | `Squish for Qt` (C++/Qt) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Primary Language** | Python 3.11+ | C# / F# (.NET) | Python 3.8+ | JavaScript / Rust / Any | Python / JS / C++ |
| **Target Ecosystem** | Any Windows GUI (CI-First) | .NET (WPF/WinForms/WinUI) | Win32 / UIA | Tauri / Electron (Web DOM) | Qt (Widgets / QML) |
| **License & Cost** | Open Source (MIT) | Open Source (MIT) | Open Source (BSD) | Open Source (MIT/Apache) | Commercial ($$$$/yr) |
| **Zero-Config Install** | `pip install wintegrate` | Requires .NET SDK & DLLs | `pip install pywinauto` | Requires WebDriver binary | Requires commercial agent |
| **In-Process Video Recording** | **Built-in (PyAV / MP4)** | External (OBS / FFmpeg) | None | None | Video on failure |
| **Desktop Window Census Diff** | **Built-in** | Manual | None | None | None |
| **Win11 XAML & Child HWND** | **RawViewWalker fallback** | Native UIA3 | Flaky on Win11 Store apps | N/A (Webview only) | Native Qt tree |
| **Native Windows 11 ARM64 CI** | **Verified on CI** | Supported on .NET ARM64 | Input drops / no PyAV | Supported | Limited |
| **Verified Post-Conditions** | **`_verified` (Polling)** | Manual (`Retry.While`) | `time.sleep()` | Explicit waits | Sync API |
| **System Tray & Native Dialogs** | **Full Support** | Full Support | Partial | **No (WebView DOM only)** | Full Support |

---

## Deep Dive by Technology Stack

### 1. Rust & Tauri Applications

#### The WebView Boundary Problem
Tauri applications combine a lightweight **Rust backend** with a **WebView2 (Chromium) frontend**.

* **What `tauri-driver` / Playwright can test**:
  `tauri-driver` communicates with WebView2 through the WebDriver protocol. It excels at testing HTML buttons, form inputs, and JavaScript frontend state.
* **What `tauri-driver` CANNOT test**:
  `tauri-driver` has no visibility into the Windows operating system outside the browser viewport. It cannot automate or verify:
  - System tray icon interactions and context menus.
  - Native Windows file dialogs (`OpenFileDialog`, `SaveFileDialog`).
  - Global system hotkeys registered via Windows API.
  - Application auto-update, installer, and process restarts.
  - Window minimization, restoration, and multi-monitor window placement.

#### Recommended Strategy for Tauri
> **Layered Testing**: Use Vitest / Playwright for fast frontend component unit tests; use **`wintegrate`** in GitHub Actions CI for full end-to-end OS integration (system tray, file pickers, window lifecycle, and crash diagnostics with video recording).

---

### 2. C++ & Python Qt Applications

#### Squish for Qt vs. `wintegrate`
* **Squish for Qt**:
  The industry benchmark for commercial Qt test automation. It injects a hook directly into `qApp` to inspect the internal `QObject` and `QQuickItem` memory trees.
  * *Trade-off*: Highly capable, but commercial licenses cost thousands of dollars per seat, making it inaccessible for many open-source projects and small-to-medium teams.
* **`wintegrate` (via Qt Accessibility)**:
  Qt natively implements Windows UI Automation through its `QAccessibleBridge`. Qt widgets (buttons, tables, tree views, combo boxes) are fully visible in the Windows UIA tree.
  * *Trade-off*: Free, open-source, and drives compiled release `.exe` binaries with automated screen recording and window census diffs in CI.

---

### 3. .NET Applications (WPF, WinForms, WinUI 3)

#### FlaUI vs. `wintegrate`
* **When to choose `FlaUI`**:
  If your entire test suite and engineering organization are standardized on C# (.NET / NUnit / xUnit), **FlaUI is the premier choice**. It provides compiled static typing and direct CLR-to-COM runtime callable wrappers.
* **When to choose `wintegrate`**:
  - Your CI/CD pipelines and test suites use **Python / pytest**.
  - You need **zero-config failure diagnostics** (in-process screen recording, pre/post window census diffs, and structured JSON action timelines) without stitching together custom screen-capture tools.
  - You want to test across mixed-language stacks (e.g. testing a C++ backend daemon alongside a .NET GUI).

---

### 4. Comparison with Python UIA Tools

#### `pywinauto`
* Originally built for interactive workstations on Windows XP through Windows 10.
* Struggles on modern Windows 11 Store apps (which launch decoupled launcher shims) and XAML Island nested child HWND boundaries (like modern Notepad).
* Lacks built-in streaming video recording, window census diffs, or native ARM64 wheel packaging.

#### `yinkaisheng/uiautomation`
* Monolithic Python module lacking modern session lifecycle management, structured context managers, type annotations, and automated CI diagnostic artifact pipelines.

---

## Summary: When to Use `wintegrate`

Use `wintegrate` when:
1. You are running **unattended Windows GUI tests on CI runners** (`windows-latest`, `windows-11-arm`) and need rich visual evidence (`.mp4` video, `window_census.json`) when a test fails.
2. You want a **zero-build, pure Python (`ctypes`/`comtypes`)** library that installs in under a second on both x64 and ARM64 Windows.
3. You need to test **real Windows interactions** (system tray, file dialogs, multi-window discovery, IME and physical scan codes) that browser-based drivers cannot reach.
