"""Сервисы: склейка OCR в абзацы/блоки, мост для игр (протокол и защита), воркер."""

import http.client
import json
import socket

import pytest
from conftest import FakeEngine

from autotranslator.core import translate
from autotranslator.services import bridge as bridge_mod
from autotranslator.services.ocr import OcrLine, group_blocks, reflow


# ---------- OCR: абзацы и блоки ----------
def _ln(text, top, left=0.0, width=500.0, height=20.0):
    return OcrLine(text, left, top, width, height)


def test_reflow_joins_wrapped_lines_and_hyphens():
    lines = [
        _ln("Это длинная строка, которую вёрстка перено-", 0),
        _ln("сла на следующую строку.", 24, width=260),
        _ln("Новый абзац после зазора.", 80),
    ]
    assert reflow(lines) == (
        "Это длинная строка, которую вёрстка переносла на следующую строку.\n"
        "Новый абзац после зазора."
    )


def test_reflow_keeps_bullets_separate():
    lines = [_ln("- первый пункт", 0), _ln("- второй пункт", 24)]
    assert reflow(lines) == "- первый пункт\n- второй пункт"


def test_reflow_removes_cjk_spaces():
    assert reflow([_ln("こ ん に ち は", 0)]) == "こんにちは"


def test_group_blocks_splits_far_apart_text():
    lines = [
        _ln("Dialogue line one", 500),
        _ln("dialogue line two", 524),
        _ln("Save", 10, left=1700, width=60),
    ]
    blocks = group_blocks(lines)
    assert sorted(len(b) for b in blocks) == [1, 2]


# ---------- мост: защита ----------
@pytest.mark.parametrize(
    ("headers", "ok"),
    [
        ({"Host": "127.0.0.1:47811"}, True),
        ({"Host": "localhost:47811"}, True),
        ({"Host": "evil.example:47811"}, False),  # DNS rebinding
        ({"Host": "127.0.0.1:47811", "Origin": "https://evil.example"}, False),
        ({"Host": "127.0.0.1:47811", "Sec-Fetch-Site": "cross-site"}, False),
        ({}, False),
    ],
)
def test_request_allowed(headers, ok):
    assert bridge_mod.request_allowed(headers, 47811) is ok


def test_parse_glossary():
    assert bridge_mod.parse_glossary("# c\nLucky = Лаки\nSCP 682\n = x\n") == {
        "Lucky": "Лаки",
        "SCP 682": "SCP 682",
    }


