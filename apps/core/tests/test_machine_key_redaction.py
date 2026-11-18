"""De redactie maskeert een machine-sleutel ook midden in een woord."""

import json

import pytest

from mso.audit import _clean
from mso.security import redact
from tests.test_machine_keys import create


@pytest.mark.parametrize("prefix", ["letter", "onder_", "1", "X-Api-Key%3D"])
def test_machine_key_redaction_after_word_character(admin, prefix):
    key = create(admin)["key"]
    message = prefix + key
    assert key not in redact(message)
    assert key not in json.dumps(_clean({"message": message}))
