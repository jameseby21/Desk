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
        # The same commit as a stamp next to the templates, and as a comment at the end of every template, so a running
        # process can tell when the files on disk are newer than the code it imported (see desk/buildstamp.py).
        stamp = out.parent / ".build-stamp"
        stamp.write_text((commit or "no git") + "\n", encoding="utf-8")
        build_data["force_include"][str(stamp)] = "desk/ui/templates/.build-stamp"
        for tpl in sorted((Path(self.root) / "src" / "desk" / "ui" / "templates").glob("*.html")):
            copy = out.parent / tpl.name
            copy.write_text(tpl.read_text(encoding="utf-8").rstrip() + f"\n<!-- desk build {commit or 'no git'} -->\n", encoding="utf-8")
            build_data["force_include"][str(copy)] = f"desk/ui/templates/{tpl.name}"
        # The About page's fallback text is the repo's own docs/WHY.md, copied into the wheel here, never committed twice.
        why = Path(self.root) / "docs" / "WHY.md"
        if why.is_file():
            build_data["force_include"][str(why)] = "desk/ui/content/WHY.md"
