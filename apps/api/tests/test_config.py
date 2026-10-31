"""Settings defaults that depend on where the code runs (checkout vs. the API image)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

import app.core.config as config_module
from app.core.config import CONTAINER_CONTENT_DIR, Settings, default_content_dir


def test_docker_image_layout_falls_back_to_the_mount_point() -> None:
    """The API image copies apps/api to /app, so the module is /app/app/core/config.py.

    Regression: the old default took parents[4] of that path and raised IndexError while
    Alembic imported the settings inside the container.
    """
    assert default_content_dir(PurePosixPath("/app/app/core/config.py")).as_posix() == "/content"


@pytest.mark.parametrize(
    "module_file",
    [
        PurePosixPath("/config.py"),
        PurePosixPath("/core/config.py"),
        # An unrelated deep location must not pick some random ancestor's content/.
        PurePosixPath("/usr/lib/python3.12/site-packages/app/core/config.py"),
    ],
)
def test_other_layouts_never_raise_and_use_the_mount_point(module_file: PurePosixPath) -> None:
    assert default_content_dir(module_file).as_posix() == "/content"


@pytest.mark.parametrize(
    ("module_file", "expected"),
    [
        (
            PurePosixPath("/srv/checkout/apps/api/app/core/config.py"),
            PurePosixPath("/srv/checkout/content"),
        ),
        (
            PureWindowsPath(r"D:\src\KyrgyzSTEM\apps\api\app\core\config.py"),
            PureWindowsPath(r"D:\src\KyrgyzSTEM\content"),
        ),
    ],
)
def test_checkout_layout_uses_the_repository_content(
    module_file: PurePosixPath | PureWindowsPath, expected: PurePosixPath | PureWindowsPath
) -> None:
    assert default_content_dir(module_file) == expected


def test_this_checkout_resolves_to_the_real_content_directory() -> None:
    content = Settings(_env_file=None).content_dir
    assert content == Path(default_content_dir())
    assert (content / "curriculum.toml").is_file()
    assert (content / "problems").is_dir()


def test_content_dir_environment_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_DIR", str(CONTAINER_CONTENT_DIR))
    assert Settings(_env_file=None).content_dir == CONTAINER_CONTENT_DIR


def test_settings_import_in_an_image_like_layout(tmp_path: Path) -> None:
    """Import the real config module from a copy laid out like the image (<root>/app/core).

    No CONTENT_DIR and no repository around it: importing must not fail, and the default
    must be the container mount point (docker-compose mounts content/ there).
    """
    package = tmp_path / "app"
    (package / "core").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(config_module.__file__, package / "core" / "config.py")

    env = {k: v for k, v in os.environ.items() if k != "CONTENT_DIR"}
    env["PYTHONPATH"] = str(tmp_path)
    code = (
        "from app.core.config import Settings; "
        "print(Settings(_env_file=None).content_dir.as_posix())"
    )
    result = subprocess.run(  # noqa: S603 - fixed code, our own interpreter
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/content"
