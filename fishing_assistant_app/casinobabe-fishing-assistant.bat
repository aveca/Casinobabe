@echo off
REM Casinobabe Fishing Assistant - Standalone Launcher
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=python.exe"
set "APP_SCRIPT=%SCRIPT_DIR%\fishing_assistant.py"

echo.
echo ============================================
echo  Casinobabe Fishing Assistant
echo  Standalone Local Supervisor
echo ============================================

if not exist "%APP_SCRIPT%" (
    echo Erreur: Fichier dapplication introuable : %APP_SCRIPT%
    pause
    exit /b 1
)

if not "%1"=="--minimal" (
    echo Vérification de l'environnement...
    where %PYTHON_EXE% >nul 2>&1 || (
        echo Erreur: Python n'est pas installé ou n'est pas dans le PATH.
        pause
        exit /b 1
    )
    echo Python detecte : %PYTHON_EXE%
)

echo.
echo Démarrage de l'assistant pêche Casinobabe...
echo.

"%PYTHON_EXE%" "%APP_SCRIPT%" %*

echo.
echo Assistant termine. Appuyez sur une touche pour fermer...
pause >nul