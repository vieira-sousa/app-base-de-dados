@echo off
REM Mede velocidade e acerto do modelo num relatorio ficticio.
REM Uso: testar_llm.bat            (modelo de config\llm.yaml)
REM      testar_llm.bat gemma3:4b  (outro modelo, depois de "ollama pull gemma3:4b")
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if "%~1"=="" (
    .venv\Scripts\python.exe -m extrator testar-llm
) else (
    .venv\Scripts\python.exe -m extrator testar-llm --modelo %1
)
pause
