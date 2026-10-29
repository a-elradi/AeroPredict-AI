import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402

I18N = Path(__file__).resolve().parents[2] / "frontend" / "i18n"
LANGS = ["ar", "es", "tr"]


def load(lang):
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def test_languages_translate_the_same_text():
    keys = {lang: set(load(lang)) for lang in LANGS}
    assert keys["ar"] == keys["es"] == keys["tr"]
    assert len(keys["ar"]) > 500


@pytest.mark.parametrize("lang", LANGS)
def test_translations_keep_slots_and_links(lang):
    for en, tr in load(lang).items():
        assert tr.strip(), en
        assert sorted(re.findall(r"\{\d\}", en)) == sorted(re.findall(r"\{\d\}", tr)), (en, tr)
        for n in set(re.findall(r"<(\d+)>", en)):
            assert re.search(rf"<{n}>.*?</{n}>", tr), (en, tr)


def test_arabic_uses_western_digits():
    for en, tr in load("ar").items():
        assert not re.search("[٠-٩]", tr), (en, tr)


def test_language_files_are_served():
    client = create_app().test_client()
    for lang in LANGS:
        r = client.get(f"/i18n/{lang}.json")
        assert r.status_code == 200 and r.get_json()
    assert client.get("/i18n.js").status_code == 200
