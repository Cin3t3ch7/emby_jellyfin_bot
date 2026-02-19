from telegram import Update
from telegram.ext import CallbackContext
from database import Session, User
from config import SUPER_ADMIN_IDS, ADMIN_IDS
from datetime import datetime
import logging
from database import Role

logger = logging.getLogger(__name__)

async def check_authorization(update: Update, context: CallbackContext):
    """Verifica si un usuario está autorizado para usar el bot"""
    user = update.effective_user
    telegram_id = user.id
    
    session = Session()
    
    # Si es SUPER_ADMIN o ADMIN, está autorizado
    if telegram_id in ADMIN_IDS:
        db_user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not db_user:
            # Crear usuario si no existe
            db_user = User(
                telegram_id=telegram_id,
                username=user.username,
                full_name=f"{user.first_name} {user.last_name if user.last_name else ''}",
                role="SUPER_ADMIN" if telegram_id in SUPER_ADMIN_IDS else "ADMIN",
                credits=float('inf'),
                is_authorized=True
            )
            session.add(db_user)
            session.commit()
        
        session.close()
        return True
    
    # Para usuarios no-admin, verificar si están en la base de datos y autorizados
    db_user = session.query(User).filter_by(telegram_id=telegram_id).first()
    
    # Si el usuario no existe o no está autorizado
    if not db_user or not db_user.is_authorized:
        # Notificar a los admins (si no existe en DB)
        if not db_user:
            await notify_admins_about_new_user(context, user)
        
        session.close()
        return False
    
    session.close()
    return True

async def notify_admins_about_new_user(context: CallbackContext, user):
    """Notifica a los administradores sobre un nuevo usuario"""
    message = (
        f"🆕 Nuevo usuario detectado\n"
        f"🆔 ID: {user.id}\n"
        f"👤 Nombre: {user.first_name} {user.last_name if user.last_name else ''}\n"
        f"📝 Username: {('@' + user.username) if user.username else 'No establecido'}\n"
        f"📅 Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=message)
        except Exception as e:
            logger.error(f"Error al notificar admin {admin_id}: {e}")

async def unauthorized_message(update: Update, context: CallbackContext):
    """Mensaje para usuarios no autorizados"""
    await update.message.reply_text(
        "⚠️ No estás autorizado para usar este bot.\n"
        "Se ha notificado a los administradores sobre tu solicitud.\n"
        "Por favor, espera a que te den acceso o contacta directamente con ellos."
    )
