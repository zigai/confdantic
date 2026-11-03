import confdantic
from confdantic import Confdantic
from confdantic.confdantic import Confdantic as ModuleConfdantic


def test_root_export_is_the_canonical_model():
    expected = {
        "Confdantic",
        "config_field",
        "Duration",
        "parse_duration",
        "load",
        "load_with_metadata",
        "Contribution",
        "env_vars",
        "EnvVar",
        "config_edit_path",
        "Metadata",
        "Origin",
        "SourceKind",
        "UnknownKey",
        "save",
        "init",
        "schema",
        "edit",
        "set_key",
        "set_keys",
        "set_bytes",
        "Format",
        "ConfdanticError",
        "ConfigError",
        "ConfigValidationError",
        "UnknownKeyError",
        "InvalidEnvironmentValueError",
        "MalformedDocumentError",
        "UnsupportedFormatError",
        "NoFormatsError",
        "FileTooLargeError",
        "InvalidKeyPathError",
        "KeyPath",
        "MultipleDocumentsError",
    }
    assert set(confdantic.__all__) == expected
    assert Confdantic is ModuleConfdantic
