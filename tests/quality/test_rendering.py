"""The renderer's packaging and its jobs (decision-42): licences, Windows job, image smoke test."""

import importlib.metadata
from pathlib import Path
from typing import cast

import pytest
import yaml

pytestmark = pytest.mark.decision("decision-42")

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


@pytest.mark.parametrize("package", ["pypdfium2", "pillow"])
def test_the_licence_texts_come_with_the_package(package: str) -> None:
    """The image copies the whole environment, so they stay in it."""
    files = importlib.metadata.files(package) or []

    texts = [file for file in files if "licenses/" in file.as_posix().lower()]
    assert texts
    assert all(Path(str(text.locate())).stat().st_size > 0 for text in texts)


def test_the_renderer_is_tested_on_windows() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    workflow = cast("dict[str, dict[str, dict[str, object]]]", yaml.safe_load(text))
    job = workflow["jobs"]["rendering-windows"]

    assert job["runs-on"] == "windows-latest"
    assert "if" not in job
    assert "uv run pytest tests/rendering tests/services/test_pages.py --no-cov" in str(
        job["steps"]
    )


@pytest.mark.parametrize("script", ["image_smoke.sh", "image_stdio_smoke.sh"])
def test_both_images_render_a_page_before_they_pass(script: str) -> None:
    text = (SCRIPTS / script).read_text(encoding="utf-8")

    call = text.index('"$(dirname "$0")/image_render_smoke.sh" "$image"')
    assert call < text.index('smoke test passed"')


def test_the_image_renders_under_its_restrictions() -> None:
    text = (SCRIPTS / "image_render_smoke.sh").read_text(encoding="utf-8")

    for option in ("--read-only", "--cap-drop ALL", "no-new-privileges", "--network none"):
        assert option in text
    assert "os.getuid() == 10001" in text
    assert "set -euo pipefail" in text
    assert text.count("docker run --rm -i --read-only --cap-drop ALL") == 2
    assert "guard(2**30, 30.0)" in text
    assert 'raise SystemExit("a process under the guard opened something")' in text
