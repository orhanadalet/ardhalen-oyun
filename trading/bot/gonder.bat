@echo off
rem Fitil: trading\veri klasorundeki yeni zip dosyalarini GitHuba gonderir (yalnizca veri dosyalari).
cd /d "%~dp0.."
where git >nul 2>&1 || (echo Git kurulu degil. Komut Isteminde: winget install -e --id Git.Git & pause & exit /b 1)
git config user.name >nul 2>&1 || git config user.name "Fitil"
git config user.email >nul 2>&1 || git config user.email "fitil@localhost"
git add veri\*.zip
git diff --cached --quiet && (echo Gonderilecek yeni veri yok. & pause & exit /b 0)
git commit -m "Veri: MT5 fiyat gecmisi"
git pull --rebase
git push
if errorlevel 1 (echo. & echo Gonderilemedi. Bu ekranin goruntusunu gonderin.) else (echo. & echo Gonderildi.)
pause
