"""Точка входа AutoTranslator (без консольного окна — расширение .pyw)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autotranslator.app import main  # noqa: E402

raise SystemExit(main())
