"""Hatch build hook: write the commit this wheel is built from into desk/_build.py inside the wheel."""

import subprocess
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip()
        except OSError:
            commit = ""
        out = Path(tempfile.mkdtemp()) / "_build.py"
        out.write_text(f'COMMIT = "{commit or "no git"}"\n', encoding="utf-8")
        build_data["force_include"][str(out)] = "desk/_build.py"
        # The About page's fallback text is the repo's own docs/WHY.md, copied into the wheel here, never committed twice.
        why = Path(self.root) / "docs" / "WHY.md"
        if why.is_file():
            build_data["force_include"][str(why)] = "desk/ui/content/WHY.md"
