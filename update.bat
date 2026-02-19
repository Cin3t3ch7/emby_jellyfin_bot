@echo off
REM Script de actualización para Windows

echo ========================================
echo   Actualizando bot...
echo ========================================

REM Guardar directorio actual
pushd %~dp0

REM Guardar hash actual
for /f %%i in ('git rev-parse HEAD') do set OLD_HASH=%%i

REM Descargar cambios
echo.
echo Descargando cambios de GitHub...
git pull

REM Obtener nuevo hash
for /f %%i in ('git rev-parse HEAD') do set NEW_HASH=%%i

REM Verificar si hubo cambios
if "%OLD_HASH%"=="%NEW_HASH%" (
    echo.
    echo Ya estas en la ultima version
    goto :end
)

echo.
echo Nueva version detectada: %NEW_HASH%

REM Instalar dependencias (siempre por seguridad en Windows)
echo.
echo Instalando dependencias...
pip install -r requirements.txt

echo.
echo ========================================
echo   Actualizacion completada
echo   Reinicia el bot manualmente: py bot.py
echo ========================================

:end
popd
pause
