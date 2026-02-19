#!/bin/bash
# Script de actualización para Linux/Kali

echo "🔄 Actualizando bot..."

# Ir al directorio del bot
cd "$(dirname "$0")"

# Guardar el hash actual
OLD_HASH=$(git rev-parse HEAD)

# Descargar cambios
echo "📥 Descargando cambios de GitHub..."
git pull

# Obtener el nuevo hash
NEW_HASH=$(git rev-parse HEAD)

# Verificar si hubo cambios
if [ "$OLD_HASH" = "$NEW_HASH" ]; then
    echo "✅ Ya estás en la última versión"
    exit 0
fi

echo "📦 Nueva versión detectada: $NEW_HASH"

# Verificar si requirements.txt cambió
if git diff "$OLD_HASH" "$NEW_HASH" --name-only | grep -q "requirements.txt"; then
    echo "📚 Instalando nuevas dependencias..."
    pip install -r requirements.txt
fi

# Reiniciar el bot si está corriendo como servicio
if systemctl is-active --quiet emby_bot; then
    echo "🔄 Reiniciando servicio..."
    sudo systemctl restart emby_bot
    echo "✅ Bot reiniciado"
else
    echo "⚠️  El bot no está corriendo como servicio"
    echo "   Reinícialo manualmente: python bot.py"
fi

echo "✅ Actualización completada"
