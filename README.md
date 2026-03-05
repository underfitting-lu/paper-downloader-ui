# Paper Downloader (Local Web UI + Packaged EXE)

Stop wasting time on copy-paste paper hunting.

If your workflow looks like this:
- search a title manually
- open multiple sites one by one
- click PDF/download repeatedly
- rename messy filenames after download

This project is built for that exact pain.

Paste a list of paper titles once, choose a folder, click run, and let it fetch papers automatically.
The input parser supports both one-line-per-title and messy pasted blocks (for example: `Liu et al., 2023; Wang et al., 2022`).

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
- IEEE manual login: use when CAPTCHA/login is required
- IEEE headless: run IEEE browser in background
- Scholar manual login: wait for you to finish Scholar/portal login before continuing
- Scholar headless: run Scholar browser in background
- input parser supports:
  - one title per line
  - numbered lists like `1) ... 2) ...`
  - pasted citation chains like `Liu et al., 2023 Wang et al., 2022`
  - recommendation: use full paper titles for highest hit accuracy

## Login Behavior (Important)

- IEEE and Scholar/portal manual login are done once per run (session reused), so it will not keep re-opening login for every paper.
- In terminal mode, it waits for your Enter confirmation after login.
- In non-interactive mode (for example packaged app without stdin), it uses `MANUAL_LOGIN_WAIT_SECONDS` from `.env` before sending the next request.
