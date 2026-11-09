@echo off
REM Instalacao no Windows: correr com duplo clique (uma vez).
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERRO] Python nao encontrado.
    echo Instale Python 3.12 de https://www.python.org/downloads/windows/
    echo e marque "Add python.exe to PATH" na instalacao.
    pause
    exit /b 1
)

echo == A criar ambiente virtual (.venv)
if not exist .venv\Scripts\python.exe %PY% -m venv .venv
if errorlevel 1 ( echo [ERRO] venv falhou & pause & exit /b 1 )

echo == A instalar dependencias (precisa de internet, so desta vez)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERRO] pip falhou & pause & exit /b 1 )

if not exist config\base_dados.yaml (
    copy config\base_dados.exemplo.yaml config\base_dados.yaml >nul
    echo == Criado config\base_dados.yaml. Copie o Excel da base para a pasta dados\ com o nome
    echo    Base_Hemicolectomia.xlsx ^(ou altere "ficheiro:" em config\base_dados.yaml^).
)

where ollama >nul 2>nul
if errorlevel 1 (
    echo.
    echo [AVISO] Ollama nao instalado. Descarregue de https://ollama.com/download/windows
    echo         e depois corra este ficheiro outra vez. Sem Ollama a app funciona so com regex.
) else (
    echo == A descarregar o modelo qwen2.5:3b ^(~1,9 GB, so desta vez^)
    ollama pull qwen2.5:3b
)

echo.
.venv\Scripts\python.exe -m extrator verificar
echo.
echo Instalacao concluida. Para usar: duplo clique em iniciar.bat
pause
