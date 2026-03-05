#!/usr/bin/env python3
"""
Local Web UI for paper_downloader.py.

Features:
- Pick download folder with native dialog.
- Paste multiple paper titles.
- Run download job (arXiv -> IEEE -> major sites priority in script).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

import paper_downloader

APP_DIR = Path(__file__).resolve().parent

app = Flask(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local web UI for paper downloader.")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7860, help="Port (default: 7860)")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not auto-open browser."
    )
    return parser.parse_args()


def open_folder_dialog(initial_dir: str) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askdirectory(
        title="Select download folder",
        initialdir=initial_dir or str(APP_DIR),
        mustexist=False,
    )
    root.destroy()
    return selected or ""


def parse_titles(raw: str) -> list[str]:
    titles: list[str] = []
    for line in raw.splitlines():
        title = line.strip()
        if title and not title.startswith("#"):
            titles.append(title)
    return titles


def build_cli_args(
    titles_file: Path,
    download_dir: str,
    source: str,
    workers: int,
    min_score: float,
    ieee_manual_login: bool,
    ieee_headless: bool,
) -> list[str]:
    cli_args = [
        "--papers-file",
        str(titles_file),
        "--download-dir",
        download_dir,
        "--source",
        source,
        "--workers",
        str(workers),
        "--min-score",
        str(min_score),
    ]
    if ieee_manual_login:
        cli_args.append("--ieee-manual-login")
    if ieee_headless:
        cli_args.append("--ieee-headless")
    return cli_args


def run_downloader(cli_args: list[str]) -> tuple[int, str]:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    exit_code = 1
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        try:
            exit_code = paper_downloader.main(cli_args)
        except Exception as exc:  # noqa: BLE001
            print(f"[WEB][ERROR] downloader crashed: {exc}")
            exit_code = 1

    stdout_text = stdout_buffer.getvalue().strip()
    stderr_text = stderr_buffer.getvalue().strip()
    logs = stdout_text
    if stderr_text:
        logs = f"{logs}\n{stderr_text}".strip()
    return int(exit_code), logs


@app.get("/")
def index() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Paper Downloader</title>
  <style>
    :root {
      --bg: #f4f6fb;
      --card: #ffffff;
      --text: #172033;
      --muted: #5d6a85;
      --accent: #0f6cff;
      --border: #d9e0f0;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: radial-gradient(circle at top right, #e6edff 0, var(--bg) 45%);
      color: var(--text);
    }
    .wrap { max-width: 960px; margin: 32px auto; padding: 0 16px; }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(18, 32, 76, 0.08);
    }
    h1 { margin: 0 0 12px; font-size: 26px; }
    .muted { color: var(--muted); font-size: 14px; margin-bottom: 14px; }
    .row { display: flex; gap: 10px; margin-bottom: 12px; align-items: center; flex-wrap: wrap; }
    label { font-size: 14px; font-weight: 600; min-width: 86px; }
    input[type="text"], input[type="number"], select, textarea {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 14px;
      color: var(--text);
      background: #fff;
    }
    textarea { min-height: 200px; resize: vertical; }
    .grow { flex: 1; min-width: 240px; }
    button {
      border: 0;
      border-radius: 10px;
      padding: 10px 14px;
      font-size: 14px;
      cursor: pointer;
      background: var(--accent);
      color: #fff;
    }
    button.secondary { background: #ecf2ff; color: #20408b; border: 1px solid #cfe0ff; }
    button:disabled { opacity: .6; cursor: not-allowed; }
    .checks { display: flex; gap: 18px; font-size: 14px; color: var(--muted); }
    .checks label { min-width: auto; font-weight: 500; display: flex; align-items: center; gap: 6px; }
    pre {
      margin: 0;
      background: #0f172a;
      color: #f8fafc;
      border-radius: 12px;
      padding: 12px;
      min-height: 180px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.45;
    }
    @media (max-width: 740px) {
      .row { flex-direction: column; align-items: stretch; }
      label { min-width: auto; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>论文批量下载器</h1>
      <div class="muted">优先级固定为 arXiv -> IEEE -> major sites。粘贴多行论文名后点击开始。</div>

      <div class="row">
        <label>下载目录</label>
        <input id="downloadDir" class="grow" type="text" placeholder="例如 D:\\papers" />
        <button type="button" class="secondary" onclick="pickFolder()">选择文件夹</button>
      </div>

      <div class="row">
        <label>数据源</label>
        <select id="source" class="grow">
          <option value="both">both (推荐)</option>
          <option value="arxiv">arxiv</option>
          <option value="ieee">ieee</option>
          <option value="major">major</option>
        </select>

        <label>并发</label>
        <input id="workers" type="number" min="1" max="16" value="4" style="width:100px" />

        <label>阈值</label>
        <input id="minScore" type="number" min="0" max="1" step="0.05" value="0.55" style="width:100px" />
      </div>

      <div class="row checks">
        <label><input id="ieeeManualLogin" type="checkbox" /> IEEE 手动登录/验证码</label>
        <label><input id="ieeeHeadless" type="checkbox" /> IEEE 无头模式</label>
      </div>

      <div class="row">
        <label>论文列表</label>
        <textarea id="paperTitles" class="grow" placeholder="每行一篇论文标题"></textarea>
      </div>

      <div class="row">
        <button id="runBtn" type="button" onclick="runDownload()">开始下载</button>
      </div>

      <pre id="logs">等待开始...</pre>
    </div>
  </div>

  <script>
    const logsEl = document.getElementById("logs");
    const runBtn = document.getElementById("runBtn");

    function setLogs(text) {
      logsEl.textContent = text || "";
      logsEl.scrollTop = logsEl.scrollHeight;
    }

    async function pickFolder() {
      try {
        const initial = document.getElementById("downloadDir").value || "";
        const resp = await fetch("/api/select-folder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initial_dir: initial })
        });
        const data = await resp.json();
        if (data.ok && data.path) {
          document.getElementById("downloadDir").value = data.path;
        }
      } catch (e) {
        setLogs("选择文件夹失败: " + e);
      }
    }

    async function runDownload() {
      const payload = {
        download_dir: document.getElementById("downloadDir").value.trim(),
        source: document.getElementById("source").value,
        workers: Number(document.getElementById("workers").value || 1),
        min_score: Number(document.getElementById("minScore").value || 0.55),
        ieee_manual_login: document.getElementById("ieeeManualLogin").checked,
        ieee_headless: document.getElementById("ieeeHeadless").checked,
        paper_titles: document.getElementById("paperTitles").value
      };

      runBtn.disabled = true;
      setLogs("任务开始，正在下载，请稍等...\\n");
      try {
        const resp = await fetch("/api/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();
        setLogs((data.logs || "") + "\\n\\n[WEB] Exit code: " + data.exit_code);
      } catch (e) {
        setLogs("运行失败: " + e);
      } finally {
        runBtn.disabled = false;
      }
    }
  </script>
</body>
</html>
"""


