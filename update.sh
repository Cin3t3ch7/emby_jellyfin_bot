#!/bin/bash
# Script de actualización para Linux/Kali/VPS
# Repositorio: https://github.com/Cin3t3ch7/emby_jellyfin_bot

echo "🔄 Iniciando actualización del bot..."

# Ir al directorio del script
cd "$(dirname "$0")" || exit

# Asegurar que estamos en la rama main
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "⚠️  No estás en la rama 'main'. Cambiando a 'main'..."
    git checkout main || git checkout -b main
fi

# Guardar el hash actual para comparar después
OLD_HASH=$(git rev-parse HEAD)

# Descargar cambios de la rama main
echo "📥 Descargando cambios de GitHub (rama main)..."
git fetch origin main
git reset --hard origin/main

# Obtener el nuevo hash
NEW_HASH=$(git rev-parse HEAD)

if [ "$OLD_HASH" = "$NEW_HASH" ]; then
    echo "✅ Ya estás en la última versión."
else
    echo "📦 Actualizado de $OLD_HASH a $NEW_HASH"

    # Verificar si requirements.txt cambió para instalar dependencias
    if git diff --name-only "$OLD_HASH" "$NEW_HASH" | grep -q "requirements.txt"; then
        echo "📚 Detectados cambios en dependencias. Instalando..."
        pip install -r requirements.txt
    fi
fi

# Dar permisos de ejecución nuevamente al script por si acaso
chmod +x update.sh

# Reiniciar el servicio si existe
SERVICE_NAME="emby_bot"
if systemctl list-units --full -all | grep -Fq "$SERVICE_NAME.service"; then
    echo "🔄 Reiniciando servicio $SERVICE_NAME..."
    sudo systemctl restart "$SERVICE_NAME"
    echo "✅ Servicio reiniciado."
else
    echo "⚠️  El servicio '$SERVICE_NAME' no se detectó o no está activo."
    echo "   Si estás usando screen o tmux, reinicia el proceso manualmente (Ctrl+C y python bot.py)."
fi

echo "✅ Proceso finalizado."
