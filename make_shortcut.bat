@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\mtslinker.lnk'); $s.TargetPath='%~dp0mtslinker.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0mtslinker.ico,0'; $s.Description='mts link webinar downloader'; $s.Save()"
if errorlevel 1 (
    echo something went wrong, send a screenshot
    pause
    exit /b 1
)
taskkill /f /im StartMenuExperienceHost.exe >nul 2>&1
echo.
echo done. open start - all apps - M - mtslinker
pause
