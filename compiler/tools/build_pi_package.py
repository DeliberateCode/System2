"""Build the Pi package from the backend's canonical project output."""

import json
import os
import shutil

from system2_compiler.channel_version import CHANNEL_VERSION

__all__ = ["build", "PACKAGE_NAME", "PACKAGE_VERSION"]

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_TOOLS_DIR, "templates")
_REPO_ROOT = os.path.dirname(os.path.dirname(_TOOLS_DIR))

PACKAGE_NAME = "@deliberatecode/system2"
PACKAGE_VERSION = CHANNEL_VERSION


def build(staging_emission, dest, package_version=PACKAGE_VERSION):
    if package_version != PACKAGE_VERSION:
        raise ValueError(
            f"Pi package version mismatch: requested {package_version!r}, "
            f"expected {PACKAGE_VERSION!r}"
        )
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    for resource in ("prompts", "skills"):
        source = os.path.join(staging_emission, ".pi", resource)
        if not os.path.isdir(source):
            raise FileNotFoundError(f"Pi emission is missing {source}")
        shutil.copytree(source, os.path.join(dest, resource))

    shutil.copytree(os.path.join(_TEMPLATES_DIR, "pi_subagents"), os.path.join(dest, "subagents"))

    package = json.loads(_read_template("pi_package.json"))
    package["version"] = package_version
    with open(os.path.join(dest, "package.json"), "w", encoding="utf-8") as stream:
        json.dump(package, stream, indent=2)
        stream.write("\n")

    readme = _read_template("pi_readme.md.template").replace(
        "__PACKAGE_NAME__", PACKAGE_NAME
    )
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as stream:
        stream.write(readme)
    shutil.copyfile(os.path.join(_REPO_ROOT, "LICENSE"), os.path.join(dest, "LICENSE"))


def _read_template(name):
    with open(os.path.join(_TEMPLATES_DIR, name), "r", encoding="utf-8") as stream:
        return stream.read()
