from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, MessageHandler, filters
from database import Session, User, Price, Account, Server, check_demo_limit
from utils.keyboards import main_menu_keyboard, service_menu_keyboard, back_to_main_menu_keyboard, create_account_keyboard, accounts_menu_keyboard
from utils.helpers import format_credits, get_role_emoji, create_account
from handlers.server_handler import validate_server_connection, add_server_to_db, update_server_in_db, delete_server_from_db
from datetime import datetime
import logging
from database import Role
import io
import csv

logger = logging.getLogger(__name__)

async def handle_callback_query(update: Update, context: CallbackContext):
    """Maneja todas las consultas de callback de los menús"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    
    # Menú principal
    if callback_data == "main_menu":
        await show_main_menu(update, context)
    
    # Menús de servicio
    elif callback_data == "emby_menu":
        await show_service_menu(update, context, "emby")
    elif callback_data == "jellyfin_menu":
        await show_service_menu(update, context, "jellyfin")
    
    # Mis cuentas
    elif callback_data == "my_accounts":
        await show_my_accounts(update, context)
    elif callback_data == "emby_accounts":
        await show_service_accounts(update, context, "emby")
    elif callback_data == "jellyfin_accounts":
        await show_service_accounts(update, context, "jellyfin")
    
    # Precios
    elif callback_data == "prices":
        await show_prices(update, context)
    
    # Crear usuario
    elif callback_data == "emby_create_user":
        await show_create_user_options(update, context, "emby")
    elif callback_data == "jellyfin_create_user":
        await show_create_user_options(update, context, "jellyfin")

    # Eliminar usuario
    elif callback_data == "emby_delete_user":
        await handle_delete_user(update, context, "emby")
    elif callback_data == "jellyfin_delete_user":
        await handle_delete_user(update, context, "jellyfin")
    
    # Crear cuenta específica con selección de servidor
    elif callback_data.startswith(("emby_create_", "jellyfin_create_")):
        parts = callback_data.split("_")
        service = parts[0]
        
        # Verificar si es la parte de selección de servidor
        if "create_on_server" in callback_data:
            # Formato: {service}_create_on_server_{server_id}_{plan}
            server_id = int(parts[4])  # Corrección: parts[4] contiene el ID del servidor
            plan = "_".join(parts[5:])  # Ajustar el índice para el plan también
            await create_user_on_server(update, context, service, server_id, plan)
        # Selección de plan regular
        else:
            plan = "_".join(parts[2:])
            await select_server_for_account(update, context, service, plan)
    
    # Manejar descarga de lista de cuentas
    elif callback_data.startswith("download_accounts_"):
        await handle_download_accounts(update, context)
    
    # Gestión de servidores
    elif callback_data.endswith("_manage_servers"):
        service = callback_data.split("_")[0]
        await show_server_management(update, context, service)
    elif callback_data.endswith("_add_server"):
        service = callback_data.split("_")[0]
        await start_add_server(update, context, service)
    elif callback_data.endswith("_edit_server_list"):
        service = callback_data.split("_")[0]
        await show_server_list(update, context, service, "edit")
    elif callback_data.endswith("_delete_server_list"):
        service = callback_data.split("_")[0]
        await show_server_list(update, context, service, "delete")
    elif "_edit_server_" in callback_data:
        parts = callback_data.split("_")
        service = parts[0]
        server_id = int(parts[-1])
        await start_edit_server(update, context, service, server_id)
    elif "_delete_server_" in callback_data:
        parts = callback_data.split("_")
        service = parts[0]
        server_id = int(parts[-1])
        await confirm_delete_server(update, context, service, server_id)
    elif callback_data.startswith(("emby_confirm_delete_", "jellyfin_confirm_delete_")):
        await handle_server_deletion_confirmation(update, context)
    elif callback_data.endswith("_cancel_delete"):
        service = callback_data.split("_")[0]
        await show_server_management(update, context, service)
    
    # Otras opciones de menú
    elif any(callback_data.startswith(prefix) for prefix in [
        "emby_delete_user", "emby_renew_user", "emby_server_status",
        "jellyfin_delete_user", "jellyfin_renew_user", "jellyfin_server_status"
    ]):
        parts = callback_data.split("_")
        service = parts[0]
        action = "_".join(parts[1:])
        await handle_service_action(update, context, service, action)

async def show_main_menu(update: Update, context: CallbackContext):
    """Muestra el menú principal"""
    query = update.callback_query
    user = query.from_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    welcome_message = (
        f"🎉 ¡Bienvenido al Bot!\n\n"
        f"👤 Usuario: {('@' + user.username) if user.username else user.first_name}\n"
        f"🎭 Rol: {get_role_emoji(db_user.role)} {db_user.role.replace('_', ' ').title()}\n"
        f"💰 Créditos disponibles: {format_credits(db_user.credits)}\n\n"
        f"💫 Selecciona una opción del menú:"
    )
    
    session.close()
    await query.edit_message_text(
        welcome_message,
        reply_markup=main_menu_keyboard()
    )

async def show_service_menu(update: Update, context: CallbackContext, service):
    """Muestra el menú de un servicio específico"""
    query = update.callback_query
    user = query.from_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    user_role = db_user.role
    session.close()
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    service_emoji = "🎬" if service == "emby" else "🍿"
    
    await query.edit_message_text(
        f"{service_emoji} *Gestión de {service_name}*\n\n"
        f"Selecciona una opción:",
        reply_markup=service_menu_keyboard(service, user_role),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_my_accounts(update: Update, context: CallbackContext):
    """Muestra opciones para ver cuentas creadas por el usuario"""
    query = update.callback_query
    
    try:
        await query.edit_message_text(
            "📝 *Mis Cuentas*\n\n"
            "Selecciona el tipo de cuentas que deseas ver:",
            reply_markup=accounts_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        # Si el mensaje ya es el mismo, simplemente lo ignoramos
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error al mostrar cuentas: {e}")
            await query.answer("No se pudo actualizar el mensaje")

async def show_service_accounts(update: Update, context: CallbackContext, service):
    """Muestra las cuentas de un servicio específico"""
    query = update.callback_query
    user = query.from_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    service_upper = service.upper()
    # CORRECCIÓN: Consulta simplificada y filtrar solo cuentas activas
    accounts = session.query(Account).filter(
        Account.user_id == db_user.id,
        Account.service == service_upper,
        Account.is_active == True  # Solo mostrar cuentas activas
    ).all()
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    service_emoji = "🎬" if service == "emby" else "🍿"
    
    try:
        if not accounts:
            await query.edit_message_text(
                f"{service_emoji} *Mis Cuentas de {service_name}*\n\n"
                f"No tienes cuentas activas de {service_name} creadas todavía.",
                reply_markup=accounts_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN
            )
            session.close()
            return
        
        message = f"{service_emoji} *Mis Cuentas de {service_name}*\n\n"
        
        for acc in accounts:
            server = session.query(Server).filter_by(id=acc.server_id).first()
            server_name = server.name if server else "Desconocido"
            
            message += (
                f"• *Usuario:* `{acc.username}`\n"
                f"  *Contraseña:* `{acc.password}`\n"
                f"  *Plan:* {acc.plan.replace('_', ' ').title()}\n"
                f"  *Servidor:* {server_name}\n"
                f"  *Vence:* {acc.expiry_date.strftime('%d/%m/%Y')}\n\n"
            )
        
        await query.edit_message_text(
            message,
            reply_markup=accounts_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        # Si el mensaje ya es el mismo, simplemente lo ignoramos
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error al mostrar cuentas de {service}: {e}")
            await query.answer("No se pudo actualizar el mensaje")
    finally:
        session.close()

async def show_prices(update: Update, context: CallbackContext):
    """Muestra los precios disponibles"""
    query = update.callback_query
    user = query.from_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    emby_prices = session.query(Price).filter_by(service="EMBY", role=db_user.role).all()
    jellyfin_prices = session.query(Price).filter_by(service="JELLYFIN", role=db_user.role).all()
    
    message = "💰 *Precios para tu rol*\n\n"
    
    # Precios de Emby
    message += "🎬 *EMBY:*\n"
    for price in emby_prices:
        message += f"• {price.plan.replace('_', ' ').title()}: {format_credits(price.amount)}\n"
    
    message += "\n🍿 *JELLYFIN:*\n"
    for price in jellyfin_prices:
        message += f"• {price.plan.replace('_', ' ').title()}: {format_credits(price.amount)}\n"
    
    session.close()
    await query.edit_message_text(
        message,
        reply_markup=back_to_main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_create_user_options(update: Update, context: CallbackContext, service):
    """Muestra las opciones para crear usuario"""
    query = update.callback_query
    user = query.from_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    service_upper = service.upper()
    prices = session.query(Price).filter_by(service=service_upper, role=db_user.role).all()
    
    # Verificar límite de demos para mostrar información al usuario
    can_create_demo, current_demo_count, demo_limit = check_demo_limit(db_user.id, session)
    
    # Construir mensaje con precios
    message = f"🆕 *Crear Nueva Cuenta*\n\n"
    message += f"💰 *Tus precios:*\n"
    
    # Mapeo de planes a nombres más amigables
    plan_display = {
        "2_screens": "Cuenta Completa",
        "3_screens": "Cuenta Completa",
        "1_screen": "Perfil",
        "live_tv": "TV en Vivo",
        "2_screens_tv": "TV Completa (2 pantallas)",
        "3_screens_tv": "TV Completa (3 pantallas)"
    }
    
    # Añadir precios
    for price in prices:
        display_name = plan_display.get(price.plan, price.plan.replace('_', ' ').title())
        message += f"• {display_name}: {format_credits(price.amount)}\n"
    
    # Agregar Demo con información del límite
    if can_create_demo:
        message += f"• Demo: Gratis ({current_demo_count}/{demo_limit} del día)\n\n"
    else:
        message += f"• Demo: ❌ Límite alcanzado ({current_demo_count}/{demo_limit} del día)\n\n"
    
    message += f"📝 Selecciona el tipo de cuenta:"
    
    session.close()
    
    # Crear teclado personalizado para la selección de cuenta, pasando el rol del usuario
    reply_markup = create_account_keyboard(service, db_user.role)
    
    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# NUEVAS FUNCIONES PARA SELECCIÓN DE SERVIDOR
async def select_server_for_account(update: Update, context: CallbackContext, service, plan):
    """Muestra servidores disponibles para creación de cuenta"""
    query = update.callback_query
    user = query.from_user
    
    # Verificar límite de demos antes de mostrar servidores
    if plan == 'demo':
        session = Session()
        db_user = session.query(User).filter_by(telegram_id=user.id).first()
        
        can_create, current_count, limit = check_demo_limit(db_user.id, session)
        if not can_create:
            await query.edit_message_text(
                f"❌ *Límite de demos alcanzado*\n\n"
                f"Has creado {current_count}/{limit} demos hoy.\n"
                f"Puedes eliminar una demo existente para crear otra.\n\n"
                f"Para eliminar demos, ve a 'Cuentas creadas' → '{service.upper()} accounts' y usa el comando `/deluser` con el nombre de usuario.",
                reply_markup=create_account_keyboard(service, db_user.role),
                parse_mode=ParseMode.MARKDOWN
            )
            session.close()
            return
        session.close()
    
    # Almacenar el plan seleccionado
    context.user_data['selected_plan'] = plan
    
    session = Session()
    
    # Obtener servidores disponibles
    available_servers = session.query(Server).filter_by(
        service=service.upper(),
        is_active=True
    ).filter(Server.current_users < Server.max_users).all()
    
    if not available_servers:
        await query.edit_message_text(
            f"❌ No hay servidores disponibles para crear cuentas de {service.upper()}.",
            reply_markup=create_account_keyboard(service, "DISTRIBUTOR")  # Volver a selección de plan
        )
        session.close()
        return
    
    # Crear mensaje
    message = f"🖥️ *Selecciona un servidor*\n\n"
    message += f"Plan: {plan.replace('_', ' ').title()}\n"
    
    # Añadir información de demo si aplica
    if plan == 'demo':
        user_db = session.query(User).filter_by(telegram_id=user.id).first()
        can_create, current_count, limit = check_demo_limit(user_db.id, session)
        message += f"Demo: {current_count + 1}/{limit} del día\n"
    
    message += "\n"
    
    # Crear teclado con opciones de servidor
    keyboard = []
    
    for server in available_servers:
        # Calcular porcentajes de uso
        user_percentage = (server.current_users / server.max_users * 100) if server.max_users > 0 else 0
        
        # Obtener indicador de color basado en porcentaje
        if user_percentage < 70:
            indicator = "🟢"  # Verde
        elif user_percentage < 90:
            indicator = "🟠"  # Naranja
        else:
            indicator = "🔴"  # Rojo
        
        # Agregar botón de servidor con información de capacidad
        server_text = f"{indicator} {server.name} ({server.current_users}/{server.max_users})"
        keyboard.append([
            InlineKeyboardButton(
                server_text,
                callback_data=f"{service}_create_on_server_{server.id}_{plan}"
            )
        ])
    
    # Agregar botón de regreso
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data=f"{service}_create_user")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    session.close()

async def create_user_on_server(update: Update, context: CallbackContext, service, server_id, plan):
    """Crea una cuenta de usuario en un servidor específico"""
    query = update.callback_query
    user = query.from_user
    
    # Mostrar mensaje "Creando cuenta..."
    await query.edit_message_text(
        f"⏳ *Creando cuenta {service.upper()}*\n\n"
        f"Plan: {plan.replace('_', ' ').title()}\n\n"
        f"Por favor, espera un momento...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Llamar a la función específica del servicio con server_id
    if service == "emby":
        from handlers.emby_handler import create_emby_account_on_server
        success, result = await create_emby_account_on_server(user.id, plan, server_id)
    else:  # jellyfin
        from handlers.jellyfin_handler import create_jellyfin_account_on_server
        success, result = await create_jellyfin_account_on_server(user.id, plan, server_id)
    
    if success:
        # Construir mensaje de éxito
        success_message = (
            f"✅ *Cuenta creada correctamente*\n\n"
            f"Servicio: {service.upper()}\n"
            f"Plan: {plan.replace('_', ' ').title()}\n"
            f"Usuario: `{result['username']}`\n"
            f"Contraseña: `{result['password']}`\n"
            f"Servidor: {result['server']}\n"
            f"URL: {result['url']}\n"
            f"Fecha de vencimiento: {result['expiry_date'].strftime('%d/%m/%Y')}\n\n"
        )
        
        # Agregar información de demos si aplica
        if plan == 'demo' and 'demo_info' in result:
            success_message += f"📊 {result['demo_info']}\n\n"
        
        await query.edit_message_text(
            success_message,
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.edit_message_text(
            f"❌ *Error al crear la cuenta*\n\n"
            f"Motivo: {result}",
            reply_markup=back_to_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

async def create_user_account(update: Update, context: CallbackContext, service, plan):
    """Inicia el proceso de selección de servidor para crear cuenta"""
    await select_server_for_account(update, context, service, plan)

# FUNCIÓN PARA MANEJAR DESCARGA DE CUENTAS
async def handle_download_accounts(update: Update, context: CallbackContext):
    """Maneja el botón de descarga de cuentas"""
    query = update.callback_query
    await query.answer()
    
    user_telegram_id = int(query.data.split('_')[2])
    
    session = Session()
    
    try:
        # Obtener usuario
        target_user = session.query(User).filter_by(telegram_id=user_telegram_id).first()
        
        if not target_user:
            await query.edit_message_text("❌ Usuario no encontrado.")
            session.close()
            return
        
        # Obtener todas las cuentas para este usuario
        accounts = session.query(Account).filter_by(user_id=target_user.id).all()
        
        if not accounts:
            await query.message.reply_text(
                f"📝 El usuario {target_user.full_name} no tiene cuentas creadas."
            )
            session.close()
            return
        
        # Crear archivo CSV en memoria
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Escribir encabezado
        writer.writerow([
            'Servicio', 'Usuario', 'Contraseña', 'Plan', 'Servidor', 'URL',
            'Fecha de creación', 'Fecha de vencimiento', 'Estado'
        ])
        
        # Escribir datos de cuentas
        for account in accounts:
            server = session.query(Server).filter_by(id=account.server_id).first()
            server_name = server.name if server else "Desconocido"
            server_url = server.url if server else "Desconocido"
            
            writer.writerow([
                account.service,
                account.username,
                account.password,
                account.plan.replace('_', ' ').title(),
                server_name,
                server_url,
                account.created_date.strftime('%Y-%m-%d'),
                account.expiry_date.strftime('%Y-%m-%d'),
                'Activo' if account.is_active else 'Inactivo'
            ])
        
        # Preparar archivo para envío
        output.seek(0)
        filename = f"cuentas_{target_user.full_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        # Enviar archivo
        await query.message.reply_document(
            document=io.BytesIO(output.getvalue().encode('utf-8')),
            filename=filename,
            caption=f"📊 Cuentas de {target_user.full_name}"
        )
    
    except Exception as e:
        logger.error(f"Error al generar archivo de cuentas: {e}")
        await query.message.reply_text(f"❌ Error al generar archivo: {str(e)}")
    
    finally:
        session.close()

async def handle_delete_user(update: Update, context: CallbackContext, service):
    """Inicia el proceso de eliminación de usuario"""
    query = update.callback_query
    
    # Guardar el servicio en el contexto del usuario
    context.user_data['delete_user_service'] = service
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    
    await query.edit_message_text(
        f"🗑️ *Eliminar Usuario {service_name}*\n\n"
        f"Por favor, envía el nombre de usuario que deseas eliminar.\n"
        f"Ejemplo: Demo4598AP\n\n"
        f"Envía /cancel para cancelar el proceso.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Configurar el manejador para la respuesta
    context.user_data['expecting_username_delete'] = True

async def handle_service_action(update: Update, context: CallbackContext, service, action):
    """Maneja acciones específicas de los servicios"""
    query = update.callback_query
    user = query.from_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    user_role = db_user.role
    session.close()
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    
    # Manejar acción de renovar usuario
    if action == "renew_user":
        context.user_data['renew_user_service'] = service
        
        await query.edit_message_text(
            f"🔄 *Renovar Usuario {service_name}*\n\n"
            f"Por favor, envía el nombre de usuario y la duración de la renovación en el formato:\n"
            f"`username 30d`\n\n"
            f"Ejemplo: `User1234AB 30d` para renovar 30 días\n\n"
            f"Envía /cancel para cancelar el proceso.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Establecer bandera para esperar entrada de renovación
        context.user_data['expecting_renewal_input'] = True
        return
    
    # Manejar acción de estado de servidores
    elif action == "server_status":
        await show_server_status(update, context, service)
        return
    
    # Por defecto para otras acciones o si no están implementadas
    message = f"🔧 Función en desarrollo: {action.replace('_', ' ').title()} para {service_name}"
    
    await query.edit_message_text(
        message,
        reply_markup=service_menu_keyboard(service, user_role)
    )

async def handle_renewal_input(update: Update, context: CallbackContext):
    """Maneja la entrada para renovación de usuario y duración"""
    message_text = update.message.text.strip()
    
    # Cancelar si se solicita
    if message_text.lower() == '/cancel':
        context.user_data.clear()
        await update.message.reply_text(
            "❌ Proceso cancelado. Volviendo al menú principal.",
            reply_markup=main_menu_keyboard()
        )
        return
    
    # Analizar entrada: usuario y duración
    parts = message_text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "⚠️ Formato incorrecto. Por favor, usa el formato: `username 30d`\n"
            "Donde el número representa la cantidad de días a renovar.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    username = parts[0].strip()
    duration_text = parts[1].strip()
    
    # Analizar duración (ej., "30d" -> 30 días)
    if not duration_text.endswith('d'):
        await update.message.reply_text(
            "⚠️ Formato de duración incorrecto. Por favor, usa el formato: `30d`\n"
            "Donde el número representa la cantidad de días a renovar.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        duration_days = int(duration_text[:-1])
        if duration_days <= 0:
            raise ValueError("La duración debe ser positiva")
    except ValueError:
        await update.message.reply_text(
            "⚠️ La duración debe ser un número positivo de días."
        )
        return
    
    service = context.user_data.get('renew_user_service', 'emby')
    
    # Procesar renovación según el servicio
    status_message = await update.message.reply_text(
        f"🔄 Procesando renovación para {username} por {duration_days} días... Por favor, espera."
    )
    
    if service == "emby":
        from handlers.emby_handler import renew_emby_account
        success, result = await renew_emby_account(update.effective_user.id, username, duration_days)
    else:  # jellyfin
        from handlers.jellyfin_handler import renew_jellyfin_account
        success, result = await renew_jellyfin_account(update.effective_user.id, username, duration_days)
    
    # Actualizar mensaje con el resultado
    if success:
        await status_message.edit_text(
            f"✅ *Usuario renovado correctamente*\n\n"
            f"Servicio: {service.upper()}\n"
            f"Plan: {result['plan'].replace('_', ' ').title()}\n"
            f"Usuario: `{result['username']}`\n"
            f"Contraseña: `{result['password']}`\n"
            f"Servidor: {result['server']}\n"
            f"URL: {result['url']}\n"
            f"Nueva fecha de vencimiento: {result['expiry_date'].strftime('%d/%m/%Y')}\n\n",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await status_message.edit_text(
            f"❌ *Error al renovar la cuenta*\n\n"
            f"Motivo: {result}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # Limpiar datos de contexto
    context.user_data.clear()
    
    # Volver al menú principal
    await update.message.reply_text(
        "Volviendo al menú principal.",
        reply_markup=main_menu_keyboard()
    )

# FUNCIONES PARA GESTIÓN DE SERVIDORES

def server_management_keyboard(service):
    """Teclado para gestión de servidores"""
    keyboard = [
        [InlineKeyboardButton("➕ Agregar servidor", callback_data=f"{service}_add_server")],
        [InlineKeyboardButton("✏️ Editar servidor", callback_data=f"{service}_edit_server_list")],
        [InlineKeyboardButton("🗑️ Eliminar servidor", callback_data=f"{service}_delete_server_list")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"{service}_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def server_list_keyboard(service, servers, action):
    """Teclado para mostrar lista de servidores para editar o eliminar"""
    keyboard = []
    for server in servers:
        keyboard.append([InlineKeyboardButton(
            f"{server.name} ({server.current_users}/{server.max_users})",
            callback_data=f"{service}_{action}_server_{server.id}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data=f"{service}_manage_servers")])
    return InlineKeyboardMarkup(keyboard)

async def show_server_management(update: Update, context: CallbackContext, service):
    """Muestra el menú de gestión de servidores"""
    query = update.callback_query
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    service_emoji = "🎬" if service == "emby" else "🍿"
    
    await query.edit_message_text(
        f"{service_emoji} *Gestión de Servidores {service_name}*\n\n"
        f"Selecciona una opción:",
        reply_markup=server_management_keyboard(service),
        parse_mode=ParseMode.MARKDOWN
    )

async def start_add_server(update: Update, context: CallbackContext, service):
    """Inicia el proceso de agregar un nuevo servidor"""
    query = update.callback_query
    
    # Guardar el servicio en el contexto del usuario
    context.user_data['add_server_service'] = service
    context.user_data['add_server_step'] = 'url'
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    
    await query.edit_message_text(
        f"🖥️ *Agregar Nuevo Servidor {service_name}*\n\n"
        f"Por favor, envía la URL o IP del servidor.\n"
        f"Ejemplo: https://emby09fullcon.xyz/ o http://37.60.240.131:8096\n\n"
        f"Envía /cancel para cancelar el proceso.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Eliminar manejadores existentes si los hay
    if 'add_server_handler' in context.chat_data:
        application = context.application
        application.remove_handler(context.chat_data['add_server_handler'])
    
    # Agregar nuevo manejador
    handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_server_input)
    context.chat_data['add_server_handler'] = handler
    context.application.add_handler(handler)

async def handle_add_server_input(update: Update, context: CallbackContext):
    """Maneja la entrada de datos para agregar un servidor"""
    message_text = update.message.text
    
    # Verificar si estamos en un proceso de agregar servidor
    if 'add_server_step' not in context.user_data:
        return
    
    current_step = context.user_data['add_server_step']
    service = context.user_data['add_server_service']
    service_name = "Emby" if service == "emby" else "Jellyfin"
    
    # Cancelar el proceso
    if message_text.lower() == '/cancel':
        # Limpiar datos
        context.user_data.clear()
        
        # Eliminar el manejador
        if 'add_server_handler' in context.chat_data:
            context.application.remove_handler(context.chat_data['add_server_handler'])
            del context.chat_data['add_server_handler']
        
        await update.message.reply_text(
            "❌ Proceso cancelado. Volviendo al menú principal.",
            reply_markup=main_menu_keyboard()
        )
        return
    
    # Manejar cada paso del proceso
    if current_step == 'url':
        context.user_data['server_url'] = message_text.strip()
        context.user_data['add_server_step'] = 'api_key'
        
        await update.message.reply_text(
            f"📝 Por favor, envía el token de autenticación (API key) del servidor {service_name}.\n"
            f"Ejemplo: 45ec2e0bc10749808ee1980514e87497\n\n"
            f"Envía /cancel para cancelar el proceso."
        )
    
    elif current_step == 'api_key':
        context.user_data['server_api_key'] = message_text.strip()
        context.user_data['add_server_step'] = 'admin_username'
        
        await update.message.reply_text(
            f"👤 Por favor, envía el nombre de usuario del administrador del servidor {service_name}.\n"
            f"Ejemplo: emby009\n\n"
            f"Envía /cancel para cancelar el proceso."
        )
    
    elif current_step == 'admin_username':
        context.user_data['server_admin_username'] = message_text.strip()
        context.user_data['add_server_step'] = 'max_devices'
        
        await update.message.reply_text(
            f"📱 Por favor, envía el límite máximo de dispositivos para este servidor.\n"
            f"Ejemplo: 80\n\n"
            f"Envía /cancel para cancelar el proceso."
        )
    
    elif current_step == 'max_devices':
        try:
            max_devices = int(message_text.strip())
            if max_devices <= 0:
                raise ValueError("El número debe ser positivo")
            
            context.user_data['server_max_devices'] = max_devices
            context.user_data['add_server_step'] = 'max_users'
            
            await update.message.reply_text(
                f"👥 Por favor, envía el límite máximo de usuarios para este servidor.\n"
                f"Ejemplo: 70\n\n"
                f"Envía /cancel para cancelar el proceso."
            )
        except ValueError:
            await update.message.reply_text(
                "⚠️ Por favor, ingresa un número entero positivo.\n"
                "Inténtalo nuevamente o envía /cancel para cancelar."
            )
    
    elif current_step == 'max_users':
        try:
            max_users = int(message_text.strip())
            if max_users <= 0:
                raise ValueError("El número debe ser positivo")
            
            context.user_data['server_max_users'] = max_users
            
            # Notificar al usuario que estamos verificando
            status_message = await update.message.reply_text(
                "🔄 Verificando conexión con el servidor... Por favor, espera."
            )
            
            # Validar el servidor
            url = context.user_data['server_url']
            api_key = context.user_data['server_api_key']
            admin_username = context.user_data['server_admin_username']
            
            # Validar servidor
            success, result = await validate_server_connection(url, api_key, admin_username, service)
            
            if success:
                # Guardar el servidor en la base de datos
                admin_id = result['admin_id']
                server_name = result['server_name']
                
                add_success, add_message = await add_server_to_db(
                    service=service,
                    url=url,
                    api_key=api_key,
                    admin_username=admin_username,
                    admin_id=admin_id,
                    server_name=server_name,
                    max_devices=context.user_data['server_max_devices'],
                    max_users=context.user_data['server_max_users']
                )
                
                if add_success:
                    await status_message.edit_text(
                        f"✅ Servidor agregado correctamente:\n"
                        f"• Nombre: {server_name}\n"
                        f"• URL: {url}\n"
                        f"• Máx. dispositivos: {context.user_data['server_max_devices']}\n"
                        f"• Máx. usuarios: {context.user_data['server_max_users']}\n\n"
                        f"Puedes seguir gestionando servidores o volver al menú principal."
                    )
                else:
                    await status_message.edit_text(
                        f"❌ Error al guardar el servidor:\n{add_message}\n\n"
                        f"Por favor, intenta nuevamente o contacta al administrador."
                    )
            else:
                await status_message.edit_text(
                    f"❌ Error de validación:\n{result}\n\n"
                    f"Por favor, verifica los datos e intenta nuevamente."
                )
            
            # Limpiar datos y eliminar manejador
            context.user_data.clear()
            if 'add_server_handler' in context.chat_data:
                context.application.remove_handler(context.chat_data['add_server_handler'])
                del context.chat_data['add_server_handler']
            
            # Mostrar el menú de gestión de servidores
            keyboard = server_management_keyboard(service)
            await update.message.reply_text(
                f"Volviendo al menú de gestión de servidores {service_name}",
                reply_markup=keyboard
            )
        
        except ValueError:
            await update.message.reply_text(
                "⚠️ Por favor, ingresa un número entero positivo.\n"
                "Inténtalo nuevamente o envía /cancel para cancelar."
            )

async def show_server_list(update: Update, context: CallbackContext, service, action):
    """Muestra la lista de servidores para editar o eliminar"""
    query = update.callback_query
    
    session = Session()
    servers = session.query(Server).filter_by(service=service.upper()).all()
    session.close()
    
    service_name = "Emby" if service == "emby" else "Jellyfin"
    action_name = "Editar" if action == "edit" else "Eliminar"
    
    if not servers:
        await query.edit_message_text(
            f"⚠️ No hay servidores {service_name} configurados.\n\n"
            f"Por favor, agrega un servidor primero.",
            reply_markup=server_management_keyboard(service)
        )
        return
    
    await query.edit_message_text(
        f"🖥️ *{action_name} Servidores {service_name}*\n\n"
        f"Selecciona un servidor:",
        reply_markup=server_list_keyboard(service, servers, action),
        parse_mode=ParseMode.MARKDOWN
    )

async def start_edit_server(update: Update, context: CallbackContext, service, server_id):
    """Inicia el proceso de edición de un servidor"""
    query = update.callback_query
    
    session = Session()
    server = session.query(Server).filter_by(id=server_id).first()
    session.close()
    
    if not server:
        await query.edit_message_text(
            "❌ Servidor no encontrado.",
            reply_markup=server_management_keyboard(service)
        )
        return
    
    # Guardar datos en el contexto del usuario
    context.user_data['edit_server_id'] = server_id
    context.user_data['edit_server_service'] = service
    context.user_data['edit_server_step'] = 'url'
    context.user_data['edit_server_original'] = {
        'url': server.url,
        'api_key': server.api_key,
        'name': server.name,
        'max_devices': server.max_devices,
        'max_users': server.max_users
    }
    
    await query.edit_message_text(
        f"✏️ *Editar Servidor: {server.name}*\n\n"
        f"URL actual: {server.url}\n\n"
        f"Envía la nueva URL o envía el mismo valor para mantenerlo.\n"
        f"Envía /cancel para cancelar el proceso.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Eliminar manejadores existentes si los hay
    if 'edit_server_handler' in context.chat_data:
        application = context.application
        application.remove_handler(context.chat_data['edit_server_handler'])
    
    # Agregar nuevo manejador
    handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_server_input)
    context.chat_data['edit_server_handler'] = handler
    context.application.add_handler(handler)

async def handle_edit_server_input(update: Update, context: CallbackContext):
    """Maneja la entrada de datos para editar un servidor"""
    message_text = update.message.text
    
    # Verificar si estamos en un proceso de editar servidor
    if 'edit_server_step' not in context.user_data:
        return
    
    current_step = context.user_data['edit_server_step']
    service = context.user_data['edit_server_service']
    server_id = context.user_data['edit_server_id']
    original = context.user_data['edit_server_original']
    
    # Cancelar el proceso
    if message_text.lower() == '/cancel':
        # Limpiar datos
        context.user_data.clear()
        
        # Eliminar el manejador
        if 'edit_server_handler' in context.chat_data:
            context.application.remove_handler(context.chat_data['edit_server_handler'])
            del context.chat_data['edit_server_handler']
        
        keyboard = server_management_keyboard(service)
        await update.message.reply_text(
            "❌ Proceso cancelado. Volviendo al menú de gestión de servidores.",
            reply_markup=keyboard
        )
        return
    
    # Manejar cada paso del proceso
    if current_step == 'url':
        # Usar el valor original si se envía vacío
        new_url = message_text.strip() if message_text.strip() else original['url']
        context.user_data['new_url'] = new_url
        context.user_data['edit_server_step'] = 'api_key'
        
        await update.message.reply_text(
            f"🔑 API Key actual: {original['api_key']}\n\n"
            f"Envía la nueva API Key o envía el mismo valor para mantenerlo.\n"
            f"Envía /cancel para cancelar el proceso."
        )
    
    elif current_step == 'api_key':
        new_api_key = message_text.strip() if message_text.strip() else original['api_key']
        context.user_data['new_api_key'] = new_api_key
        context.user_data['edit_server_step'] = 'name'
        
        await update.message.reply_text(
            f"📝 Nombre actual: {original['name']}\n\n"
            f"Envía el nuevo nombre para el servidor o envía el mismo valor para mantenerlo.\n"
            f"Envía /cancel para cancelar el proceso."
        )
    
    elif current_step == 'name':
        new_name = message_text.strip() if message_text.strip() else original['name']
        context.user_data['new_name'] = new_name
        context.user_data['edit_server_step'] = 'max_devices'
        
        await update.message.reply_text(
            f"📱 Límite máximo de dispositivos actual: {original['max_devices']}\n\n"
            f"Envía el nuevo límite o envía el mismo valor para mantenerlo.\n"
            f"Envía /cancel para cancelar el proceso."
        )
    
    elif current_step == 'max_devices':
        try:
            max_devices_text = message_text.strip()
            new_max_devices = int(max_devices_text) if max_devices_text else original['max_devices']
            
            if new_max_devices <= 0:
                raise ValueError("El número debe ser positivo")
            
            context.user_data['new_max_devices'] = new_max_devices
            context.user_data['edit_server_step'] = 'max_users'
            
            await update.message.reply_text(
                f"👥 Límite máximo de usuarios actual: {original['max_users']}\n\n"
                f"Envía el nuevo límite o envía el mismo valor para mantenerlo.\n"
                f"Envía /cancel para cancelar el proceso."
            )
        except ValueError:
            await update.message.reply_text(
                "⚠️ Por favor, ingresa un número entero positivo.\n"
                "Inténtalo nuevamente o envía /cancel para cancelar."
            )
    
    elif current_step == 'max_users':
        try:
            max_users_text = message_text.strip()
            new_max_users = int(max_users_text) if max_users_text else original['max_users']
            
            if new_max_users <= 0:
                raise ValueError("El número debe ser positivo")
            
            # Actualizar el servidor
            status_message = await update.message.reply_text(
                "🔄 Actualizando información del servidor... Por favor, espera."
            )
            
            success, result = await update_server_in_db(
                server_id=server_id,
                url=context.user_data['new_url'],
                api_key=context.user_data['new_api_key'],
                name=context.user_data['new_name'],
                max_devices=context.user_data['new_max_devices'],
                max_users=new_max_users
            )
            
            if success:
                await status_message.edit_text(
                    f"✅ Servidor actualizado correctamente:\n"
                    f"• Nombre: {context.user_data['new_name']}\n"
                    f"• URL: {context.user_data['new_url']}\n"
                    f"• Máx. dispositivos: {context.user_data['new_max_devices']}\n"
                    f"• Máx. usuarios: {new_max_users}\n\n"
                    f"Puedes seguir gestionando servidores o volver al menú principal."
                )
            else:
                await status_message.edit_text(
                    f"❌ Error al actualizar el servidor:\n{result}\n\n"
                    f"Por favor, intenta nuevamente o contacta al administrador."
                )
            
            # Limpiar datos y eliminar manejador
            context.user_data.clear()
            if 'edit_server_handler' in context.chat_data:
                context.application.remove_handler(context.chat_data['edit_server_handler'])
                del context.chat_data['edit_server_handler']
            
            # Mostrar el menú de gestión de servidores
            keyboard = server_management_keyboard(service)
            await update.message.reply_text(
                "Volviendo al menú de gestión de servidores",
                reply_markup=keyboard
            )
        
        except ValueError:
            await update.message.reply_text(
                "⚠️ Por favor, ingresa un número entero positivo.\n"
                "Inténtalo nuevamente o envía /cancel para cancelar."
            )

async def confirm_delete_server(update: Update, context: CallbackContext, service, server_id):
    """Pide confirmación para eliminar un servidor"""
    query = update.callback_query
    
    session = Session()
    server = session.query(Server).filter_by(id=server_id).first()
    
    if not server:
        await query.edit_message_text(
            "❌ Servidor no encontrado.",
            reply_markup=server_management_keyboard(service)
        )
        session.close()
        return
    
    # Verificar si hay cuentas asociadas
    from database import Account
    accounts_count = session.query(Account).filter_by(server_id=server_id).count()
    session.close()
    
    # Crear teclado de confirmación
    keyboard = [
        [
            InlineKeyboardButton("✅ Sí, eliminar", callback_data=f"{service}_confirm_delete_{server_id}"),
            InlineKeyboardButton("❌ No, cancelar", callback_data=f"{service}_cancel_delete")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    warning_message = ""
    if accounts_count > 0:
        warning_message = f"⚠️ Este servidor tiene {accounts_count} cuentas asociadas que se marcarán como inactivas.\n\n"
    
    await query.edit_message_text(
        f"⚠️ *¿Estás seguro de eliminar este servidor?*\n\n"
        f"• Nombre: {server.name}\n"
        f"• URL: {server.url}\n"
        f"• Servicio: {server.service}\n\n"
        f"{warning_message}"
        f"Esta acción no se puede deshacer.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_server_input(update: Update, context: CallbackContext):
    """Función centralizada para manejar la entrada de datos del servidor"""
    if 'add_server_step' in context.user_data:
        await handle_add_server_input(update, context)
    elif 'edit_server_step' in context.user_data:
        await handle_edit_server_input(update, context)
    elif 'expecting_renewal_input' in context.user_data and context.user_data['expecting_renewal_input']:
        await handle_renewal_input(update, context)
    elif 'expecting_username_delete' in context.user_data and context.user_data['expecting_username_delete']:
        await handle_username_delete(update, context)

async def handle_server_deletion_confirmation(update: Update, context: CallbackContext):
    """Maneja la confirmación para eliminar un servidor"""
    query = update.callback_query
    callback_data = query.data
    
    parts = callback_data.split("_")
    service = parts[0]
    server_id = int(parts[-1])
    
    # Pasar force=True para forzar la eliminación y marcar cuentas como inactivas
    success, result = await delete_server_from_db(server_id, force=True)
    
    if success:
        await query.edit_message_text(
            f"✅ {result}",
            reply_markup=server_management_keyboard(service)
        )
    else:
        await query.edit_message_text(
            f"❌ {result}",
            reply_markup=server_management_keyboard(service)
        )

async def handle_username_delete(update: Update, context: CallbackContext):
    """Maneja la entrada del nombre de usuario a eliminar"""
    username = update.message.text.strip()
    
    # Cancelar el proceso si se solicita
    if username.lower() == '/cancel':
        context.user_data.clear()
        await update.message.reply_text(
            "❌ Proceso cancelado. Volviendo al menú principal.",
            reply_markup=main_menu_keyboard()
        )
        return
    
    service = context.user_data.get('delete_user_service', 'emby')
    service_name = "Emby" if service == "emby" else "Jellyfin"
    
    # Notificar que estamos procesando
    status_message = await update.message.reply_text(
        f"🔄 Eliminando usuario {username} de {service_name}... Por favor, espera."
    )
    
    # Procesar según el servicio
    if service == "emby":
        from handlers.emby_handler import delete_emby_account
        success, result = await delete_emby_account(username)
    else:
        # Implementar para Jellyfin
        from handlers.jellyfin_handler import delete_jellyfin_account
        success, result = await delete_jellyfin_account(username)
    
    # Actualizar mensaje con el resultado
    if success:
        await status_message.edit_text(
            f"✅ {result}\n\n"
            f"El usuario ha sido eliminado correctamente."
        )
    else:
        await status_message.edit_text(
            f"❌ Error: {result}\n\n"
            f"Por favor, verifica el nombre de usuario e intenta nuevamente."
        )
    
    # Limpiar datos del contexto
    context.user_data.clear()
    
    # Mostrar menú principal
    await update.message.reply_text(
        "Volviendo al menú principal.",
        reply_markup=main_menu_keyboard()
    )

async def show_server_status(update: Update, context: CallbackContext, service):
    """Muestra el estado de los servidores"""
    query = update.callback_query
    
    # Mostrar mensaje de carga
    await query.edit_message_text(
        f"🔄 Obteniendo información de los servidores {service.upper()}... Por favor, espera."
    )
    
    # Obtener estado de servidores
    if service == "emby":
        from handlers.emby_handler import get_emby_servers_status
        success, result = await get_emby_servers_status()
    else:  # jellyfin
        from handlers.jellyfin_handler import get_jellyfin_servers_status
        success, result = await get_jellyfin_servers_status()
    
    # Construir mensaje basado en el resultado
    if success:
        service_name = "EMBY" if service == "emby" else "JELLYFIN"
        message = f"📊 ESTADO DE SERVIDORES {service_name}\n\n"
        
        for server_status in result:
            # Determinar emoji de estado
            online_status = "✅ ONLINE" if server_status['online'] else "❌ OFFLINE"
            
            # Determinar indicadores de color basados en porcentajes
            def get_color_indicator(percentage):
                if percentage < 70:
                    return "🟢"  # Verde
                elif percentage < 90:
                    return "🟠"  # Naranja
                else:
                    return "🔴"  # Rojo
            
            user_color = get_color_indicator(server_status['users_percentage'])
            
            # CORRECCIÓN para Jellyfin: usar dispositivos registrados reales si están disponibles
            if service == "jellyfin" and 'total_registered_devices' in server_status:
                total_devices = server_status['total_registered_devices']
                devices_percentage = (total_devices / server_status['max_devices'] * 100) if server_status['max_devices'] > 0 else 0
                device_color = get_color_indicator(devices_percentage)
                
                message += (
                    f"{online_status} {server_status['name']}\n"
                    f"   🌐 URL: {server_status['url']}\n"
                    f"   {user_color} Usuarios: {server_status['db_users']}/{server_status['max_users']} "
                    f"({server_status['users_percentage']:.1f}%)\n"
                    f"   {device_color} Dispositivos registrados: {total_devices}/{server_status['max_devices']} "
                    f"({devices_percentage:.1f}%)\n"
                    f"   👥 Usuarios conectados: {server_status['active_users']}\n"
                    f"   📱 Dispositivos activos: {server_status['active_devices']}\n\n"
                )
            else:
                # Para Emby o si no hay datos de dispositivos registrados
                device_color = get_color_indicator(server_status['devices_percentage'])
                
                message += (
                    f"{online_status} {server_status['name']}\n"
                    f"   🌐 URL: {server_status['url']}\n"
                    f"   {user_color} Usuarios: {server_status['db_users']}/{server_status['max_users']} "
                    f"({server_status['users_percentage']:.1f}%)\n"
                    f"   {device_color} Dispositivos: {server_status['db_devices']}/{server_status['max_devices']} "
                    f"({server_status['devices_percentage']:.1f}%)\n"
                    f"   👥 Usuarios conectados: {server_status['active_users']}\n"
                    f"   📱 Dispositivos conectados: {server_status['active_devices']}\n\n"
                )
        
        # Agregar botón de regreso
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data=f"{service}_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup
        )
    else:
        # Mensaje de error
        await query.edit_message_text(
            f"❌ Error al obtener el estado de los servidores: {result}",
            reply_markup=service_menu_keyboard(service, "ADMIN")  # Por defecto ADMIN para caso de error
        )
