@echo off
chcp 65001 >nul
setlocal EnableExtensions
title mtslinker
cd /d "%~dp0"

rem --- ищем python: сначала обычный, потом conda ---
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
set "CONDA="
if not defined PY for %%d in ("%USERPROFILE%\MiniConda3" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3") do if not defined CONDA if exist "%%~d\python.exe" set "CONDA=%%~d"
if defined CONDA set "PATH=%CONDA%;%CONDA%\Library\bin;%CONDA%\Scripts;%PATH%"
if defined CONDA set "PY=python"
if not defined PY (
    echo python не найден
    echo поставь его с https://www.python.org/downloads/
    echo при установке отметь галочку "Add python.exe to PATH"
    pause
    exit /b 1
)

rem --- при первом запуске ставим зависимости ---
%PY% -c "import httpx, imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
    echo первый запуск, ставлю зависимости, подожди минуту...
    %PY% -m pip install --quiet httpx imageio-ffmpeg
    if errorlevel 1 (
        echo установка не удалась, скинь скрин
        pause
        exit /b 1
    )
)

set "OUT=%USERPROFILE%\Videos\mtslinker"
if not exist "%OUT%" mkdir "%OUT%"

rem --- режим: при первом запуске спрашиваем, потом берём из mode.txt ---
set "MODE="
if exist "%~dp0mode.txt" set /p MODE=<"%~dp0mode.txt"
if not defined MODE call :choosemode

:loop
cls
if "%MODE%"=="full" (set "MLABEL=как в плеере, без экрана показываются вебки") else (set "MLABEL=только экран, без экрана чёрный кадр")
echo ================ mtslinker ================
echo видео сохраняются в %OUT%
echo режим: %MLABEL%
echo.
set "URL="
set /p "URL=вставь ссылку и нажми enter (m - сменить режим): "
if not defined URL goto loop

rem убираем кавычки, всё после ? и слеш в конце
set "URL=%URL:"=%"
if /i "%URL%"=="m" (
    call :choosemode
    goto loop
)
for /f "tokens=1 delims=?#" %%a in ("%URL%") do set "URL=%%a"
if "%URL:~-1%"=="/" set "URL=%URL:~0,-1%"

rem --- sessionId для закрытых записей ---
set "SID="
if exist "%~dp0session.txt" set /p SID=<"%~dp0session.txt"
if defined SID (
    echo использую сохранённый sessionId, удали session.txt чтобы сбросить
) else (
    set /p "SID=sessionId если запись закрытая, иначе просто enter: "
)
if defined SID if not exist "%~dp0session.txt" (echo %SID%)>"%~dp0session.txt"

echo.
pushd "%OUT%"
if defined SID goto withsid
%PY% "%~dp0mts_merge.py" "%URL%" --mode %MODE%
goto done
:withsid
%PY% "%~dp0mts_merge.py" "%URL%" --mode %MODE% --session-id %SID%
:done
popd

start "" "%OUT%"
echo.
echo enter чтобы скачать ещё одну запись
pause >nul
goto loop

:choosemode
echo.
echo выбери режим:
echo   1 - только экран. когда лектор не показывает экран, будет чёрный кадр
echo       лучше для конспектов и анализа кадров
echo   2 - как в плеере. когда экрана нет, показываются включённые вебки
echo       (выключенные отсеиваются, несколько штук встают сеткой)
set "M="
set /p "M=1 или 2: "
if "%M%"=="2" (set "MODE=full") else (set "MODE=screen")
(echo %MODE%)>"%~dp0mode.txt"
exit /b
