"""批量用 LibreOffice 把上海中考 .doc 转成文本。

踩过的坑：
1. Windows 上必须用 soffice.com（soffice.exe 会脱离控制台，拿不到输出与退出码）
2. 每次启动 soffice 进程要 2-4 秒，300+ 文件必须批处理，不能一个一个调
3. soffice 不能并发跑同一个 user profile，要用 -env:UserInstallation 给每个进程独立 profile
4. 转换后的 txt 是 UTF-8 带 BOM，读取时要剥掉
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "build" / "dl" / "smse_files"
OUT = ROOT / "build" / "dl" / "smse_txt"

SOFFICE = r"D:\Program Files\LibreOffice\program\soffice.com"


def find_soffice() -> str:
    cands = [
        SOFFICE,
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
    ]
    for c in cands:
        if pathlib.Path(c).exists():
            return c
    raise SystemExit("找不到 soffice.com，请确认 LibreOffice 安装路径")


def convert_batch(files: list[pathlib.Path], soffice: str, profile: str) -> tuple[int, int]:
    """一次 soffice 调用转换多个文件（soffice 支持多个输入文件）。"""
    args = [
        soffice, "--headless", "--norestore", "--invisible",
        f"-env:UserInstallation=file:///{profile.replace(chr(92), '/')}",
        "--convert-to", "txt:Text (encoded):UTF8",
        "--outdir", str(OUT),
    ] + [str(f) for f in files]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=900)
    return proc.returncode, len(files)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    soffice = find_soffice()
    OUT.mkdir(parents=True, exist_ok=True)

    docs = sorted([p for p in SRC.rglob("*.doc")] + [p for p in SRC.rglob("*.docx")])
    if args.limit:
        docs = docs[: args.limit]
    print(f"待转换 {len(docs)} 个文件")

    done = 0
    for i in range(0, len(docs), args.batch):
        chunk = docs[i: i + args.batch]
        profile = ROOT / "build" / "dl" / "lo_profile" / f"p{i}"
        profile.mkdir(parents=True, exist_ok=True)
        try:
            rc, n = convert_batch(chunk, soffice, str(profile))
        except subprocess.TimeoutExpired:
            print(f"  批次 {i} 超时，跳过")
            continue
        done += n
        print(f"  [{done}/{len(docs)}] rc={rc}", flush=True)

    # 核对
    txts = list(OUT.rglob("*.txt"))
    print(json.dumps({"docs": len(docs), "txt_out": len(txts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
