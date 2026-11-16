from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import Distribution, find_packages, setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parent


class NativeDistribution(Distribution):
    def has_ext_modules(self) -> bool:
        return True


class NativeWheel(bdist_wheel):
    def get_tag(self) -> tuple[str, str, str]:
        _, _, platform = super().get_tag()
        # The ctypes library is platform-specific, but has no CPython ABI dependency.
        return "py3", "none", platform


class BuildPythonApi(build_py):
    def run(self) -> None:
        subprocess.run(
            # melee-brute: upstream's optimised, exactness-audited profile (upstream installs the -O0 build)
            ["make", "--no-print-directory", "python-release", f"PY={sys.executable}", f"-j{os.cpu_count()}"],
            cwd=ROOT,
            check=True,
        )
        super().run()
        destination = Path(self.build_lib) / "melee_sim" / "libmelee_core.so"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "build/melee_core/python-release/libmelee_core.so", destination)
        # Keep debug information in the build tree, outside the installed package.
        subprocess.run(["strip", "-S", str(destination)], check=True)


setup(
    packages=find_packages(include=("melee_sim", "melee_brute", "tools", "tools.data")),
    package_data={"melee_sim": ["libmelee_core.so"]},
    include_package_data=False,
    distclass=NativeDistribution,
    cmdclass={"build_py": BuildPythonApi, "bdist_wheel": NativeWheel},
)
