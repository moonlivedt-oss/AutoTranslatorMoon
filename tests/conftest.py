"""Общие настройки тестов: пакет из корня проекта, Qt без экрана."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeEngine:
    """Движок-заглушка: «переводит» в верхний регистр, считает вызовы."""

    name = "fake"

    def __init__(self, fail=None):
        self.calls = 0
        self.fail = fail

    def translate(self, text, source, target):
        self.calls += 1
        if self.fail:
            raise self.fail
        return text.upper()

    def translate_many(self, texts, source, target):
        return [self.translate(t, source, target) for t in texts]

    def detected(self, text):
        return "en"

    def has_model(self, text, source, target):
        return True
