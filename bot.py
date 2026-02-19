import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext
from config import BOT_TOKEN
from database import init_db
from handlers.command_handler import start_command, price_command, adduser_command, deluser_command, credits_command, role_command, monitor_command, reset_command, list_command, handle_download_accounts, checkdevices_command, demos_command, check_expired_command, list_accounts_command, cleanup_orphaned_command
from handlers.menu_handler import handle_callback_query, handle_server_input, handle_username_delete, handle_renewal_input
from handlers.auth_handler import check_authorization, unauthorized_message
from utils.keyboards import main_menu_keyboard
from scheduled_tasks import check_expired_accounts, send_servers_status_to_admins, cleanup_orphaned_devices, check_and_enforce_device_limits

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Disable httpx logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

async def auth_middleware(update: Update, context):
    """Middleware para verificar autorización antes de procesar comandos"""
    if not await check_authorization(update, context): 
        await unauthorized_message(update, context)
        return False
    return True

# Envoltorio para comandos que requieren autorización
def auth_wrapper(func):
    async def wrapped(update, context):
        if await auth_middleware(update, context):
            await func(update, context)
    return wrapped

# Manejador de errores
async def error_handler(update, context):
    """Registra errores causados por actualizaciones."""
    logger.error(f"Error al procesar actualización: {context.error}")
    
    try:
        # Enviar mensaje al usuario
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Ha ocurrido un error al procesar tu solicitud. "
                "Por favor, intenta nuevamente más tarde."
            )
    except Exception as e:
        logger.error(f"Error al enviar mensaje de error: {e}")

# Manejador para callbacks que requieren autorización
async def auth_callback_query_handler(update, context):
    """Verifica autorización antes de procesar callbacks"""
    query = update.callback_query
    
    # Verificar autorización
    if await auth_middleware(update, context):
        await handle_callback_query(update, context)
    else:
        # Responder para que el botón deje de cargarse
        await query.answer("No estás autorizado para usar este bot")

# Manejador para mensajes de texto (para procesos de entrada como agregar servidor)
async def text_message_handler(update: Update, context: CallbackContext):
    """Maneja mensajes de texto para diversos procesos interactivos"""
    # Verificar si hay procesos activos que requieren entrada de texto
    if 'add_server_step' in context.user_data or 'edit_server_step' in context.user_data:
        if await auth_middleware(update, context):
            # Si estamos en un proceso de agregar/editar servidor, dejar que el manejador específico lo procese
            await handle_server_input(update, context)
    elif 'expecting_username_delete' in context.user_data and context.user_data['expecting_username_delete']:
        if await auth_middleware(update, context):
            # Proceso para eliminar usuario
            await handle_username_delete(update, context)
    elif 'expecting_renewal_input' in context.user_data and context.user_data['expecting_renewal_input']:
        if await auth_middleware(update, context):
            # Proceso para renovar usuario
            await handle_renewal_input(update, context)
    else:
        # Si no hay un proceso activo, entonces es un mensaje desconocido
        if await auth_middleware(update, context):
            await update.message.reply_text(
                "Comando no reconocido. Usa /start para mostrar el menú principal."
            )

# Manejador para el comando cancel
async def cancel_command(update, context):
    """Maneja el comando /cancel en cualquier contexto"""
    if not await auth_middleware(update, context):
        return
    
    # Si estamos en un proceso de agregar/editar servidor, cancelarlo
    process_active = False
    
    if 'add_server_step' in context.user_data or 'edit_server_step' in context.user_data:
        process_active = True
        context.user_data.clear()
    
    if 'expecting_username_delete' in context.user_data:
        process_active = True
        context.user_data.clear()
    
    if 'expecting_renewal_input' in context.user_data:
        process_active = True
        context.user_data.clear()
    
    if process_active:
        await update.message.reply_text(
            "❌ Proceso cancelado. Volviendo al menú principal.",
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "No hay ningún proceso activo para cancelar.",
            reply_markup=main_menu_keyboard()
        )

# Esta función configura el job_queue para ejecutar tareas en segundo plano
async def setup_jobs_background(context):
    """Configura las tareas programadas para ejecutarse en segundo plano"""
    logger.info("Configurando tareas programadas en segundo plano...")
    
    # Programar la verificación de cuentas expiradas cada 15 minutos
    context.job_queue.run_repeating(
        callback=check_expired_accounts,
        interval=900,  # 15 minutos en segundos
        first=10  # Empezar después de 10 segundos
    )

    # Programar el envío de estado de servidores cada 5 horas
    context.job_queue.run_repeating(
        callback=send_servers_status_to_admins,
        interval=18000,  # 5 horas en segundos
        first=120  # Empezar después de 2 minutos
    )

    # Programar la limpieza de dispositivos huérfanos cada 12 horas
    context.job_queue.run_repeating(
        callback=cleanup_orphaned_devices,
        interval=43200,  # 12 horas en segundos
        first=300  # Empezar después de 5 minutos
    )

    # Programar la verificación de límites de dispositivos cada 3 horas
    context.job_queue.run_repeating(
        callback=check_and_enforce_device_limits,
        interval=10800,  # 3 horas en segundos
        first=600  # Empezar después de 10 minutos
    )
    
    logger.info("Tareas programadas configuradas correctamente en segundo plano.")

