#!/usr/bin/env python3
"""Use the Arrow C++ SDK bundled in the pinned PyArrow wheel at build time."""
import os
from pathlib import Path
import platform
import shlex
import subprocess
import tempfile
import pyarrow as pa

root = Path(__file__).resolve().parents[1]
pa.create_library_symlinks()
library = Path(pa.__file__).resolve().parent
output = Path(os.environ.get("NATIVE_BIN", root / "core-native"))
# Wheels have historically used different libstdc++ string ABIs. Probe the
# actual bundled SDK instead of assuming the host compiler's default matches.
abi_flags=[]
if platform.system()=="Linux":
    with tempfile.TemporaryDirectory() as tmp:
        probe=Path(tmp)/"abi.cpp"
        probe.write_text('#include <arrow/api.h>\nint main(){auto f=arrow::field(std::string("probe"),arrow::int64());return f->ToString().empty();}\n')
        errors=[]
        for option in [[],["-D_GLIBCXX_USE_CXX11_ABI=0"]]:
            check=shlex.split(os.environ.get("CXX","c++"))+["-std=c++20",*option,
                "-I"+pa.get_include(),str(probe),"-L"+str(library),"-Wl,-rpath,"+str(library),
                "-larrow","-o",str(Path(tmp)/"abi")]
            result=subprocess.run(check,capture_output=True,text=True)
            if result.returncode==0:
                subprocess.run([str(Path(tmp)/"abi")],check=True)
                abi_flags=option;break
            errors.append(result.stderr)
        else:raise RuntimeError("Arrow C++ SDK ABI link probe failed:\n"+"\n".join(errors))
cmd = shlex.split(os.environ.get("CXX", "c++")) + [
    "-O3", "-DNDEBUG", "-std=c++20", "-pthread", "-DSHOPPING_NATIVE_PARQUET", *abi_flags,
    "-I" + pa.get_include(), str(root / "src/main.cpp"),
    "-L" + str(library), "-Wl,-rpath," + str(library),
    "-lparquet", "-larrow", "-o", str(output),
]
if platform.machine().lower() in {"x86_64", "amd64"}:
    cmd[1:1] = ["-march=x86-64-v3"]
print("Compiling native C++ Parquet pipeline", flush=True)
subprocess.run(cmd, check=True)
subprocess.run([str(output.resolve()), "--self-test"], check=True)
