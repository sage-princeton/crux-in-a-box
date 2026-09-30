from pathlib import Path

import pytest

from drop_ins import BASE_FILES, write_tree


@pytest.fixture
def drop_in_dir(tmp_path: Path) -> Path:
    return write_tree(tmp_path / "drop-in", BASE_FILES)
