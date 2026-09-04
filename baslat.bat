@echo off
chcp 65001 >nul
title BIST Teknik Tarayici
cd /d "%~dp0"

echo ============================================
echo   BIST Teknik Tarayici Baslatiliyor
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo HATA: Python bulunamadi.
    echo Lutfen Python 3.11 veya daha yeni bir surum kurun.
    echo Kurulum sirasinda "Add python.exe to PATH" kutusunu isaretleyin.
    echo.
    pause
    exit /b 1
)

REM Gerekli kutuphaneleri ve kritik surumleri kontrol et.
python -c "import streamlit,plotly,yfinance,pandas; sv=tuple(map(int,streamlit.__version__.split('.')[:2])); pv=tuple(map(int,pandas.__version__.split('.')[:2])); assert sv >= (1,51), streamlit.__version__; assert pv >= (2,2), pandas.__version__" >nul 2>nul
if errorlevel 1 (
    echo Gerekli kutuphaneler eksik veya eski. Kurulum/guncelleme yapiliyor...
    python -m pip install --upgrade -r requirements.txt
    if errorlevel 1 (
        echo.
        echo HATA: Kutuphane kurulumu tamamlanamadi. Internet baglantisini kontrol edin.
        pause
        exit /b 1
    )
    echo.
)

echo Arayuz baslatiliyor...
echo Kapatmak icin bu pencereyi kapatabilir ya da Ctrl+C basabilirsiniz.
echo.

python -m streamlit run app.py
pause
