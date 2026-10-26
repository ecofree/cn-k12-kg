"""批量下载上海中考卷（Xnye/Smse），用 raw 端点避开 API 配额。

实测记录：
- git clone / codeload tarball 都过代理（502 / TLS 握手失败）
- raw.githubusercontent.com 可用，但会间歇性失败，需要重试
- api.github.com 的 git/blobs 也可用，但速率配额只有 60 次/小时，276 个文件跑不完

所以主路走 raw + 重试，blob API 作为兜底。
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import pathlib
import random
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DL = ROOT / "build" / "dl" / "smse_files"
TREE = ROOT / "build" / "dl" / "smse_tree.json"
RAW = "https://raw.githubusercontent.com/Xnye/Smse/main/"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cn-k12-kg/1.0"


def target_path(remote: str) -> pathlib.Path:
    # 保留目录结构，文件名安全化
    parts = remote.split("/")
    return DL.joinpath(*parts[:-1], parts[-1])


def fetch(remote: str, size: int, retries: int = 5) -> tuple[str, bool, str]:
    dest = target_path(remote)
    if dest.exists() and dest.stat().st_size == size:
        return remote, True, "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = RAW + urllib.parse.quote(remote)
    last = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            if len(data) != size:
                last = f"size {len(data)}!={size}"
                time.sleep(1.5 * (attempt + 1))
                continue
            dest.write_bytes(data)
            return remote, True, "ok"
        except Exception as exc:
            last = str(exc)[:80]
            # 代理抖动是常态，指数退避
            time.sleep(1.2 * (attempt + 1) + random.random())
    return remote, False, last


def main() -> None:
    tree = json.loads(TREE.read_text(encoding="utf-8"))["tree"]
    blobs = [
        x for x in tree
        if x["type"] == "blob" and x["path"].lower().endswith((".doc", ".docx"))
    ]
    DL.mkdir(parents=True, exist_ok=True)
    ok = fail = cached = 0
    fails = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch, x["path"], x["size"]): x for x in blobs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            remote, good, msg = fut.result()
            if good:
                if msg == "cached":
                    cached += 1
                else:
                    ok += 1
            else:
                fail += 1
                fails.append((remote, msg))
            if i % 20 == 0:
                print(f"[{i}/{len(blobs)}] ok={ok} cached={cached} fail={fail}", flush=True)
    print(json.dumps({
        "total": len(blobs), "downloaded": ok, "cached": cached,
        "failed": fail, "fail_samples": fails[:5],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
