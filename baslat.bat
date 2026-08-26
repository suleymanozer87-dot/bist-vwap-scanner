@echo off
chcp 65001 >nul
title BIST Zincirleme VWAP Tarayıcı
cd /d "%~dp0"

echo ============================================
echo   BIST Zincirleme VWAP Tarayici Baslatiliyor
echo ============================================
echo.

REM Python kurulu mu kontrol et
where python >nul 2>nul
if errorlevel 1 (
    echo HATA: Python bulunamadi.
    echo Lutfen once https://www.python.org/downloads/ adresinden Python kurun.
    echo Kurulum sirasinda "Add python.exe to PATH" kutusunu isaretlemeyi unutmayin.
    echo.
    pause
    exit /b 1
)

REM Kutuphaneler kurulu mu kontrol et, degilse kur
python -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo Gerekli kutuphaneler kuruluyor, bu birkac dakika surebilir...
    pip install -r requirements.txt
    echo.
)

echo Arayuz baslatiliyor, tarayicinizda otomatik acilacak...
echo Kapatmak icin bu pencereyi kapatabilir ya da Ctrl+C basabilirsiniz.
echo.

streamlit run app.py

pause
