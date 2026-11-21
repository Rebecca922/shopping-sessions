#!/usr/bin/env python3
"""One final candidate: build, verify, compare containers, commit and print SHA.

All correctness checks must pass before commit/push. Existing scored commits and
the existing v2 image are retained. No website submission is performed here.
"""
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OLD="shopping-sessions:v2-lookup"
NEW="shopping-sessions:v3-final"


def run(cmd,env=None,log=None):
    print("\n$ "+" ".join(str(v) for v in cmd),flush=True)
    handle=log.open("w") if log else None
    start=time.perf_counter()
    try:
        proc=subprocess.Popen([str(v) for v in cmd],cwd=ROOT,env=env,
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in proc.stdout:
            print(line,end="",flush=True)
            if handle:handle.write(line)
        status=proc.wait()
        if status:raise RuntimeError(f"命令退出码 {status}"+(f"，日志 {log}" if log else ""))
        return time.perf_counter()-start
    finally:
        if handle:handle.close()


def main():
    logs=ROOT/"logs/final-v3";logs.mkdir(parents=True,exist_ok=True)
    output=ROOT/"bench-output/final-v3";output.mkdir(parents=True,exist_ok=True)
    data=ROOT/"bench-data/seed17-20m"
    try:
        for required in [ROOT/"dev/manifest.json",ROOT/"dev-answer/user_daily.csv",data/"manifest.json"]:
            if not required.is_file():raise RuntimeError(f"缺少已有测试数据：{required}")
        run(["docker","info"],log=logs/"docker-info.log")
        run(["docker","image","inspect",OLD],log=logs/"baseline-image.json")
        run(["docker","buildx","build","--platform","linux/amd64","--load","-t",NEW,"."],log=logs/"build.log")
        env=dict(os.environ,IMAGE=NEW,PLATFORM="linux/amd64",CPUS="8",MEM="24g")
        run([sys.executable,"scripts/run_fixtures.py","fixtures","--","sh","scripts/docker_run.sh"],env,logs/"fixtures.log")
        run(["sh","scripts/docker_run.sh",ROOT/"dev",output/"dev"],env,logs/"dev.log")
        run([sys.executable,"scripts/compare.py",output/"dev",ROOT/"dev-answer"],log=logs/"dev-comparison.log")
        records=[]
        def once(image,label,warmup):
            name="v2" if image==OLD else "v3"
            elapsed=run(["sh","scripts/docker_run.sh",data,output/name],
                        dict(env,IMAGE=image),logs/f"{name}-{label}.log")
            records.append({"variant":name,"seconds":elapsed,"warmup":warmup})
            print(f"{name} / {label} 外层命令耗时：{elapsed:.3f} 秒",flush=True)
        once(OLD,"warmup",True);once(NEW,"warmup",True)
        for i in range(1,4):
            for image in ([OLD,NEW] if i%2 else [NEW,OLD]):once(image,f"run-{i}",False)
        run([sys.executable,"scripts/compare.py",output/"v3",output/"v2"],log=logs/"20m-comparison.log")
        med={v:statistics.median(r["seconds"] for r in records if r["variant"]==v and not r["warmup"]) for v in ["v2","v3"]}
        (logs/"report.json").write_text(json.dumps({"runs":records,"median_outer_command_seconds":med,
            "outputs_match":True,"note":"Includes local Docker CLI/container overhead; not official scorer timing.",
            "source_sha256":hashlib.sha256((ROOT/"src/main.cpp").read_bytes()).hexdigest()},indent=2))
        print(f"\n完整输出 MATCH。旧版容器 {med['v2']:.3f}s，新版容器 {med['v3']:.3f}s；本机速度比 {med['v2']/med['v3']:.3f}。",flush=True)
        print("本机 amd64 模拟不能代替 Intel 评测；以下保存通过正确性检查的候选提交。",flush=True)
        remote=subprocess.check_output(["git","remote","get-url","origin"],cwd=ROOT,text=True).strip().rstrip("/")
        if remote not in {"https://github.com/Rebecca922/shopping-sessions","https://github.com/Rebecca922/shopping-sessions.git","git@github.com:Rebecca922/shopping-sessions.git"}:
            raise RuntimeError(f"origin 不是预期仓库，未推送：{remote}")
        run(["git","add","--","Dockerfile","src/main.cpp","src/native_parquet.h",
             "scripts/build_native.py","scripts/smoke_native.py","scripts/final_attempt.py",
             "FINAL_NOTES_zh.md",".gitignore",".dockerignore"])
        run(["git","--no-pager","diff","--cached","--stat"])
        if subprocess.run(["git","diff","--cached","--quiet"],cwd=ROOT).returncode:
            run(["git","commit","-m","Add native Parquet output and cache-aware SIMD parsing"])
        run(["git","push","origin","HEAD"])
        sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
        print("\n===== 可提交的最后候选版本 =====")
        print("仓库：https://github.com/Rebecca922/shopping-sessions")
        print("Commit："+sha)
        print("到 Databricks 页面填写该 Commit 并提交。保留现有 5.51 秒版本，最终采用正式评测中较快的正确提交。")
        return 0
    except (OSError,RuntimeError,subprocess.CalledProcessError) as error:
        print("\n已停止："+str(error),file=sys.stderr)
        print("现有比赛提交保持不变。请发送 logs/final-v3 中对应日志的最后 40 行。",file=sys.stderr)
        return 1


if __name__=="__main__":sys.exit(main())
