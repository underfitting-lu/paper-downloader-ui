# Paper Downloader (Local Web UI + Packaged EXE)

Stop wasting time on copy-paste paper hunting.

If your workflow looks like this:
- search a title manually
- open multiple sites one by one
- click PDF/download repeatedly
- rename messy filenames after download

This project is built for that exact pain.

Paste a list of paper titles once, choose a folder, click run, and let it fetch papers automatically.

Search priority is fixed:
1. arXiv (first)
2. IEEE
3. major publisher fallback (ACM/Springer/Elsevier/Wiley/Nature, etc.)

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

- source: `both` / `arxiv` / `ieee` / `major`
- workers: parallel downloads
- min score: title match threshold (0-1, larger is stricter)
- IEEE manual login: use when CAPTCHA/login is required
- IEEE headless: run IEEE browser in background
