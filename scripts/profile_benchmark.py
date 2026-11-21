#!/usr/bin/env python3
"""Measure the unchanged pipeline in fresh processes; explain cumulative logs.

This utility neither edits the pipeline nor commits/pushes any files.
Native timings diagnose this host; they are not predictions of scorer timings.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STAGES = [
    ("links and dimensions", "读取关联关系、合并身份、读取商品和广告表"),
    ("event parse (invalid copies retained for dedup)", "读取事件、严格解析、预检查与用户编号查找"),
    ("global event dedup", "按事件 ID 全局去重"),
    ("click parse", "读取和解析广告点击"),
    ("sort, sessions, attribution, interval-union assists", "排序、会话、广告归因与助攻（合并项）"),
    ("binary results and dictionaries written", "写出剩余统计结果与字符串字典"),
]
OUTSIDE = "Python 协调、库加载、Parquet 转换和输出等（合并项）"


def parse_log(text):
    cumulative = []
    for key, label in STAGES:
        matches = re.findall(r"^" + re.escape(key) + r": ([0-9.eE+-]+) s elapsed\s*$", text, re.M)
        if len(matches) != 1:
            raise ValueError(f"日志应恰好包含一次完整运行，阶段 {key!r} 找到 {len(matches)} 次")
        cumulative.append(float(matches[0]))
    completed = re.findall(r"^complete: ([0-9.eE+-]+) s including Parquet\s*$", text, re.M)
    if len(completed) != 1:
        raise ValueError("没有找到唯一的 complete 行；程序可能没有成功结束")
    total = float(completed[0])
    previous = 0.0
    stages = []
    for (_, label), value in zip(STAGES, cumulative):
        if value < previous - 0.0001:
            raise ValueError("累计时间倒退，不能使用这份日志")
        stages.append({"name": label, "seconds": max(0, value - previous)})
        previous = value
    # complete is printed with only three decimal places. Permit its rounding.
    if total < previous - 0.001:
        raise ValueError("完整程序耗时小于 C++ 耗时，日志不一致")
    stages.append({"name": OUTSIDE, "seconds": max(0, total - previous)})
    return {"pipeline_seconds": total, "core_seconds": cumulative[-1], "stages": stages}


def describe(result, title):
    print("\n" + title, flush=True)
    total = result["pipeline_seconds"]
    print(f"完整程序内部计时：{total:.3f} 秒；C++ 内部计时：{result['core_seconds']:.3f} 秒")
    for item in result["stages"]:
        percent = 100 * item["seconds"] / total if total else 0
        print(f"  {item['seconds']:.3f} 秒（{percent:.1f}%）：{item['name']}")
    slowest = max(result["stages"], key=lambda v: v["seconds"])
    print("当前最大的计时项：" + slowest["name"])
    fraction = slowest["seconds"] / total if total else 0
    if 0 < fraction < .999:
        print(f"即使这一项耗时降为零，其余时间不变，整体最多也只能快约 {1/(1-fraction):.2f} 倍。")
    print("注意：合并项还需进一步拆分，不能据此把其中某个操作认定为唯一瓶颈。", flush=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    data = args.data.resolve()
    output = args.output.resolve()
    scratch = args.scratch.resolve()
    logdir = args.logdir.resolve()
    core = args.core.resolve()
    if args.repeat < 1 or args.repeat > 9 or args.repeat % 2 == 0:
        raise ValueError("repeat 必须是 1～9 的奇数")
    if not core.is_file() or not os.access(core, os.X_OK):
        raise ValueError(f"找不到可执行的核心程序：{core}，请先执行 make core")
    if not (data / "manifest.json").is_file():
        raise ValueError(f"找不到输入 manifest.json：{data}")
    if not 1 <= args.threads <= 64:
        raise ValueError("threads 必须在 1～64 之间")
    for path in [output, scratch, logdir]:
        path.mkdir(parents=True, exist_ok=True)
    for group in ["events", "clicks", "links"]:
        if not (data / group).is_dir():
            raise ValueError(f"缺少输入目录 {group}")
    manifest = json.loads((data / "manifest.json").read_text())
    info = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": platform.platform(), "architecture": platform.machine(),
        "python": sys.version, "threads_requested": args.threads,
        "runner_sha256": digest(ROOT / "run.py"), "core_sha256": digest(core),
        "source_sha256": digest(ROOT / "src/main.cpp"),
        "data": str(data), "manifest": manifest,
        "input_bytes": sum(p.stat().st_size for p in data.rglob("*.csv")),
        "note": "Native diagnostic run. No hard CPU/RAM container limits; disk caches may be warm. No scored performance claim.",
    }
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    info["git_head"] = git.stdout.strip() if git.returncode == 0 else None
    results = []
    for i in range(1, args.repeat + 1):
        env = dict(os.environ, DATA_DIR=str(data), OUTPUT_DIR=str(output),
                   SCRATCH_DIR=str(scratch), CORE_BIN=str(core), NCPU=str(args.threads))
        path = logdir / f"run-{i}.log"
        print(f"\n开始第 {i}/{args.repeat} 次独立进程运行；日志：{path}", flush=True)
        started = time.perf_counter()
        with path.open("w") as log:
            p = subprocess.Popen([sys.executable, "-u", str(ROOT / "run.py")],
                                 cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in p.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            status = p.wait()
        elapsed = time.perf_counter() - started
        if status:
            raise RuntimeError(f"第 {i} 次运行失败，退出码 {status}；查看 {path}")
        result = parse_log(path.read_text())
        result.update(run=i, subprocess_wall_seconds=elapsed, log=str(path))
        results.append(result)
        describe(result, f"第 {i} 次阶段分析")
        (logdir / f"run-{i}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    # Choose an actual run at the median internal pipeline time; do not add up
    # unrelated per-stage medians, which need not correspond to any real run.
    representative = sorted(results, key=lambda r: r["pipeline_seconds"])[len(results)//2]
    info["runs"] = results
    info["representative_run"] = representative["run"]
    info["median_pipeline_seconds"] = statistics.median(r["pipeline_seconds"] for r in results)
    report = logdir / "report.json"
    report.write_text(json.dumps(info, ensure_ascii=False, indent=2))
    describe(representative, "===== 中间一次运行的阶段分析 =====")
    print("各次完整程序耗时：" + ", ".join(f"{r['pipeline_seconds']:.3f}s" for r in results))
    print("完整报告：" + str(report))
    print("结果文件留在：" + str(output))
    print("这些时间只用于本机定位瓶颈，不能直接与排行榜比较。")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    summarize = sub.add_parser("summarize", help="分析已有的一次完整运行日志")
    summarize.add_argument("log", type=Path)
    benchmark = sub.add_parser("run", help="重复运行原程序，记录并分析阶段耗时")
    benchmark.add_argument("--data", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--scratch", type=Path, default=ROOT / "scratch")
    benchmark.add_argument("--logdir", type=Path, default=ROOT / "logs" / "profile")
    benchmark.add_argument("--core", type=Path, default=ROOT / "core")
    benchmark.add_argument("--repeat", type=int, default=3)
    benchmark.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    try:
        if args.command == "summarize":
            describe(parse_log(args.log.read_text()), "已有日志的阶段分析")
        else:
            run(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
