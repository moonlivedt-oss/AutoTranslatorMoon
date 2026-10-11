@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem Запуск AutoTranslator двойным кликом. Сам находит подходящий Python, при первом
rem запуске ставит пакеты из requirements.txt и стартует без окна консоли.
rem Windows OCR (пакет winsdk) собран только под Python 3.11 и 3.12.

set "PY="
for %%V in (3.11 3.12) do if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
)
if not defined PY (
    python -c "import sys; sys.exit(sys.version_info[:2] not in ((3, 11), (3, 12)))" >nul 2>&1 && set "PY=python"
)
if defined PY goto :deps

echo.
echo Для AutoTranslator нужен Python 3.11 или 3.12 (под 3.13+ нет пакета Windows OCR).
where winget >nul 2>&1 || goto :nopython
choice /c YN /m "Установить Python 3.11 через winget сейчас"
if errorlevel 2 goto :nopython
winget install -e --id Python.Python.3.11 --scope user --accept-package-agreements --accept-source-agreements
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if defined PY goto :deps
echo Python поставлен, но ещё не виден в этом окне. Запустите файл ещё раз.
pause
exit /b 1

:nopython
echo Установите Python 3.11 с https://www.python.org/downloads/ и запустите файл ещё раз.
pause
exit /b 1

:deps
%PY% -c "import PyQt6.QtWidgets, requests, mss, winsdk, PIL" >nul 2>&1 && goto :run
echo Первый запуск: ставлю пакеты из requirements.txt (один раз, нужен интернет)...
%PY% -m pip install --disable-pip-version-check -r requirements.txt
%PY% -c "import PyQt6.QtWidgets, requests, mss, winsdk, PIL" >nul 2>&1 && goto :run
echo.
echo Не удалось поставить пакеты. Текст ошибки - выше.
pause
exit /b 1

:run
rem Берём настоящий pythonw.exe рядом с python.exe: заглушка pyw из Microsoft
rem Store мелькает и не стартует.
set "PYW="
for /f "delims=" %%p in ('%PY% -c "import os, sys; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%p"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0run.pyw"
) else (
    start "" %PY% "%~dp0run.pyw"
)