@app.post("/api/select-folder")
def select_folder() -> Any:
    payload = request.get_json(silent=True) or {}
    initial_dir = str(payload.get("initial_dir", "")).strip()
    try:
        selected = open_folder_dialog(initial_dir)
        return jsonify({"ok": True, "path": selected})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/download")
def download() -> Any:
    payload = request.get_json(silent=True) or {}
    raw_titles = str(payload.get("paper_titles", ""))
    titles = parse_titles(raw_titles)
    if not titles:
        return jsonify({"ok": False, "error": "No paper titles provided.", "exit_code": 1}), 400

    download_dir = str(payload.get("download_dir", "")).strip()
    if not download_dir:
        return jsonify({"ok": False, "error": "Please choose a download folder.", "exit_code": 1}), 400

    source = str(payload.get("source", "both")).strip().lower()
    if source not in {"arxiv", "ieee", "major", "both"}:
        source = "both"

    try:
        workers = max(1, int(payload.get("workers", 4)))
    except Exception:
        workers = 4

    try:
        min_score = float(payload.get("min_score", 0.55))
    except Exception:
        min_score = 0.55
    min_score = min(1.0, max(0.0, min_score))

    ieee_manual_login = bool(payload.get("ieee_manual_login", False))
    ieee_headless = bool(payload.get("ieee_headless", False))

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    ) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write("\n".join(titles))
        temp_file.write("\n")

    cli_args = build_cli_args(
        titles_file=temp_path,
        download_dir=download_dir,
        source=source,
        workers=workers,
        min_score=min_score,
        ieee_manual_login=ieee_manual_login,
        ieee_headless=ieee_headless,
    )

    try:
        exit_code, logs = run_downloader(cli_args)
        return jsonify(
            {
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "logs": logs.strip(),
                "command": "paper_downloader.py " + " ".join(cli_args),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "exit_code": 1}), 500
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> int:
    args = parse_args()
    if not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
