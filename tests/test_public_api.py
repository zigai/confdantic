from pathlib import Path

import confdantic
from confdantic import Confdantic
from confdantic.confdantic import Confdantic as ModuleConfdantic


def test_root_export_is_the_canonical_model():
    assert confdantic.__all__ == ["Confdantic"]
    assert Confdantic is ModuleConfdantic


def test_typing_marker_is_packaged_with_the_module():
    assert Path(confdantic.__file__).with_name("py.typed").is_file()
