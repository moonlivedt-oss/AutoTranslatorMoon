"""Ядро: определение языка, нарезка текста, кэш, ретраи, конфиг, разметка Ren'Py."""

import json
import threading

import pytest
from conftest import FakeEngine

from autotranslator.core import config, renpy_text, translate
from autotranslator.core.cache import TranslationCache
from autotranslator.core.langs import detect_lang


# ---------- языки ----------
@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("こんにちは世界", "ja"),
        ("안녕하세요", "ko"),
        ("你好世界", "zh-CN"),
        ("Привет, как дела? Это ёжик", "ru"),
        ("Привіт, як справи? Їжак і ґанок", "uk"),
        ("The cat is on the table and it is happy", "en"),
        ("Der Hund ist nicht in dem Haus, für mich", "de"),
        ("El niño y la casa de los padres, ¿por qué?", "es"),
        ("", "en"),
    ],
)
def test_detect_lang(text, lang):
    assert detect_lang(text) == lang


# ---------- нарезка длинного текста ----------
def test_split_chunks_respects_limit_and_keeps_text():
    para = "Слово. " * 400  # ~2800 символов
    text = "\n".join([para] * 5)
    chunks = translate.split_chunks(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace(
        "\n", ""
    ).replace(" ", "")


def test_split_chunks_short_text_untouched():
    assert translate.split_chunks("abc") == ["abc"]


# ---------- кэш ----------
def test_cache_lru_eviction():
    c = TranslationCache(max_items=2)
    c.put(("o", "en", "ru", "a"), "А")
    c.put(("o", "en", "ru", "b"), "Б")
    c.get(("o", "en", "ru", "a"))  # «a» стал свежим
    c.put(("o", "en", "ru", "c"), "В")
    assert c.get(("o", "en", "ru", "b")) is None
    assert c.get(("o", "en", "ru", "a")) == "А"


def test_cache_disk_roundtrip(tmp_path):
    path = tmp_path / "cache.json"
    c = TranslationCache()
    c.put(("online", "en", "ru", "hello"), "привет")
    assert c.save(path)
    assert not c.save(path)  # не менялся — не пишем
    d = TranslationCache()
    assert d.load(path) == 1
    assert d.get(("online", "en", "ru", "hello")) == "привет"


def test_cache_ignores_broken_file(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{oops", encoding="utf-8")
    assert TranslationCache().load(path) == 0
    path.write_text(json.dumps([["x"], 5, ["a", "b", "c", "d", "e"]]), encoding="utf-8")
    c = TranslationCache()
    assert c.load(path) == 1


def test_cache_thread_safe():
    c = TranslationCache(max_items=50)
    errors = []

    def work(n):
        try:
            for i in range(2000):
                k = ("o", "en", "ru", str((n * 7 + i) % 120))
                c.put(k, "x")
                c.get(k)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(c) <= 50


# ---------- онлайн-движок: ретраи и сетевые ошибки ----------
def test_online_retries_then_network_error(monkeypatch):
    pytest.importorskip("requests")
    eng = translate.OnlineEngine()
    calls = []

    def boom(*_a):
        calls.append(1)
        raise ConnectionError("connection refused")

    monkeypatch.setattr(eng, "_once", boom)
    monkeypatch.setattr(translate.time, "sleep", lambda _s: None)
    with pytest.raises(translate.NetworkError, match="Нет сети"):
        eng.translate("retry-test-unique", "en", "ru")
    assert len(calls) == translate.RETRIES


def test_online_recovers_after_blip(monkeypatch):
    pytest.importorskip("requests")
    eng = translate.OnlineEngine()
    state = {"n": 0}

    def flaky(text, *_a):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("timed out")
        return "ok:" + text

    monkeypatch.setattr(eng, "_once", flaky)
    monkeypatch.setattr(translate.time, "sleep", lambda _s: None)
    assert eng.translate("blip-test-unique", "en", "ru") == "ok:blip-test-unique"


# ---------- конфиг ----------
def test_config_migrates_old_direction():
    data = config._migrate({"schema": 1, "direction": "ru->en", "ocr_lang": "ru"})
    assert (data["source"], data["target"], data["schema"]) == ("ru", "en", 2)
    assert "ocr_lang" not in data


def test_config_sanitize_fixes_bad_values():
    d = dict(config.DEFAULTS)
    d.update(
        opacity="70",
        font_scale=999,
        engine="quantum",
        region=[1, 2, "x", 4],
        history=[{"src": 1, "dst": "a"}, "junk"],
        autocopy=1,
        hotkeys={"lens": "f9", "bad": 3},
    )
    r = config._sanitize(d)
    assert r["opacity"] == 100  # строка — сброс к умолчанию
    assert r["font_scale"] == 180  # в пределах
    assert r["engine"] == "online"
    assert r["region"] is None
    assert r["history"] == [{"src": "1", "dst": "a"}]
    assert r["autocopy"] is False
    assert r["hotkeys"] == {"lens": "f9"}


def test_config_defaults_not_shared(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_FILE", tmp_path / "config.json")
    a = config.load()
    a["history"].append({"src": "x", "dst": "y"})
    assert config.load()["history"] == []


def test_config_broken_file_set_aside(monkeypatch, tmp_path):
    f = tmp_path / "config.json"
    f.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(config, "_FILE", f)
    data = config.load()
    assert data["engine"] == "online"
    assert (tmp_path / "config.broken.json").exists()


def test_config_save_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_DIR", tmp_path)
    data = config.load()
    data["target"] = "de"
    config.save(data)
    assert config.load()["target"] == "de"


# ---------- Ren'Py: теги и глоссарий ----------
def test_renpy_tags_survive_translation():
    out = renpy_text.translate_many(
        FakeEngine(), ["Hello {i}[name]{/i}, wait{w}"], "en", "ru"
    )
    assert out == ["HELLO {i}[name]{/i}, WAIT{w}"]


def test_renpy_glossary_names_not_translated():
    out = renpy_text.translate_many(
        FakeEngine(),
        ["Lucky smiles at Mary Jane."],
        "en",
        "ru",
        {"Lucky": "Lucky", "Mary Jane": "Мэри Джейн"},
    )
    assert out == ["Lucky SMILES AT Мэри Джейн."]


def test_renpy_only_tags_returned_as_is():
    eng = FakeEngine()
    assert renpy_text.translate_many(eng, ["{w}[x]"], "en", "ru") == ["{w}[x]"]
    assert eng.calls == 0


def test_renpy_restore_rejects_lost_markers():
    assert renpy_text.restore("text {0}", ["{i}", "{/i}"]) is None
    assert renpy_text.restore("a {1} b {0}", ["X", "Y"]) == "a Y b X"
    assert renpy_text.restore("a {0} [b", ["X"]) is None


def test_renpy_fallback_translates_pieces():
    class Lossy(FakeEngine):
        def translate_many(self, texts, source, target):
            # первый проход «теряет» метки — должен сработать запасной путь
            if any("{" in t for t in texts):
                return [t.replace("{0}", "") for t in texts]
            return super().translate_many(texts, source, target)

    out = renpy_text.translate_many(Lossy(), ["one {b}two{/b} three"], "en", "ru")
    assert out == ["ONE {b}TWO{/b} THREE"]


def test_online_rate_limit_fails_fast(monkeypatch):
    pytest.importorskip("requests")
    eng = translate.OnlineEngine()
    calls = []

    def limited(*_a):
        calls.append(1)
        raise RuntimeError("429 Client Error: Too Many Requests")

    monkeypatch.setattr(eng, "_once", limited)
    monkeypatch.setattr(translate.time, "sleep", lambda _s: None)
    with pytest.raises(translate.NetworkError, match="ограничил"):
        eng.translate("rate-limit-unique", "en", "ru")
    assert len(calls) == 1