@pytest.fixture
def live_bridge(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(translate, "make_engine", lambda _name: engine)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    b = bridge_mod.Bridge(port)
    b.settings.update(source="en", target="ru")
    assert b.start(), b.error
    yield b, engine
    b.stop()


def _call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"} if body is not None else {}
    hdrs.update(headers or {})
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    payload = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, payload


def test_bridge_translate_and_fixes(live_bridge):
    b, engine = live_bridge
    status, out = _call(
        b.port, "POST", "/translate", {"texts": ["hi {i}you{/i}"], "game": "G"}
    )
    assert status == 200 and out["tr"] == ["HI {i}YOU{/i}"]
    b.add_fix("G", "hi {i}you{/i}", "привет")
    calls = engine.calls
    status, out = _call(
        b.port, "POST", "/translate", {"texts": ["hi {i}you{/i}"], "game": "G"}
    )
    assert out["tr"] == ["привет"] and engine.calls == calls  # правка без движка
    status, fx = _call(b.port, "GET", "/fixes?game=G&since=0&lang=ru")
    assert fx["items"] == [["hi {i}you{/i}", "привет"]]


def test_bridge_engine_is_pooled(live_bridge, monkeypatch):
    b, _ = live_bridge
    made = []
    monkeypatch.setattr(
        translate, "make_engine", lambda _n: made.append(1) or FakeEngine()
    )
    b._pool.clear()
    for _ in range(5):
        _call(b.port, "POST", "/translate", {"texts": ["x"], "game": "G"})
    assert len(made) == 1  # последовательные запросы берут движок из пула


def test_bridge_rejects_browser_and_garbage(live_bridge):
    b, _ = live_bridge
    st, _ = _call(b.port, "GET", "/info", headers={"Origin": "https://evil.example"})
    assert st == 403
    st, _ = _call(b.port, "GET", "/info", headers={"Host": "evil.example"})
    assert st == 403
    st, _ = _call(b.port, "POST", "/translate", {"texts": "not a list"})
    assert st == 400
    # кривые числа в /hello не роняют соединение
    st, out = _call(b.port, "POST", "/hello", {"game": "G", "dict": "x", "total": None})
    assert st == 200 and out["ok"]
    conn = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    conn.request(
        "POST",
        "/translate",
        body=b'{"texts": []}',
        headers={"Content-Type": "text/plain"},
    )
    assert conn.getresponse().status == 400


# ---------- воркер: офлайн-подстраховка ----------
def test_worker_falls_back_to_offline():
    from autotranslator.worker import Worker

    w = Worker()
    w._engines = {
        "online": FakeEngine(fail=translate.NetworkError("Нет сети")),
        "offline": FakeEngine(),
    }
    got = []
    w.translated.connect(lambda *a: got.append(a))
    w.do_translate(1, "hello", "online", "auto", "ru")
    assert got == [(1, "HELLO", "en", "offline")]

    w.offline_fallback = False
    failed = []
    w.failed.connect(lambda *a: failed.append(a))
    w.do_translate(2, "hello", "online", "auto", "ru")
    assert failed and failed[0][:2] == (2, "translate")


def test_ocr_noise_and_alignment():
    from autotranslator.services.ocr import block_alignment, is_noise

    assert all(is_noise(t) for t in ("12:45", "×", "a", "100%", "v1.2.0", "—"))
    assert not any(is_noise(t) for t in ("OK", "Save", "Page 3", "日"))
    left = [OcrLine("a", 10, 0, 100, 10), OcrLine("b", 10, 12, 50, 10)]
    center = [OcrLine("a", 10, 0, 100, 10), OcrLine("b", 35, 12, 50, 10)]
    right = [OcrLine("a", 10, 0, 100, 10), OcrLine("b", 60, 12, 50, 10)]
    assert block_alignment(left) == "left"
    assert block_alignment(center) == "center"
    assert block_alignment(right) == "right"
    assert block_alignment(left[:1]) == "left"


def test_worker_lens_big_blocks_first():
    from autotranslator.services.ocr import Block
    from autotranslator.worker import LENS_SPLIT_MIN_BLOCKS, Worker

    w = Worker()
    w._engines = {"online": FakeEngine()}
    texts = ["A long line of dialogue goes right here"] + ["Save"] * (
        LENS_SPLIT_MIN_BLOCKS - 1
    )
    blocks = [Block(t, 0, 0, 10, 10, 10, (0, 0, 0)) for t in texts]
    partial, final = [], []
    w.lens_partial.connect(lambda rid, t: partial.append(t))
    w.lens_translated.connect(lambda rid, t: final.append(t))
    w.do_lens_translate(7, blocks, "online", "en", "ru")
    # сначала только реплика, мелочь ещё пустая; потом всё
    assert partial == [[texts[0].upper()] + [""] * (len(texts) - 1)]
    assert final == [[t.upper() for t in texts]]

    few = blocks[:2]
    partial.clear()
    final.clear()
    w.do_lens_translate(8, few, "online", "en", "ru")
    assert partial == [] and final == [[t.upper() for t in texts[:2]]]


def _block(text, left=10, top=10, width=200, height=20):
    from autotranslator.services.ocr import Block

    return Block(text, left, top, width, height, 20, (0, 0, 0))


def test_tracker_waits_for_typing_per_block():
    from autotranslator.live.tracker import BlockTracker

    t = BlockTracker()
    clock = 100.0
    # реплика «печатается», а подпись кнопки стоит на месте
    t.update([_block("Hello th"), _block("Save", top=300, width=40)], clock)
    t.update(
        [_block("Hello there, fri"), _block("Save", top=300, width=40)], clock + 0.3
    )
    assert [s.text for s in t.to_translate()] == ["Save"]  # кнопка — сразу
    t.update(
        [_block("Hello there, friend"), _block("Save", top=300, width=40)], clock + 0.6
    )
    assert [s.text for s in t.to_translate()] == ["Save"]
    t.update(
        [_block("Hello there, friend"), _block("Save", top=300, width=40)], clock + 0.9
    )
    assert sorted(s.text for s in t.to_translate()) == ["Hello there, friend", "Save"]


def test_tracker_keeps_translation_on_ocr_jitter_and_drops_on_new_text():
    from autotranslator.live.tracker import BlockTracker

    t = BlockTracker()
    t.update([_block("The quick brown fox jumps.")], 0.0)
    t.settle()
    (slot,) = t.to_translate()
    slot.asked = slot.text
    assert t.apply([(slot.id, slot.text)], ["Быстрая лиса прыгает."])
    t.update([_block("The quick brown f0x jumps,")], 1.0)  # дрожание OCR
    assert slot.dst == "Быстрая лиса прыгает." and not t.to_translate()
    t.update([_block("A completely different line here.")], 2.0)  # новая реплика
    assert slot.dst is None and not slot.stable
    # опоздавший перевод старой реплики не прилипает к новой
    assert not t.apply([(slot.id, "The quick brown fox jumps.")], ["Лиса"])


def test_tracker_gives_up_waiting_after_limit():
    from autotranslator.live.tracker import STABLE_MAX_S, BlockTracker

    t = BlockTracker()
    t.update([_block("aaaa bbbb")], 0.0)
    t.update([_block("aaaa bbbb cccc")], STABLE_MAX_S / 2)
    assert not t.to_translate()
    t.update([_block("aaaa bbbb cccc dddd")], STABLE_MAX_S + 0.1)
    assert [s.text for s in t.to_translate()] == ["aaaa bbbb cccc dddd"]


def test_same_text_tolerates_jitter_but_not_typing():
    from autotranslator.core.textutil import same_text

    assert same_text("Hello, world!", "hello world")
    assert same_text("The quick brown fox jumps.", "The quick brown f0x jumps,")
    assert not same_text("Hello th", "Hello there")
    assert not same_text("Save", "Load")


def test_capture_changes_finds_dirty_regions():
    from PIL import Image, ImageDraw

    from autotranslator.live.capture import CaptureWorker

    a = Image.new("RGB", (400, 200), (20, 20, 20))
    b = a.copy()
    ImageDraw.Draw(b).rectangle((10, 10, 120, 40), fill=(250, 250, 250))
    sig_a, sig_b = CaptureWorker.signature(a), CaptureWorker.signature(b)
    assert CaptureWorker.changes(sig_a, sig_a, [(0, 0, 50, 50)]) == (0.0, [])
    share, hit = CaptureWorker.changes(
        sig_a, sig_b, [(300, 100, 50, 50), (0, 0, 60, 60)]
    )
    assert 0 < share < 0.2 and hit == [1]


def test_live_session_translates_stable_blocks_and_follows_clicks(monkeypatch):
    from PyQt6.QtCore import QObject, QPoint, QRect, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    from autotranslator.live import session as S

    app = QApplication.instance() or QApplication([])  # noqa: F841 — держим ссылку

    class FakeCapture(QObject):
        frame = pyqtSignal(int, object, object)
        same = pyqtSignal(int, bool)
        dirty = pyqtSignal(int, object)
        failed = pyqtSignal(int, str)

        def __init__(self):
            super().__init__()
            self.requests = []

        def capture(self, req_id, *args):
            self.requests.append(req_id)

        def reset(self):
            pass

    class FakeTranslator(QObject):
        live_translated = pyqtSignal(int, object)
        live_failed = pyqtSignal(int, str)

        def __init__(self):
            super().__init__()
            self.asked = []

        def do_live_translate(self, req_id, blocks, *args):
            self.asked.append((req_id, [b.text for b in blocks]))

    monkeypatch.setattr(S.QTimer, "singleShot", lambda *a: None)  # перепроверки вручную
    cap, tr = FakeCapture(), FakeTranslator()
    s = S.LiveSession(
        QRect(0, 0, 400, 300), cap, tr, lambda: ("online", "en", "ru", "en")
    )
    from PIL import Image

    image = Image.new("RGB", (400, 300), (10, 10, 30))

    def frame(*texts):
        s.capture(force=True)
        blocks = [_block(t, top=10 + 40 * i) for i, t in enumerate(texts)]
        cap.frame.emit(s._req, blocks, image)

    frame("Hello th")  # реплика печатается — не переводим
    frame("Hello there, my friend")  # ещё изменилась — снова ждём
    assert tr.asked == []
    s.capture(force=False)
    cap.same.emit(s._req, True)  # кадр замер — допечатано, переводим
    assert [t for _, t in tr.asked] == [["Hello there, my friend"]]
    tr.live_translated.emit(tr.asked[-1][0], ["Привет, мой друг"])
    assert s.overlay.has_items
    frame("Hello there, my friend")  # тот же текст — без нового запроса
    assert len(tr.asked) == 1
    # клик в игре: перевод реплики гаснет сразу, до распознавания
    s._on_advance("mouse", QPoint(60, 250))
    assert s.overlay.hidden_ids()
    s._on_advance("mouse", QPoint(900, 900))  # клик мимо области — не считается
    frame("")  # текста нет — слой очищается
    assert not s.overlay.has_items
    # пока кадр распознаётся, новые запросы не копятся в очереди
    s.capture(force=True)
    n = len(cap.requests)
    s.capture(force=True)
    s.capture(force=False)
    assert len(cap.requests) == n
    cap.same.emit(s._req, True)  # кадр вернулся — отложенный запрос уходит один
    assert len(cap.requests) == n + 1
    s.stop()


def test_text_quality_tells_garbage_from_text():
    from autotranslator.core.textutil import text_quality
    from autotranslator.services.ocr import is_garbage

    # русский текст, прочитанный английским OCR
    assert text_quality('CMeHa *3b1Ka nn" ABHXKa paHbLue') < 0.5
    assert text_quality("YnyuweH'•1e nepeB0ALMKa") < 0.4
    for ok in (
        "Hello there, my friend!",
        "Привет, как дела?",
        "RenpyTestMenu, iPhone, OK",
        "README.md",
        "こんにちは世界",
    ):
        assert text_quality(ok) > 0.9, ok
    assert is_garbage("YnyuweH'•1e nepeB0ALMKa")
    assert not is_garbage("Save") and not is_garbage("x7Q")  # коротко — не судим


def test_lang_picker_switches_to_readable_language(monkeypatch):
    from autotranslator.services import ocr

    monkeypatch.setattr(ocr, "available_ocr_languages", lambda: ["en-US", "ru"])
    calls = []

    def recognize(tag):
        calls.append(tag)
        return {"en-US": "pa60Tana nepeB0A KHonKa", "ru": "работа перевод кнопка"}[tag]

    from autotranslator.core.textutil import text_quality

    p = ocr.LangPicker()
    assert p.run("en-US", recognize, text_quality) == "работа перевод кнопка"
    assert p.chosen("en-US") == "ru"
    calls.clear()
    p.run("en-US", recognize, text_quality)  # дальше — сразу нужным языком
    assert calls == ["ru"]


def test_lang_picker_does_not_retry_garbage_every_frame(monkeypatch):
    from autotranslator.services import ocr

    monkeypatch.setattr(ocr, "available_ocr_languages", lambda: ["en-US", "ru"])
    calls = []

    def recognize(tag):
        calls.append(tag)
        return "xqzw MOxews"  # мусор при любом языке (узор фона)

    p = ocr.LangPicker()
    p.run("en-US", recognize, lambda t: 0.1)
    p.run("en-US", recognize, lambda t: 0.1)
    assert calls == ["en-US", "ru", "en-US"]  # вторая попытка — не раньше RETRY_S


def test_dialogue_area_learned_from_clicks():
    from autotranslator.live.tracker import DialogueArea

    d = DialogueArea()
    assert d.rect is None
    for text in ("First line of dialogue here", "Second line of dialogue"):
        d.clicked()
        d.changed(_block("Auto", top=5, width=40))  # подпись — не реплика
        d.changed(_block(text, top=400, width=600))
    assert d.rect is not None
    assert d.contains((10, 400, 500, 20))
    assert not d.contains((10, 5, 40, 20))
