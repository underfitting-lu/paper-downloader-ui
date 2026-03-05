#!/usr/bin/env python3
"""
Local Web UI for paper_downloader.py.

Features:
- Pick download folder with native dialog.
- Paste multiple paper titles.
- Run download job (arXiv -> IEEE -> Scholar/portals -> ORCID -> major sites).
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


def build_cli_args(
    titles_file: Path,
    download_dir: str,
    source: str,
    workers: int,
    min_score: float,
    ieee_manual_login: bool,
    ieee_headless: bool,
    scholar_manual_login: bool,
    scholar_headless: bool,
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
    if scholar_manual_login:
        cli_args.append("--scholar-manual-login")
    if scholar_headless:
        cli_args.append("--scholar-headless")
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
  <title>Paper Downloader · Ink Glass</title>
  <style>
    :root {
      --bg-ink: #0f151c;
      --bg-paper: #d7d9dc;
      --glass: rgba(236, 239, 242, 0.44);
      --glass-strong: rgba(245, 247, 250, 0.62);
      --stroke: rgba(255, 255, 255, 0.52);
      --ink-900: #121923;
      --ink-700: #2c3a4f;
      --ink-500: #4d617e;
      --accent: #1368da;
      --accent-soft: #d9e8ff;
      --shadow: 0 28px 70px rgba(8, 13, 22, 0.32);
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink-900);
      font-family: "IBM Plex Sans", "Noto Sans SC", "Source Han Sans SC", "PingFang SC", sans-serif;
      background:
        radial-gradient(circle at 10% 12%, rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0) 30%),
        radial-gradient(circle at 85% 18%, rgba(38, 62, 94, 0.22), rgba(38, 62, 94, 0) 34%),
        radial-gradient(circle at 75% 85%, rgba(15, 24, 37, 0.30), rgba(15, 24, 37, 0) 40%),
        linear-gradient(135deg, var(--bg-paper), #c5cacf 55%, #b8bdc3);
      overflow-x: hidden;
    }
    #inkCanvas {
      position: fixed;
      inset: 0;
      z-index: 0;
      pointer-events: none;
      opacity: 0.52;
      mix-blend-mode: multiply;
    }
    .mist {
      position: fixed;
      border-radius: 999px;
      filter: blur(44px);
      pointer-events: none;
      z-index: 0;
    }
    .mist-a {
      width: 380px;
      height: 380px;
      top: -70px;
      left: -60px;
      background: rgba(248, 251, 255, 0.62);
      animation: driftA 14s ease-in-out infinite alternate;
    }
    .mist-b {
      width: 460px;
      height: 460px;
      top: 8%;
      right: -90px;
      background: rgba(27, 47, 76, 0.26);
      animation: driftB 18s ease-in-out infinite alternate;
    }
    .mist-c {
      width: 420px;
      height: 420px;
      bottom: -130px;
      left: 24%;
      background: rgba(15, 20, 28, 0.22);
      animation: driftC 20s ease-in-out infinite alternate;
    }
    .wrap {
      position: relative;
      z-index: 2;
      max-width: 1040px;
      margin: 44px auto;
      padding: 0 18px 34px;
    }
    .card {
      border-radius: 24px;
      padding: 26px 24px 22px;
      background: linear-gradient(135deg, var(--glass-strong), var(--glass));
      border: 1px solid var(--stroke);
      backdrop-filter: blur(14px) saturate(138%);
      -webkit-backdrop-filter: blur(14px) saturate(138%);
      box-shadow: var(--shadow);
      transform: translateY(6px);
      animation: riseIn 720ms cubic-bezier(0.2, 0.7, 0.2, 1) forwards;
    }
    .hero {
      margin-bottom: 18px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.36);
    }
    .eyebrow {
      margin: 0 0 8px;
      color: var(--ink-500);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font-size: 11px;
      font-weight: 700;
    }
    h1 {
      margin: 0;
      font-size: clamp(26px, 4.8vw, 42px);
      line-height: 1.14;
      font-family: "Noto Serif SC", "Source Han Serif SC", "STSong", serif;
      font-weight: 700;
      letter-spacing: 0.01em;
      color: #0f1824;
    }
    .muted {
      margin: 12px 0 0;
      max-width: 760px;
      color: var(--ink-700);
      font-size: 14px;
      line-height: 1.7;
    }
    .row {
      display: flex;
      gap: 10px;
      margin-bottom: 12px;
      align-items: center;
      flex-wrap: wrap;
    }
    label {
      font-size: 13px;
      font-weight: 700;
      min-width: 82px;
      color: var(--ink-700);
      letter-spacing: 0.02em;
    }
    .label-with-tip {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-width: auto;
    }
    .hint-tip {
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 1px solid rgba(19, 42, 78, 0.22);
      background: rgba(242, 247, 255, 0.76);
      color: #254b84;
      font-size: 11px;
      font-weight: 800;
      line-height: 16px;
      text-align: center;
      cursor: help;
      user-select: none;
    }
    .tip-bubble {
      position: absolute;
      top: calc(100% + 8px);
      left: 0;
      width: 260px;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid rgba(255, 255, 255, 0.44);
      background: rgba(19, 28, 44, 0.88);
      color: #eaf1ff;
      font-size: 12px;
      line-height: 1.45;
      letter-spacing: 0;
      box-shadow: 0 12px 24px rgba(8, 12, 20, 0.34);
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transform: translateY(-4px);
      transition: opacity 140ms ease, transform 140ms ease;
      z-index: 5;
    }
    .label-with-tip:hover .tip-bubble,
    .label-with-tip:focus-within .tip-bubble {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }
    input[type="text"],
    input[type="number"],
    select,
    textarea {
      width: 100%;
      border: 1px solid rgba(255, 255, 255, 0.54);
      border-radius: 12px;
      padding: 10px 12px;
      font-size: 14px;
      color: var(--ink-900);
      background: rgba(246, 249, 253, 0.72);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.58);
      transition: border-color 160ms ease, box-shadow 160ms ease, background-color 160ms ease;
    }
    input:focus,
    select:focus,
    textarea:focus {
      outline: none;
      border-color: rgba(19, 104, 218, 0.56);
      background: rgba(252, 254, 255, 0.9);
      box-shadow: 0 0 0 3px rgba(19, 104, 218, 0.18);
    }
    textarea {
      min-height: 210px;
      resize: vertical;
      line-height: 1.6;
    }
    .grow {
      flex: 1;
      min-width: 240px;
    }
    .meta-panel {
      margin: 14px 0 12px;
      border: 1px solid rgba(255, 255, 255, 0.38);
      border-radius: 14px;
      background: rgba(246, 249, 252, 0.48);
      padding: 12px 12px 2px;
    }
    .field {
      display: grid;
      grid-template-columns: 90px 1fr;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
    }
    .field-inline {
      display: grid;
      grid-template-columns: 82px 140px 62px 112px 62px 112px;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
    }
    button {
      border: 0;
      border-radius: 12px;
      padding: 11px 16px;
      font-size: 14px;
      cursor: pointer;
      font-weight: 700;
      background: linear-gradient(120deg, var(--accent), #2e7be1);
      color: #fff;
      box-shadow: 0 14px 28px rgba(19, 104, 218, 0.32);
      transition: transform 140ms ease, box-shadow 140ms ease, opacity 140ms ease;
    }
    button:hover {
      transform: translateY(-1px);
      box-shadow: 0 18px 32px rgba(19, 104, 218, 0.34);
    }
    button.secondary {
      background: linear-gradient(120deg, #edf3ff, #e5edfb);
      color: #1f437c;
      border: 1px solid #cadafb;
      box-shadow: none;
    }
    button.secondary:hover {
      box-shadow: 0 8px 18px rgba(17, 49, 96, 0.16);
    }
    button:disabled {
      opacity: 0.58;
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
    }
    .checks {
      display: flex;
      flex-wrap: wrap;
      gap: 18px;
      font-size: 14px;
      color: var(--ink-700);
      margin-bottom: 10px;
    }
    .checks label {
      min-width: auto;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 7px;
      letter-spacing: 0.01em;
    }
    .checks input {
      accent-color: #1b63c0;
    }
    pre {
      margin: 0;
      background: rgba(14, 20, 30, 0.84);
      color: #e8edf6;
      border-radius: 14px;
      border: 1px solid rgba(147, 164, 187, 0.34);
      padding: 13px 14px;
      min-height: 190px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.5;
      box-shadow: inset 0 0 28px rgba(27, 35, 50, 0.5);
    }
    .actions {
      margin-top: 8px;
      margin-bottom: 12px;
    }
    .tagline {
      margin-top: 10px;
      margin-bottom: 0;
      color: var(--ink-500);
      font-size: 12px;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }
    @keyframes riseIn {
      from { opacity: 0; transform: translateY(16px) scale(0.985); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    @keyframes driftA {
      from { transform: translate(0, 0) scale(1); }
      to { transform: translate(30px, 24px) scale(1.08); }
    }
    @keyframes driftB {
      from { transform: translate(0, 0) scale(1); }
      to { transform: translate(-34px, 22px) scale(0.92); }
    }
    @keyframes driftC {
      from { transform: translate(0, 0) scale(1); }
      to { transform: translate(32px, -20px) scale(1.06); }
    }
    @media (max-width: 920px) {
      .field-inline {
        grid-template-columns: 78px 1fr 60px 100px 60px 100px;
      }
    }
    @media (max-width: 780px) {
      .card {
        padding: 20px 16px 16px;
        border-radius: 18px;
      }
      .field {
        grid-template-columns: 1fr;
        gap: 6px;
      }
      .field-inline {
        grid-template-columns: 1fr 1fr;
        gap: 10px;
      }
      .field-inline label:nth-child(1),
      .field-inline label:nth-child(3),
      .field-inline label:nth-child(5) {
        margin-top: 2px;
      }
      .row { flex-direction: column; align-items: stretch; }
      label { min-width: auto; }
    }
  </style>
</head>
<body>
  <canvas id="inkCanvas"></canvas>
  <div class="mist mist-a"></div>
  <div class="mist mist-b"></div>
  <div class="mist mist-c"></div>
  <div class="wrap">
    <div class="card">
      <div class="hero">
        <p class="eyebrow">Ink Glass Workflow</p>
        <h1>论文批量下载器</h1>
        <p class="muted">从混乱引用到整洁本地 PDF：支持整段粘贴、自动解析和多源下载，优先级固定为 <strong>arXiv -> IEEE -> Scholar/门户(Elsevier/ACS/CNS) -> ORCID -> major sites</strong>。</p>
      </div>

      <div class="meta-panel">
        <div class="field">
          <label>下载目录</label>
          <div class="row">
            <input id="downloadDir" class="grow" type="text" placeholder="例如 D:\\papers" />
            <button type="button" class="secondary" onclick="pickFolder()">选择文件夹</button>
          </div>
        </div>

        <div class="field-inline">
          <label>数据源</label>
          <select id="source">
            <option value="both">both (推荐)</option>
            <option value="arxiv">arxiv</option>
            <option value="ieee">ieee</option>
            <option value="scholar">scholar</option>
            <option value="orcid">orcid</option>
            <option value="major">major</option>
          </select>

          <label>并发</label>
          <input id="workers" type="number" min="1" max="16" value="4" />

          <label class="label-with-tip">阈值
            <span class="hint-tip" tabindex="0" title="阈值说明">?</span>
            <span class="tip-bubble">阈值是最低匹配分数（0~1）。越高越严格，误匹配更少；越低越宽松，但可能下载到不相关论文。</span>
          </label>
          <input id="minScore" type="number" min="0" max="1" step="0.05" value="0.9" />
        </div>

        <div class="checks">
          <label><input id="ieeeManualLogin" type="checkbox" /> IEEE 手动登录/验证码</label>
          <label><input id="ieeeHeadless" type="checkbox" /> IEEE 无头模式</label>
          <label><input id="scholarManualLogin" type="checkbox" /> Scholar/门户手动登录</label>
          <label><input id="scholarHeadless" type="checkbox" /> Scholar 无头模式</label>
        </div>
      </div>

      <div class="field">
        <label>论文输入</label>
        <textarea id="paperTitles" class="grow" placeholder="建议尽量粘贴完整论文标题，可显著提升匹配准确率。支持一行一个，也支持整段引用粘贴（例如 Liu et al., 2023 Wang et al., 2022）。"></textarea>
      </div>

      <div class="actions">
        <button id="runBtn" type="button" onclick="runDownload()">开始下载</button>
      </div>

      <pre id="logs">等待开始...</pre>
      <p class="tagline">Glassmorphism · Ink Particle · Local-First</p>
    </div>
  </div>

  <script>
    const canvas = document.getElementById("inkCanvas");
    const ctx = canvas.getContext("2d");
    const logsEl = document.getElementById("logs");
    const runBtn = document.getElementById("runBtn");
    const particles = [];

    function resizeCanvas() {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(window.innerWidth * dpr);
      canvas.height = Math.floor(window.innerHeight * dpr);
      canvas.style.width = window.innerWidth + "px";
      canvas.style.height = window.innerHeight + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function createParticle(seedX) {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const startX = typeof seedX === "number" ? seedX : Math.random() * w;
      return {
        x: startX,
        y: Math.random() * h,
        r: 0.8 + Math.random() * 2.6,
        vx: (-0.18 + Math.random() * 0.36),
        vy: (-0.14 + Math.random() * 0.28),
        alpha: 0.035 + Math.random() * 0.07
      };
    }

    function initParticles() {
      particles.length = 0;
      const count = Math.min(110, Math.max(54, Math.floor(window.innerWidth / 16)));
      for (let i = 0; i < count; i++) {
        particles.push(createParticle());
      }
    }

    function stepParticles() {
      const w = window.innerWidth;
      const h = window.innerHeight;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(22, 30, 42, 0.08)";
      ctx.fillRect(0, 0, w, h);

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;

        if (p.x < -12 || p.x > w + 12 || p.y < -12 || p.y > h + 12) {
          particles[i] = createParticle(Math.random() * w);
          continue;
        }

        ctx.beginPath();
        ctx.fillStyle = "rgba(16, 24, 37, " + p.alpha.toFixed(3) + ")";
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      requestAnimationFrame(stepParticles);
    }

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
        min_score: Number(document.getElementById("minScore").value || 0.9),
        ieee_manual_login: document.getElementById("ieeeManualLogin").checked,
        ieee_headless: document.getElementById("ieeeHeadless").checked,
        scholar_manual_login: document.getElementById("scholarManualLogin").checked,
        scholar_headless: document.getElementById("scholarHeadless").checked,
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

    resizeCanvas();
    initParticles();
    stepParticles();
    window.addEventListener("resize", () => {
      resizeCanvas();
      initParticles();
    });
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
    titles = paper_downloader.extract_paper_queries(raw_titles)
    if not titles:
        return jsonify({"ok": False, "error": "No paper titles provided.", "exit_code": 1}), 400

    download_dir = str(payload.get("download_dir", "")).strip()
    if not download_dir:
        return jsonify({"ok": False, "error": "Please choose a download folder.", "exit_code": 1}), 400

    source = str(payload.get("source", "both")).strip().lower()
    if source not in {"arxiv", "ieee", "scholar", "orcid", "major", "both"}:
        source = "both"

    try:
        workers = max(1, int(payload.get("workers", 4)))
    except Exception:
        workers = 4

    try:
        min_score = float(payload.get("min_score", 0.9))
    except Exception:
        min_score = 0.9
    min_score = min(1.0, max(0.0, min_score))

    ieee_manual_login = bool(payload.get("ieee_manual_login", False))
    ieee_headless = bool(payload.get("ieee_headless", False))
    scholar_manual_login = bool(payload.get("scholar_manual_login", False))
    scholar_headless = bool(payload.get("scholar_headless", False))

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
        scholar_manual_login=scholar_manual_login,
        scholar_headless=scholar_headless,
    )

    try:
        exit_code, logs = run_downloader(cli_args)
        parsed_preview = ", ".join(titles[:5])
        if len(titles) > 5:
            parsed_preview += ", ..."
        logs = (
            f"[WEB] Parsed {len(titles)} item(s): {parsed_preview}\n"
            f"{logs}"
        ).strip()
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
