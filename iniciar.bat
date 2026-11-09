@echo off
REM Abre a interface no browser (http://localhost:8501). Fechar esta janela termina a app.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist .venv\Scripts\python.exe (
    echo Corra primeiro instalar.bat
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m streamlit run app.py --server.headless false
pause
