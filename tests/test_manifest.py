"""Release metadata the HACS/hassfest checks care about."""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "eufy_clean_rooms"


def test_manifest_release_fields() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.2.1"
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == ["paho-mqtt>=2.0.0"]
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"] and keys[2:] == sorted(keys[2:])


def test_translations_match_strings() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
    assert strings == en


def test_hacs_json() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == "Eufy Clean Rooms" and hacs["render_readme"] is True and "homeassistant" in hacs


def test_readme_keeps_coffee_section() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Buy me a coffee" in readme and "buymeacoffee.com/ajachiernoo" in readme
