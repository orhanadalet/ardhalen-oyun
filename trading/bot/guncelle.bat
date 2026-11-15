@echo off
rem Fitil: dosyalari GitHubdaki son surume gunceller. config.json, gunluk ve veriler korunur.
cd /d "%~dp0.."
where git >nul 2>&1 || (echo Git kurulu degil. Komut Isteminde: winget install -e --id Git.Git & pause & exit /b 1)
git rev-parse --is-inside-work-tree >nul 2>&1 || (echo Bu klasor git ile indirilmemis. Kurulum adimlari: trading\bot\README.md, "Kolay guncelleme". & pause & exit /b 1)
echo Guncelleniyor...
git pull --ff-only
if errorlevel 1 (echo. & echo Guncelleme yapilamadi. Bu ekranin goruntusunu gonderin.) else (echo. & echo Guncel.)
pause