async def post_init(application):
    """
    Configura los comandos del bot en la interfaz de Telegram al iniciar
    """
    from telegram import BotCommand
    
    commands = [
        BotCommand("start", "Iniciar el bot y ver el menú principal"),
        BotCommand("price", "Gestionar precios de planes"),
        BotCommand("adduser", "Agregar saldo a un usuario"),
        BotCommand("deluser", "Eliminar saldo o usuarios"),
        BotCommand("credits", "Ver créditos disponibles de un usuario"),
        BotCommand("role", "Cambiar el rol de un usuario"),
        BotCommand("monitor", "Monitor de estado de servidores"),
        BotCommand("reset", "Reiniciar contadores de demos diarios"),
        BotCommand("list", "Listar usuarios del bot"),
        BotCommand("demos", "Ver y gestionar demos"),
        BotCommand("checkdevices", "Verificar límites de dispositivos"),
        BotCommand("check_expired", "Verificar cuentas vencidas (Manual)"),
        BotCommand("list_accounts", "Reporte CSV de cuentas por servidor"),
        BotCommand("cleanup_orphaned", "Limpieza manual de dispositivos huérfanos"),
    ]
    
    await application.bot.set_my_commands(commands)
    logger.info("Comandos del bot configurados correctamente en Telegram")

def main():
    """Función principal que inicia el bot"""
    # Inicializar base de datos
    init_db()
    
    # Crear aplicación
    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Registrar manejador de errores
    application.add_error_handler(error_handler)
    
    # Configurar tareas programadas al inicio usando job_queue directamente
    # En lugar de crear una tarea asíncrona, programamos la función 
    # para que se ejecute una vez cuando el bot ya esté corriendo
    application.job_queue.run_once(setup_jobs_background, 1)
    
    # Registrar manejadores
    # El comando start no requiere autorización previa
    application.add_handler(CommandHandler("start", start_command))
    
    # Comandos que requieren autorización
    application.add_handler(CommandHandler("price", auth_wrapper(price_command)))
    application.add_handler(CommandHandler("adduser", auth_wrapper(adduser_command)))
    application.add_handler(CommandHandler("deluser", auth_wrapper(deluser_command)))
    application.add_handler(CommandHandler("credits", auth_wrapper(credits_command)))
    application.add_handler(CommandHandler("cancel", auth_wrapper(cancel_command)))
    application.add_handler(CommandHandler("role", auth_wrapper(role_command)))
    application.add_handler(CommandHandler("demos", auth_wrapper(demos_command)))
    application.add_handler(CommandHandler("check_expired", auth_wrapper(check_expired_command)))  # NUEVO COMANDO
    application.add_handler(CommandHandler("list_accounts", auth_wrapper(list_accounts_command)))  # NUEVO COMANDO
    application.add_handler(CommandHandler("cleanup_orphaned", auth_wrapper(cleanup_orphaned_command)))  # NUEVO COMANDO
    
    # Nuevos comandos
    application.add_handler(CommandHandler("checkdevices", auth_wrapper(checkdevices_command)))
    application.add_handler(CommandHandler("monitor", auth_wrapper(monitor_command)))
    application.add_handler(CommandHandler("reset", auth_wrapper(reset_command)))
    application.add_handler(CommandHandler("list", auth_wrapper(list_command)))
    
    # Manejador de callbacks para menús (con verificación de autorización)
    application.add_handler(CallbackQueryHandler(auth_callback_query_handler))
    
    # Manejador general para mensajes de texto
    application.add_handler(MessageHandler(filters.TEXT, text_message_handler))
    
    # Manejador para comandos desconocidos
    application.add_handler(MessageHandler(
        filters.COMMAND & ~filters.Command(["start", "price", "adduser", "deluser", "credits", "cancel", "role", "monitor", "reset", "list", "checkdevices", "demos"]), 
        auth_wrapper(lambda update, context: update.message.reply_text(
            "Comando no reconocido. Usa /start para mostrar el menú principal."
        ))
    ))
    
    # Iniciar el bot
    logger.info("Iniciando el bot...")
    application.run_polling()

    logger.info("Bot iniciado correctamente")

if __name__ == "__main__":
    main()
