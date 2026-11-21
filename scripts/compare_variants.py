#!/usr/bin/env python3
"""Alternate original and candidate native executables on identical input.

Warm-up runs are excluded. Every measured run starts a fresh Python/C++ process.
Only the final valid outputs of each variant are compared with the official
order-insensitive comparator; this is not an independent hidden-data oracle.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

from profile_benchmark import ROOT, parse_log, describe


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--old-core", type=Path, default=ROOT / "core-v1-baseline")
    p.add_argument("--new-core", type=Path, default=ROOT / "core")
    p.add_argument("--output", type=Path, default=ROOT / "bench-output" / "lookup-v2")
    p.add_argument("--scratch", type=Path, default=ROOT / "scratch")
    p.add_argument("--logdir", type=Path, default=ROOT / "logs" / "lookup-v2-ab")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--no-warmup", action="store_true")
    args = p.parse_args()
    try:
        if args.repeat < 1 or args.repeat > 9 or args.repeat % 2 == 0:
            raise ValueError("repeat 必须是 1～9 的奇数")
        if not 1 <= args.threads <= 64:
            raise ValueError("threads 必须在 1～64 之间")
        data, output, scratch, logs = [v.resolve() for v in [args.data, args.output, args.scratch, args.logdir]]
        variants = {"v1": args.old_core.resolve(), "v2-flat": args.new_core.resolve()}
        for name, core in variants.items():
            if not core.is_file() or not os.access(core, os.X_OK):
                raise ValueError(f"{name} 缺少可执行文件：{core}")
        if not (data / "manifest.json").is_file():
            raise ValueError(f"数据不存在：{data}")
        for folder in [output, scratch, logs]:
            folder.mkdir(parents=True, exist_ok=True)
        all_runs = []
        metadata = {
            "data": str(data), "threads": args.threads,
            "runner_sha256": sha(ROOT / "run.py"),
            "executables_sha256": {k: sha(v) for k, v in variants.items()},
            "note": "Local native comparison, not a scoring run. CPU/RAM are not container-enforced.",
        }

        def once(name, number, warmup):
            core = variants[name]
            env = dict(os.environ, DATA_DIR=str(data), OUTPUT_DIR=str(output / name),
                       SCRATCH_DIR=str(scratch), CORE_BIN=str(core), NCPU=str(args.threads))
            env.pop("SHOPPING_LOOKUP", None)
            if name == "v2-flat":
                env["SHOPPING_LOOKUP"] = "flat"
            label = "warmup" if warmup else f"run-{number}"
            path = logs / f"{name}-{label}.log"
            print(f"\n[{name} / {label}]", flush=True)
            started = time.perf_counter()
            with path.open("w") as log:
                proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "run.py")],
                                        cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
                for line in proc.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                code = proc.wait()
            wall = time.perf_counter() - started
            if code:
                raise RuntimeError(f"{name} 退出码 {code}，查看 {path}")
            result = parse_log(path.read_text())
            result.update(variant=name, run=number, warmup=warmup, subprocess_wall_seconds=wall)
            all_runs.append(result)
            (logs / "progress.json").write_text(json.dumps({**metadata, "runs": all_runs}, ensure_ascii=False, indent=2))

        if not args.no_warmup:
            once("v1", 0, True)
            once("v2-flat", 0, True)
        for i in range(1, args.repeat + 1):
            order = ["v1", "v2-flat"] if i % 2 else ["v2-flat", "v1"]
            for name in order:
                once(name, i, False)

        print("\n===== 使用官方比较器核对两套完整输出 =====", flush=True)
        compare = subprocess.run([sys.executable, str(ROOT / "scripts" / "compare.py"),
                                  str(output / "v1"), str(output / "v2-flat")],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(compare.stdout, end="", flush=True)
        (logs / "output-comparison.log").write_text(compare.stdout)
        metadata["outputs_match"] = compare.returncode == 0
        if compare.returncode:
            (logs / "report.json").write_text(json.dumps({**metadata, "runs": all_runs}, ensure_ascii=False, indent=2))
            raise RuntimeError("新旧输出不同：停止采用候选版本，先修复正确性")

        medians = {}
        for name in variants:
            runs = [r for r in all_runs if r["variant"] == name and not r["warmup"]]
            representative = sorted(runs, key=lambda r: r["pipeline_seconds"])[len(runs)//2]
            medians[name] = statistics.median(r["pipeline_seconds"] for r in runs)
            describe(representative, "===== " + name + " 中间一次运行 =====")
        ratio = medians["v1"] / medians["v2-flat"]
        reduction = 100 * (1 - medians["v2-flat"] / medians["v1"])
        metadata.update(runs=all_runs, median_pipeline_seconds=medians,
                        speed_ratio_old_div_new=ratio, runtime_reduction_percent=reduction)
        (logs / "report.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        print("\n===== 最终 A/B 对比 =====")
        print(f"旧版中位耗时：{medians['v1']:.3f} 秒")
        print(f"新版中位耗时：{medians['v2-flat']:.3f} 秒")
        print(f"本机速度比（旧÷新）：{ratio:.3f} 倍；耗时降低：{reduction:.1f}%")
        print("两套完整输出：MATCH")
        print("报告：" + str(logs / "report.json"))
        print("该结果仅比较本机同数据的两个实现，正式成绩仍由评测器决定。")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
