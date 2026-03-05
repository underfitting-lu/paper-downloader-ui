# Paper Downloader (Local Web UI + Packaged EXE)

## 中文简介

还在为“找论文 + 点下载 + 改文件名”反复机械操作吗？

这个项目专门解决这个痛点：
- 一次粘贴多篇论文（支持乱序引用文本）
- 本地网页选择下载目录后一键运行
- 自动检索并下载 PDF，文件名自动改为论文真实标题

适合想把论文搜集流程标准化、批量化的学生和研究者。

## English Overview

Tired of the repetitive workflow: search title, open portals, click PDF, rename files?

This project is built for exactly that pain:
- Paste multiple paper names once (including messy citation blocks)
- Choose a local folder in a simple web UI
- Auto-fetch PDFs and rename files to the real paper titles

Great for students and researchers who want a fast, repeatable paper collection workflow.

Search priority is fixed:
1. arXiv (first)
2. IEEE
3. Google Scholar + portal filters (Elsevier / ACS / CNS)
4. ORCID works lookup
5. major publisher fallback (ACM/Springer/Elsevier/Wiley/Nature, etc.)

Filename rule:
- use matched real title
- replace `:` with space
- replace illegal Windows filename chars with `_`

## For End Users (No Python Needed)

1. Go to GitHub **Actions** or **Releases** and download `PaperDownloaderUI-windows.zip`.
2. Unzip it.
3. Double-click `PaperDownloaderUI.exe`.
4. Browser opens local UI (`http://127.0.0.1:7860`).

## For Developers (Run from Source)

```bat
cd paper-downloader
start_web_ui.bat
```

`start_web_ui.bat` will create `.venv`, install dependencies, and start the web UI.

## Build Standalone EXE Locally

```bat
cd paper-downloader
build_exe.bat
```

Output:
- `release\PaperDownloaderUI\PaperDownloaderUI.exe`

## Build Standalone EXE on GitHub

This repo includes workflow:
- `.github/workflows/build-windows-exe.yml`

How to use:
1. Push a tag like `v1.0.0`, or run **Actions -> Build Windows EXE -> Run workflow**.
2. Download artifact/release asset `PaperDownloaderUI-windows.zip`.

## UI Options

- source: `both` / `arxiv` / `ieee` / `scholar` / `orcid` / `major`
- workers: parallel downloads
- min score: title match threshold (0-1, larger is stricter)
- portal mode: automatic (headless-first with built-in fallback)
- input parser supports:
  - one title per line
  - numbered lists like `1) ... 2) ...`
  - pasted citation chains like `Liu et al., 2023 Wang et al., 2022`
  - recommendation: use full paper titles for highest hit accuracy

## Login Behavior (Important)

- IEEE and Scholar/portal manual login are done once per run (session reused), so it will not keep re-opening login for every paper.
- Browser session is persisted under `.browser-profile` by default, so login state can survive across app restarts.
- If IEEE headless mode fails to get a usable result, the script auto-retries once in visible mode with the same profile.
- In terminal mode, it waits for your Enter confirmation after login.
- In non-interactive mode (for example packaged app without stdin), it uses `MANUAL_LOGIN_WAIT_SECONDS` from `.env` before sending the next request.
