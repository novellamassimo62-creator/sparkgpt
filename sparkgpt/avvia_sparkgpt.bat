@echo off
cd /d "%~dp0"
set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
set "OLLAMA_APP=%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
if not exist "%OLLAMA_EXE%" (
  echo Ollama non risulta installato. Scaricalo da https://ollama.com/download/windows
  pause
  exit /b 1
)
curl.exe --silent --fail http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  if exist "%OLLAMA_APP%" start "" "%OLLAMA_APP%"
  set "OLLAMA_READY="
  for /l %%i in (1,1,20) do (
    if not defined OLLAMA_READY (
      timeout /t 2 /nobreak >nul
      curl.exe --silent --fail http://127.0.0.1:11434/api/tags >nul 2>nul && set "OLLAMA_READY=1"
    )
  )
)
curl.exe --silent --fail http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  echo Ollama non si e avviato. Apri Ollama e riprova.
  pause
  exit /b 1
)
"%OLLAMA_EXE%" list | findstr /i /c:"qwen2.5:3b" >nul
if errorlevel 1 (
  echo Scarico il modello AI locale qwen2.5:3b, solo la prima volta...
  "%OLLAMA_EXE%" pull qwen2.5:3b
  if errorlevel 1 (
    echo Download del modello non riuscito.
    pause
    exit /b 1
  )
)
set "VISION_MODEL_READY="
"%OLLAMA_EXE%" list | findstr /i /c:"qwen2.5vl:3b" >nul
if not errorlevel 1 set "VISION_MODEL_READY=1"
if not defined VISION_MODEL_READY (
  echo Scarico il modello visivo locale qwen2.5vl:3b, solo la prima volta. Il download e' di alcuni GB...
  "%OLLAMA_EXE%" pull qwen2.5vl:3b
  if errorlevel 1 (
    echo Download del modello per le immagini non riuscito. Chat testuale disponibile al prossimo avvio.
    pause
    exit /b 1
  )
)
set "PYTHON_COMMAND=python"
set "PYTHON_PREFIX="
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_COMMAND=.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_COMMAND=py"
    set "PYTHON_PREFIX=-3"
  )
)
call "%PYTHON_COMMAND%" %PYTHON_PREFIX% -c "import jwt, torch, diffusers, transformers, PIL, huggingface_hub; assert torch.version.cuda is None" >nul 2>nul
if errorlevel 1 (
  echo Installo PyTorch solo CPU e i componenti locali gratuiti per le immagini...
  call "%PYTHON_COMMAND%" %PYTHON_PREFIX% -c "import torch; assert torch.version.cuda is None" >nul 2>nul
  if errorlevel 1 (
    call "%PYTHON_COMMAND%" %PYTHON_PREFIX% -m pip uninstall --yes torch
    if errorlevel 1 (
      echo Rimozione della versione precedente di PyTorch non riuscita.
      pause
      exit /b 1
    )
    call "%PYTHON_COMMAND%" %PYTHON_PREFIX% -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cpu torch
    if errorlevel 1 (
      echo Installazione di PyTorch CPU non riuscita.
      pause
      exit /b 1
    )
  )
  call "%PYTHON_COMMAND%" %PYTHON_PREFIX% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo Installazione delle dipendenze non riuscita.
    pause
    exit /b 1
  )
)
call "%PYTHON_COMMAND%" %PYTHON_PREFIX% server.py
if errorlevel 1 pause
