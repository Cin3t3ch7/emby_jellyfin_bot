import logging
from datetime import datetime
import asyncio
import concurrent.futures
import httpx
from sqlalchemy import and_
from database import Session, Account, Server, User as DbUser, get_db_session
from handlers.emby_handler import delete_emby_user
from handlers.jellyfin_handler import delete_jellyfin_user
from audit_logger import (
    log_expired_accounts_cleanup,
    log_device_cleanup,
    log_device_limit_enforcement,
    log_error
)

logger = logging.getLogger(__name__)

# Executor para operaciones bloqueantes
executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

# Configuración de límites de dispositivos por plan (centralizada)
DEVICE_LIMITS = {
    'EMBY': {
        '1_screen': 1,
        'live_tv': 1,
        'demo': 1,
        '2_screens': 2,
        '2_screens_tv': 2,
        'bulk': 3,
        '3_screens': 3,
        '3_screens_tv': 3
    },
    'JELLYFIN': {
        '1_screen': 1,
        'live_tv': 1,
        'demo': 1,
        '3_screens': 3,
        '3_screens_tv': 3,
        'bulk': 5,
        '2_screens': 2,
        '2_screens_tv': 2
    }
}


def get_color_indicator(percentage):
    """
    Obtiene un indicador de color basado en el porcentaje de uso

    Args:
        percentage: Porcentaje de uso (0-100)

    Returns:
        str: Emoji indicador (🟢, 🟠, 🔴)
    """
    if percentage < 70:
        return "🟢"  # Verde
    elif percentage < 90:
        return "🟠"  # Naranja
    else:
        return "🔴"  # Rojo

# Funciones auxiliares para ejecutar tareas en segundo plano
# NOTA: Esta función se mantiene por compatibilidad pero no se recomienda su uso
# debido a problemas con event loops anidados. Usar directamente await en funciones async.

async def check_account_exists(server, user_id, service):
    """
    Verifica si una cuenta existe en el servidor

    Args:
        server: Objeto Server de la base de datos
        user_id: ID del usuario en el servicio
        service: "EMBY" o "JELLYFIN"

    Returns:
        bool: True si la cuenta existe, False en caso contrario
    """
    try:
        # Eliminar la barra final si existe
        url = server.url
        if url.endswith('/'):
            url = url[:-1]

        if service == "EMBY":
            # Verificar si el usuario existe en Emby
            check_url = f"{url}/emby/Users/{user_id}?api_key={server.api_key}"
        else:  # JELLYFIN
            # Verificar si el usuario existe en Jellyfin
            check_url = f"{url}/Users/{user_id}?api_key={server.api_key}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(check_url)
            # Si la respuesta es 200, el usuario existe
            if response.status_code == 200:
                return True

            # Si la respuesta es 404, el usuario no existe
            if response.status_code == 404:
                return False

            # Otros códigos podrían indicar problemas de conexión
            logger.warning(f"Código de respuesta inesperado al verificar usuario: {response.status_code}")
            return True  # Asumimos que existe para ser conservadores

    except Exception as e:
        logger.error(f"Error al verificar si la cuenta existe: {e}")
        return True  # En caso de error, asumimos que existe para ser conservadores

async def check_expired_accounts(*args, **kwargs):
    """
    Verifica y procesa las cuentas vencidas.
    - Todas las cuentas vencidas se eliminan completamente del servidor y de la base de datos
    """
    logger.info("Iniciando verificación de cuentas vencidas...")
    
    session = Session()
    now = datetime.utcnow()
    
    try:
        # Obtener todas las cuentas activas vencidas
        expired_accounts = session.query(Account).filter(
            and_(
                Account.is_active == True,
                Account.expiry_date < now
            )
        ).all()
        
        if not expired_accounts:
            logger.info("No se encontraron cuentas vencidas.")
            session.close()
            return
        
        logger.info(f"Se encontraron {len(expired_accounts)} cuentas vencidas.")
        
        # Procesar cada cuenta vencida
        for account in expired_accounts:
            # Obtener el servidor asociado
            server = session.query(Server).filter_by(id=account.server_id).first()
            
            if not server:
                logger.error(f"No se encontró el servidor para la cuenta {account.username}")
                continue
                
            service_user_id = account.service_user_id
            if not service_user_id:
                logger.error(f"No se encontró el ID de servicio para la cuenta {account.username}")
                continue
            
            # Verificar si la cuenta aún existe en el servidor
            account_exists = await check_account_exists(server, service_user_id, account.service)
            
            if not account_exists:
                logger.info(f"La cuenta {account.username} ya no existe en el servidor.")
                
                # Eliminar completamente de la base de datos
                session.delete(account)
                logger.info(f"Cuenta {account.username} eliminada de la base de datos.")
                
                # Actualizar contador de usuarios en el servidor
                if server.current_users > 0:
                    server.current_users -= 1
                
                continue
            
            # Intentar eliminar la cuenta del servidor
            if account.service == "EMBY":
                success, message = await delete_emby_user(server, service_user_id)
            else:  # JELLYFIN
                success, message = await delete_jellyfin_user(server, service_user_id)
            
            # Si la función retorna True, significa que se eliminó o ya no existe (404)
            if success:
                logger.info(f"Cuenta {account.username} eliminada correctamente del servidor (o ya no existía).")
                
                # Eliminar completamente de la base de datos
                session.delete(account)
                logger.info(f"Cuenta {account.username} eliminada de la base de datos.")
                
                # Actualizar contador de usuarios en el servidor
                if server.current_users > 0:
                    server.current_users -= 1
            else:
                # SI FALLA, NO HACEMOS NADA EN LA BD
                # La cuenta sigue activa y vencida, por lo que en el próximo ciclo
                # (15 min después) se volverá a intentar eliminar.
                # Esto asegura que no queden cuentas "zombies" en el servidor.
                logger.error(f"Error al eliminar cuenta {account.username} del servidor: {message}")
                logger.info(f"La cuenta se mantendrá en cola para reintentar eliminación en el siguiente ciclo.")
        
        # Guardar cambios
        session.commit()
        logger.info("Proceso de verificación de cuentas vencidas completado.")

        # Registrar en auditoría
        log_expired_accounts_cleanup(
            len(expired_accounts),
            f"Procesadas {len(expired_accounts)} cuentas vencidas"
        )

    except Exception as e:
        session.rollback()
        logger.error(f"Error al procesar cuentas vencidas: {e}")
        log_error("check_expired_accounts", str(e))
    finally:
        session.close()

async def send_servers_status_to_admins(context=None):
    """Envía el estado de todos los servidores a los administradores"""
    logger.info("Enviando estado de servidores a los administradores...")
    
    from database import Session, User
    from config import ADMIN_IDS
    from handlers.emby_handler import get_emby_servers_status
    from handlers.jellyfin_handler import get_jellyfin_servers_status
    
    session = Session()
    try:
        # Obtener todos los usuarios con roles admin
        admin_users = session.query(User).filter(User.role.in_(["SUPER_ADMIN", "ADMIN"])).all()
        admin_telegram_ids = [user.telegram_id for user in admin_users]
        
        # Añadir los IDs de admin configurados (por si acaso)
        for admin_id in ADMIN_IDS:
            if admin_id not in admin_telegram_ids:
                admin_telegram_ids.append(admin_id)
        
        # Ejecutar estas operaciones en segundo plano para no bloquear
        emby_success, emby_result = await get_emby_servers_status()
        jellyfin_success, jellyfin_result = await get_jellyfin_servers_status()
        
        # Construir mensaje de resumen
        message = "📊 *REPORTE PERIÓDICO DE ESTADO DE SERVIDORES*\n\n"
        
        # Procesar servidores Emby
        if emby_success and isinstance(emby_result, list) and len(emby_result) > 0:
            message += "*SERVIDORES EMBY:*\n"
            for server in emby_result:
                online_status = "✅ ONLINE" if server['online'] else "❌ OFFLINE"
                
                user_color = get_color_indicator(server['users_percentage'])
                device_color = get_color_indicator(server['devices_percentage'])
                
                message += (
                    f"{online_status} {server['name']}\n"
                    f"   🌐 URL: {server['url']}\n"
                    f"   {user_color} Usuarios: {server['db_users']}/{server['max_users']} "
                    f"({server['users_percentage']:.1f}%)\n"
                    f"   {device_color} Dispositivos: {server['db_devices']}/{server['max_devices']} "
                    f"({server['devices_percentage']:.1f}%)\n"
                    f"   👥 Usuarios conectados: {server['active_users']}\n"
                    f"   📱 Dispositivos conectados: {server['active_devices']}\n\n"
                )
        else:
            message += "*SERVIDORES EMBY:* No hay información disponible\n\n"
        
        # Procesar servidores Jellyfin
        if jellyfin_success and isinstance(jellyfin_result, list) and len(jellyfin_result) > 0:
            message += "*SERVIDORES JELLYFIN:*\n"
            for server in jellyfin_result:
                online_status = "✅ ONLINE" if server['online'] else "❌ OFFLINE"
                
                user_color = get_color_indicator(server['users_percentage'])
                device_color = get_color_indicator(server['devices_percentage'])
                
                message += (
                    f"{online_status} {server['name']}\n"
                    f"   🌐 URL: {server['url']}\n"
                    f"   {user_color} Usuarios: {server['db_users']}/{server['max_users']} "
                    f"({server['users_percentage']:.1f}%)\n"
                    f"   {device_color} Dispositivos: {server['db_devices']}/{server['max_devices']} "
                    f"({server['devices_percentage']:.1f}%)\n"
                    f"   👥 Usuarios conectados: {server['active_users']}\n"
                    f"   📱 Dispositivos conectados: {server['active_devices']}\n\n"
                )
        else:
            message += "*SERVIDORES JELLYFIN:* No hay información disponible\n\n"
        
        # Enviar mensaje a todos los administradores de forma no bloqueante
        if context and context.bot:
            for admin_id in admin_telegram_ids:
                try:
                    # Intentar enviar el mensaje, pero no esperar por su finalización
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=message,
                        parse_mode="MARKDOWN"
                    )
                    logger.info(f"Estado de servidores enviado a admin {admin_id}")
                except Exception as e:
                    logger.error(f"Error al enviar estado a admin {admin_id}: {e}")
        
    except Exception as e:
        logger.error(f"Error al enviar estado de servidores: {e}")
    finally:
        session.close()

async def cleanup_orphaned_devices(context=None):
    """
    Elimina dispositivos huérfanos en todos los servidores
    - Dispositivos huérfanos: aquellos que no están asociados a un usuario activo o en sesión
    """
    logger.info("Iniciando limpieza de dispositivos huérfanos...")
    
    from database import Session, Server
    from handlers.emby_handler import delete_orphaned_emby_devices
    from handlers.jellyfin_handler import delete_orphaned_jellyfin_devices
    import time
    import asyncio
    from sqlalchemy.exc import OperationalError, SQLAlchemyError
    
    # Resultados para el reporte
    total_deleted = 0
    server_details = []
    
    # CORRECCIÓN: Función para obtener los servidores con reintentos usando context manager
    def get_servers_with_retry(service_type, max_retries=3, retry_delay=2):
        for attempt in range(max_retries):
            try:
                # CORRECCIÓN: Usar context manager para garantizar cierre de sesión
                with get_db_session() as session:
                    servers = session.query(Server).filter_by(service=service_type, is_active=True).all()
                    # Hacer una copia de los objetos para no depender de la sesión
                    servers_copy = []
                    for server in servers:
                        servers_copy.append({
                            "id": server.id,
                            "name": server.name,
                            "service": server.service,
                            "url": server.url,
                            "api_key": server.api_key,
                            "max_users": server.max_users,
                            "current_users": server.current_users
                        })
                    return servers_copy
                # session.close() se llama automáticamente al salir del with
            except (OperationalError, SQLAlchemyError) as e:
                logger.warning(f"Error al consultar servidores {service_type}. Intento {attempt+1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error(f"No se pudieron obtener los servidores {service_type} después de {max_retries} intentos")
                    return []
    
    try:
        # Obtener todos los servidores activos con manejo de errores
        emby_servers_data = get_servers_with_retry("EMBY")
        jellyfin_servers_data = get_servers_with_retry("JELLYFIN")
        
        # Procesamiento en paralelo para cada servidor Emby
        emby_tasks = []
        for server_data in emby_servers_data:
            # Recrear objeto Server para la función
            server = Server()
            server.id = server_data["id"]
            server.name = server_data["name"]
            server.service = server_data["service"]
            server.url = server_data["url"]
            server.api_key = server_data["api_key"]
            
            # Crear tarea pero no esperar su finalización inmediata
            emby_tasks.append(delete_orphaned_emby_devices(server))
        
        # Procesamiento en paralelo para cada servidor Jellyfin
        jellyfin_tasks = []
        for server_data in jellyfin_servers_data:
            # Recrear objeto Server para la función
            server = Server()
            server.id = server_data["id"]
            server.name = server_data["name"]
            server.service = server_data["service"]
            server.url = server_data["url"]
            server.api_key = server_data["api_key"]
            
            # Crear tarea pero no esperar su finalización inmediata
            jellyfin_tasks.append(delete_orphaned_jellyfin_devices(server))
        
        # Recopilar resultados de servidores Emby (con manejo de errores)
        for task in asyncio.as_completed(emby_tasks):
            try:
                result = await task
                deleted_count = result[0]
                deleted_devices = result[2] if len(result) > 2 else []
                
                total_deleted += deleted_count
                
                if deleted_count > 0:
                    server_details.append({
                        "name": result[1].split("servidor ")[1] if "servidor " in result[1] else "Emby",
                        "service": "EMBY",
                        "deleted_count": deleted_count,
                        "devices": deleted_devices
                    })
            except Exception as e:
                logger.error(f"Error al procesar resultado de limpieza Emby: {e}")
        
        # Recopilar resultados de servidores Jellyfin (con manejo de errores)
        for task in asyncio.as_completed(jellyfin_tasks):
            try:
                result = await task
                deleted_count = result[0]
                deleted_devices = result[2] if len(result) > 2 else []
                
                total_deleted += deleted_count
                
                if deleted_count > 0:
                    server_details.append({
                        "name": result[1].split("servidor ")[1] if "servidor " in result[1] else "Jellyfin",
                        "service": "JELLYFIN",
                        "deleted_count": deleted_count,
                        "devices": deleted_devices
                    })
            except Exception as e:
                logger.error(f"Error al procesar resultado de limpieza Jellyfin: {e}")
        
        # Si se proporcionó un contexto y hay dispositivos eliminados, enviar un informe DETALLADO
        if context and hasattr(context, 'bot') and total_deleted > 0:
            try:
                # Construir mensaje de reporte específico para DISPOSITIVOS HUÉRFANOS
                report_message = f"🧹 *REPORTE DE LIMPIEZA DE HUÉRFANOS*\n\n"
                report_message += f"Total eliminados: {total_deleted}\n\n"
                
                for server_detail in server_details:
                    server_name = server_detail['name']
                    count = server_detail['deleted_count']
                    devices = server_detail.get('devices', [])
                    
                    report_message += f"🖥️ *{server_name}*: {count} eliminados\n"
                    
                    # Listar dispositivos eliminados
                    for device in devices:
                        dev_name = device.get('device_name', 'Desconocido')
                        app_name = device.get('app_name', '')
                        reason = device.get('reason', '')
                        
                        device_str = f"   • {dev_name}"
                        if app_name:
                            device_str += f" ({app_name})"
                        if reason:
                            device_str += f" - {reason}"
                        report_message += f"{device_str}\n"
                    
                    report_message += "\n"

                # Enviar a admins
                from config import ADMIN_IDS
                from database import Session, User
                session = Session()
                admin_users = session.query(User).filter(User.role.in_(["SUPER_ADMIN", "ADMIN"])).all()
                admin_ids = [user.telegram_id for user in admin_users]
                for aid in ADMIN_IDS:
                    if aid not in admin_ids:
                        admin_ids.append(aid)
                session.close()

                for admin_id in admin_ids:
                    try:
                        await context.bot.send_message(chat_id=admin_id, text=report_message, parse_mode="MARKDOWN")
                    except Exception as e:
                        logger.error(f"Error al enviar reporte de huérfanos a {admin_id}: {e}")

            except Exception as e:
                logger.error(f"Error al generar/enviar informe de limpieza: {e}")
        
        # Actualizar mensaje de estado si es un comando manual
        if context and hasattr(context, 'user_data') and context.user_data is not None and 'status_message' in context.user_data:
            try:
                if total_deleted > 0:
                    await context.user_data['status_message'].edit_text(
                        f"✅ Limpieza completada: {total_deleted} dispositivos huérfanos eliminados."
                    )
                else:
                    await context.user_data['status_message'].edit_text(
                        "✅ No se encontraron dispositivos huérfanos para eliminar."
                    )
            except Exception as e:
                logger.error(f"Error al actualizar mensaje de estado: {e}")
        
        logger.info(f"Limpieza de dispositivos huérfanos completada. Total eliminados: {total_deleted}")

        # Registrar en auditoría
        log_device_cleanup(total_deleted, len(server_details))

    except Exception as e:
        logger.error(f"Error en la limpieza de dispositivos huérfanos: {e}")
        log_error("cleanup_orphaned_devices", str(e))
        
        # Actualizar mensaje de error si es un comando manual
        if context and hasattr(context, 'user_data') and context.user_data is not None and 'status_message' in context.user_data:
            try:
                await context.user_data['status_message'].edit_text(
                    f"❌ Error en la limpieza de dispositivos: {str(e)}"
                )
            except Exception as msg_err:
                logger.error(f"Error adicional al actualizar mensaje de error: {msg_err}")

async def send_device_limits_report(context, total_count, devices_removed, servers_report=None):
    """
    Envía un informe sobre los dispositivos a los administradores
    
    Args:
        context: Contexto del bot
        total_count: Número total de usuarios verificados o servidores revisados
        devices_removed: Número total de dispositivos eliminados
        servers_report: Lista con detalles de los servidores (opcional)
    """
    try:
        from database import Session, User
        from config import ADMIN_IDS
        
        session = Session()
        
        # Obtener todos los usuarios con roles admin
        admin_users = session.query(User).filter(User.role.in_(["SUPER_ADMIN", "ADMIN"])).all()
        admin_telegram_ids = [user.telegram_id for user in admin_users]
        
        # Añadir los IDs de admin configurados (por si acaso)
        for admin_id in ADMIN_IDS:
            if admin_id not in admin_telegram_ids:
                admin_telegram_ids.append(admin_id)
        
        # Determinar el tipo de reporte basado en los argumentos proporcionados
        is_orphan_report = True
        if servers_report and isinstance(servers_report, list) and len(servers_report) > 0:
            # Revisar si el primer servidor tiene la estructura esperada para un reporte de límites de dispositivos
            first_server = servers_report[0]
            if isinstance(first_server, dict) and 'users_details' in first_server:
                is_orphan_report = False
        
        if is_orphan_report:
            # Construir mensaje para reporte de dispositivos huérfanos
            report = f"🧹 *LIMPIEZA DE DISPOSITIVOS HUÉRFANOS*\n\n"
            report += f"🗑️ Dispositivos eliminados: {devices_removed}\n\n"
            
            # Agregar detalles por servidor solo si hay dispositivos eliminados
            if servers_report:
                for server_info in servers_report:
                    if isinstance(server_info, dict):
                        # Nombre del servidor
                        server_name = server_info.get('name', 'Desconocido')
                        
                        # Conteo de dispositivos eliminados - puede estar en deleted_count o devices_removed
                        deleted_count = server_info.get('deleted_count', server_info.get('devices_removed', 0))
                        
                        report += f"🖥️ Servidor {server_name}:\n"
                        report += f"   🗑️ Dispositivos eliminados: {deleted_count}\n"
                        
                        # Agregar detalles de cada dispositivo
                        devices = server_info.get('devices', [])
                        for device in devices:
                            device_name = device.get('device_name', 'Desconocido')
                            device_app = device.get('app_name', '')
                            report += f"   • {device_name} ({device_app})\n"
                    else:
                        # En caso de que sea una lista plana de dispositivos
                        device_name = getattr(server_info, 'device_name', 'Desconocido')
                        device_app = getattr(server_info, 'app_name', '')
                        report += f"   • {device_name} ({device_app})\n"
                    
                    report += "\n"
        else:
            # Construir mensaje para reporte de límites de dispositivos
            report = (
                f"📱 *CONTROL DE LÍMITES DE DISPOSITIVOS*\n\n"
                f"👤 Usuarios verificados: {total_count}\n"
                f"🗑️ Dispositivos eliminados: {devices_removed}\n\n"
            )
            
            # Información por servidor
            if servers_report:
                for server in servers_report:
                    server_name = server.get('name', 'Desconocido')
                    # CORRECCIÓN: Acceder de manera segura a 'devices_removed'
                    devices_removed_count = server.get('devices_removed', 0)
                    
                    report += f"🖥️ *Servidor {server_name}*:\n"
                    report += f"   🗑️ Dispositivos eliminados: {devices_removed_count}\n\n"
                    
                    # Detalles de usuarios
                    users_details = server.get('users_details', [])
                    for user_detail in users_details:
                        username = user_detail.get('username', 'Desconocido')
                        plan = user_detail.get('plan', 'desconocido')
                        limit = user_detail.get('device_limit', '?')
                        total = user_detail.get('total_devices', '?')
                        
                        report += f"   👤 *{username}* (Plan: {plan}, Límite: {limit}, Total: {total}):\n"
                        
                        # Dispositivos eliminados
                        removed_devices = user_detail.get('removed_devices', [])
                        for device in removed_devices:
                            device_name = device.get('name', 'Desconocido')
                            app_name = device.get('app', '')
                            report += f"      • {device_name} {f'({app_name})' if app_name else ''}\n"
                        
                        report += "\n"
        
        # Si no hay dispositivos eliminados
        if devices_removed == 0:
            report += "No se encontraron dispositivos para eliminar.\n"
        
        session.close()
        
        # Enviar a todos los administradores de forma no bloqueante
        if context and hasattr(context, 'bot'):
            for admin_id in admin_telegram_ids:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=report,
                        parse_mode="MARKDOWN"
                    )
                except Exception as e:
                    logger.error(f"Error al enviar reporte a admin {admin_id}: {e}")
    
    except Exception as e:
        logger.error(f"Error al enviar informe de dispositivos: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def check_and_enforce_device_limits(context=None):
    """
    Verifica y elimina dispositivos excedentes para cada usuario según su límite permitido
    """
    logger.info("Iniciando verificación de límites de dispositivos...")
    
    from database import Session, Server, Account, User
    import asyncio
    
    # Variables para el informe
    total_users_checked = 0
    total_devices_removed = 0
    servers_report = []
    
    session = Session()
    try:
        # Obtener todos los servidores activos
        emby_servers = session.query(Server).filter_by(service="EMBY", is_active=True).all()
        jellyfin_servers = session.query(Server).filter_by(service="JELLYFIN", is_active=True).all()
        
        logger.info(f"Encontrados {len(emby_servers)} servidores EMBY activos y {len(jellyfin_servers)} servidores JELLYFIN activos")
        
        # Verificar si hay servidores para procesar
        if not emby_servers and not jellyfin_servers:
            logger.warning("No hay servidores activos configurados para verificar")
            # Verificar si el contexto tiene user_data y status_message antes de acceder
            if context and hasattr(context, 'user_data') and context.user_data and 'status_message' in context.user_data:
                await context.user_data['status_message'].edit_text(
                    "⚠️ No hay servidores activos configurados para verificar"
                )
            session.close()
            return
            
        # Verificar que existen cuentas activas en estos servidores
        emby_server_ids = [server.id for server in emby_servers]
        jellyfin_server_ids = [server.id for server in jellyfin_servers]
        
        emby_accounts_count = 0
        jellyfin_accounts_count = 0
        
        if emby_server_ids:
            emby_accounts_count = session.query(Account).filter(
                Account.server_id.in_(emby_server_ids),
                Account.service == "EMBY",
                Account.is_active == True
            ).count()
            
        if jellyfin_server_ids:
            jellyfin_accounts_count = session.query(Account).filter(
                Account.server_id.in_(jellyfin_server_ids),
                Account.service == "JELLYFIN",
                Account.is_active == True
            ).count()
            
        logger.info(f"Encontradas {emby_accounts_count} cuentas EMBY activas y {jellyfin_accounts_count} cuentas JELLYFIN activas")
        
        if emby_accounts_count == 0 and jellyfin_accounts_count == 0:
            logger.warning("No hay cuentas activas para verificar")
            # Verificar si el contexto tiene user_data y status_message antes de acceder
            if context and hasattr(context, 'user_data') and context.user_data and 'status_message' in context.user_data:
                await context.user_data['status_message'].edit_text(
                    "⚠️ No hay cuentas activas para verificar"
                )
            session.close()
            return
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Procesamiento en paralelo para servidores Emby
            emby_tasks = []
            for server in emby_servers:
                emby_tasks.append(process_emby_server_device_limits(server, session, client))
            
            # Procesamiento en paralelo para servidores Jellyfin
            jellyfin_tasks = []
            for server in jellyfin_servers:
                jellyfin_tasks.append(process_jellyfin_server_device_limits(server, session, client))
            
            # Esperar y recopilar resultados de servidores Emby
            for future in asyncio.as_completed(emby_tasks):
                try:
                    server_report = await future
                    if server_report:
                        total_users_checked += server_report.get('users_checked', 0)
                        total_devices_removed += server_report.get('devices_removed', 0)
                        if server_report.get('devices_removed', 0) > 0:
                            servers_report.append({
                                'name': server_report.get('server_name', 'Emby'),
                                'service': 'EMBY',
                                'devices_removed': server_report.get('devices_removed', 0),
                                'users_details': server_report.get('users_details', [])
                            })
                except Exception as e:
                    logger.error(f"Error al procesar resultados de servidor Emby: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            # Esperar y recopilar resultados de servidores Jellyfin
            for future in asyncio.as_completed(jellyfin_tasks):
                try:
                    server_report = await future
                    if server_report:
                        total_users_checked += server_report.get('users_checked', 0)
                        total_devices_removed += server_report.get('devices_removed', 0)
                        if server_report.get('devices_removed', 0) > 0:
                            servers_report.append({
                                'name': server_report.get('server_name', 'Jellyfin'),
                                'service': 'JELLYFIN',
                                'devices_removed': server_report.get('devices_removed', 0),
                                'users_details': server_report.get('users_details', [])
                            })
                except Exception as e:
                    logger.error(f"Error al procesar resultados de servidor Jellyfin: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
        
        # Generar informe para comando manual o proceso automático
        if context and hasattr(context, 'bot'):
            # Verificar si context.user_data existe y contiene 'status_message'
            if hasattr(context, 'user_data') and context.user_data and 'status_message' in context.user_data:
                # Es un comando manual, actualizar el mensaje del usuario
                try:
                    status_message = context.user_data['status_message']
                    if total_devices_removed > 0:
                        await status_message.edit_text(
                            f"✅ Verificación completada.\n\n"
                            f"👤 Usuarios verificados: {total_users_checked}\n"
                            f"🗑️ Dispositivos eliminados: {total_devices_removed}\n\n"
                            f"Se enviará un informe detallado a los administradores."
                        )
                    else:
                        await status_message.edit_text(
                            f"✅ Verificación completada.\n\n"
                            f"👤 Usuarios verificados: {total_users_checked}\n"
                            f"No se encontraron dispositivos que excedan los límites."
                        )
                    
                    # Limpiar datos de contexto
                    if 'status_message' in context.user_data:
                        context.user_data.pop('status_message', None)
                    if 'target_username' in context.user_data:
                        context.user_data.pop('target_username', None)
                except Exception as e:
                    logger.error(f"Error al actualizar mensaje final: {e}")
            
            # Enviar informe detallado a los administradores si hay dispositivos eliminados
            if total_devices_removed > 0:
                try:
                    await send_device_limits_report(context, total_users_checked, total_devices_removed, servers_report)
                except Exception as e:
                    logger.error(f"Error al enviar informe detallado: {e}")
        
        logger.info(f"Verificación de límites de dispositivos completada. Usuarios verificados: {total_users_checked}, Dispositivos eliminados: {total_devices_removed}")
        
    except Exception as e:
        logger.error(f"Error al verificar límites de dispositivos: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Actualizar mensaje de error para comando manual si context.user_data contiene 'status_message'
        if context and hasattr(context, 'user_data') and context.user_data and 'status_message' in context.user_data:
            try:
                await context.user_data['status_message'].edit_text(
                    f"❌ Error al verificar límites de dispositivos: {str(e)}"
                )
            except Exception as msg_err:
                logger.error(f"Error adicional al actualizar mensaje de error: {msg_err}")
    finally:
        try:
            session.close()
        except Exception as ses_err:
            logger.error(f"Error al cerrar sesión de base de datos: {ses_err}")

async def process_emby_server_device_limits(server, db_session, client):
    """
    Procesa los límites de dispositivos para un servidor Emby específico
    """
    try:
        from datetime import datetime
        import urllib.parse  # Para escapar correctamente los IDs

        # Inicializar reporte
        report = {
            'users_checked': 0,
            'devices_removed': 0,
            'users_details': [],
            'server_name': server.name
        }

        # Eliminar la barra final si existe
        url = server.url
        if url.endswith('/'):
            url = url[:-1]

        # Obtener todos los usuarios del servidor
        users_url = f"{url}/emby/Users?api_key={server.api_key}"
        logger.info(f"Servidor {server.name}: API URL para usuarios = {users_url}")
        
        users_response = await client.get(users_url)
        if users_response.status_code != 200:
            response_text = users_response.text
            logger.error(f"Error al obtener usuarios del servidor {server.name}: {response_text}")
            return report

        server_users = users_response.json()
        logger.info(f"Servidor {server.name}: Se encontraron {len(server_users)} usuarios en el servidor")
        
        # Obtener todas las cuentas activas para este servidor
        accounts = db_session.query(Account).filter_by(
            server_id=server.id,
            service="EMBY",
            is_active=True
        ).all()
        logger.info(f"Servidor {server.name}: Se encontraron {len(accounts)} cuentas activas en la base de datos")
        
        # Verificar si hay cuentas para procesar
        if not accounts:
            logger.warning(f"Servidor {server.name}: No hay cuentas activas asociadas en la base de datos")
            return report
        
        # Usar configuración centralizada de límites de dispositivos
        plan_to_limit = DEVICE_LIMITS['EMBY']
        
        # Obtener todos los dispositivos del servidor
        all_devices_url = f"{url}/emby/Devices?api_key={server.api_key}"
        logger.info(f"Obteniendo todos los dispositivos: {all_devices_url}")
        all_devices_response = await client.get(all_devices_url)
        
        if all_devices_response.status_code != 200:
            logger.error(f"Error al obtener dispositivos: {all_devices_response.text}")
            return report
            
        # Procesar la respuesta para obtener la lista de dispositivos
        all_devices = all_devices_response.json().get('Items', [])
        logger.info(f"Servidor {server.name}: Se encontraron {len(all_devices)} dispositivos en total")
        
        # Procesar cada usuario en el servidor
        for user in server_users:
            user_id = user.get('Id')
            user_name = user.get('Name')
            
            # Buscar la cuenta correspondiente en nuestra base de datos
            account = next((acc for acc in accounts if acc.service_user_id == user_id), None)
            
            # Saltar usuarios admin o sin cuenta en nuestra base de datos
            if not account or user.get('Policy', {}).get('IsAdministrator', False):
                continue
            
            report['users_checked'] += 1
            logger.info(f"Verificando límites para usuario {user_name} (ID: {user_id}, Plan: {account.plan})")
            
            # Determinar el límite de dispositivos según el plan
            device_limit = plan_to_limit.get(account.plan, 1)
            logger.info(f"Límite de dispositivos para {user_name}: {device_limit}")
            
            # IMPORTANTE: Filtrar por LastUserId en lugar de UserId
            user_devices = [d for d in all_devices if d.get('LastUserId') == user_id]
            logger.info(f"Usuario {user_name}: Se encontraron {len(user_devices)} dispositivos")
            
            # Si el usuario no excede su límite, continuar con el siguiente
            if len(user_devices) <= device_limit:
                logger.info(f"Usuario {user_name} tiene {len(user_devices)} dispositivos, dentro del límite de {device_limit}")
                continue
            
            logger.warning(f"Usuario {user_name} excede límite: {len(user_devices)} dispositivos, límite {device_limit}")
            
            # Clasificar dispositivos (con y sin fecha de actividad)
            devices_with_date = []
            devices_without_date = []
            
            for device in user_devices:
                device_id = device.get('Id')
                device_name = device.get('Name', 'Desconocido')
                app_name = device.get('AppName', '')
                last_activity = device.get('DateLastActivity')
                
                if last_activity:
                    # Formato ISO 8601 para fecha de actividad
                    try:
                        activity_date = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))
                        devices_with_date.append({
                            'id': device_id,
                            'name': device_name,
                            'app': app_name,
                            'date': activity_date
                        })
                    except (ValueError, TypeError):
                        devices_without_date.append({
                            'id': device_id,
                            'name': device_name,
                            'app': app_name
                        })
                else:
                    devices_without_date.append({
                        'id': device_id,
                        'name': device_name,
                        'app': app_name
                    })
            
            # Ordenar dispositivos con fecha por más antiguos primero
            devices_with_date.sort(key=lambda x: x['date'])
            
            # Calcular cuántos dispositivos excedentes hay que eliminar
            devices_to_remove_count = len(user_devices) - device_limit
            
            # Preparar lista de dispositivos a eliminar
            devices_to_remove = []
            
            # Primero agregar dispositivos sin fecha de actividad
            if len(devices_without_date) <= devices_to_remove_count:
                devices_to_remove.extend(devices_without_date)
                devices_to_remove_count -= len(devices_without_date)
            else:
                devices_to_remove.extend(devices_without_date[:devices_to_remove_count])
                devices_to_remove_count = 0
            
            # Si aún necesitamos eliminar más, agregar dispositivos con fecha (los más antiguos)
            if devices_to_remove_count > 0:
                devices_to_remove.extend(devices_with_date[:devices_to_remove_count])
            
            # Eliminar los dispositivos seleccionados
            user_removed_devices = []
            
            for device in devices_to_remove:
                try:
                    # URL de eliminación para dispositivos Emby
                    # Añadir el ID como parámetro de consulta
                    device_id = urllib.parse.quote(device['id'])
                    delete_url = f"{url}/emby/Devices?Id={device_id}&api_key={server.api_key}"
                    
                    logger.info(f"Intentando eliminar dispositivo {device['name']}, URL: {delete_url}")
                    delete_response = await client.delete(delete_url)
                    
                    if delete_response.status_code == 204 or delete_response.status_code == 200:
                        report['devices_removed'] += 1
                        user_removed_devices.append({
                            'name': device['name'],
                            'app': device['app']
                        })
                        logger.info(f"Dispositivo {device['name']} eliminado para usuario {user_name}")
                    else:
                        logger.warning(f"Error al eliminar dispositivo {device['name']} para usuario {user_name}: {delete_response.text}")
                except Exception as e:
                    logger.error(f"Error al eliminar dispositivo {device['name']} para usuario {user_name}: {e}")
            
            # Agregar detalles al reporte si se eliminaron dispositivos
            if user_removed_devices:
                report['users_details'].append({
                    'username': user_name,
                    'removed_devices': user_removed_devices,
                    'plan': account.plan,
                    'device_limit': device_limit,
                    'total_devices': len(user_devices)
                })
        
        return report
        
    except Exception as e:
        logger.error(f"Error al procesar límites de dispositivos en servidor Emby {server.name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'users_checked': 0,
            'devices_removed': 0,
            'users_details': [],
            'server_name': getattr(server, 'name', 'Desconocido')
        }

async def process_jellyfin_server_device_limits(server, db_session, client):
    """
    Procesa los límites de dispositivos para un servidor Jellyfin específico
    """
    try:
        from datetime import datetime, timedelta
        
        # Inicializar reporte
        report = {
            'users_checked': 0,
            'devices_removed': 0,
            'users_details': [],
            'server_name': server.name
        }
        
        # Eliminar la barra final si existe
        url = server.url
        if url.endswith('/'):
            url = url[:-1]
        
        # Obtener todos los usuarios del servidor
        users_url = f"{url}/Users?api_key={server.api_key}"
        logger.info(f"Servidor {server.name}: API URL para usuarios = {users_url}")
        users_response = await client.get(users_url)
        
        if users_response.status_code != 200:
            logger.error(f"Error al obtener usuarios del servidor {server.name}: {users_response.text}")
            return report
        
        server_users = users_response.json()
        if not isinstance(server_users, list):
            server_users = [server_users]
        
        logger.info(f"Servidor {server.name}: Se encontraron {len(server_users)} usuarios en el servidor")
        
        # Obtener todas las cuentas activas para este servidor
        accounts = db_session.query(Account).filter_by(
            server_id=server.id,
            service="JELLYFIN",
            is_active=True
        ).all()
        
        logger.info(f"Servidor {server.name}: Se encontraron {len(accounts)} cuentas activas en la base de datos")
        
        # Usar configuración centralizada de límites de dispositivos
        plan_to_limit = DEVICE_LIMITS['JELLYFIN']
        
        # Obtener todos los dispositivos del servidor
        devices_url = f"{url}/Devices?api_key={server.api_key}"
        logger.info(f"Obteniendo todos los dispositivos: {devices_url}")
        devices_response = await client.get(devices_url)
        
        if devices_response.status_code != 200:
            logger.error(f"Error al obtener dispositivos del servidor {server.name}: {devices_response.text}")
            return report
        
        # Manejar diferentes formatos de respuesta
        devices_data = devices_response.json()
        all_devices = []
        
        if isinstance(devices_data, list):
            all_devices = devices_data
        elif isinstance(devices_data, dict) and 'Items' in devices_data:
            all_devices = devices_data.get('Items', [])
        else:
            all_devices = [devices_data]
        
        logger.info(f"Servidor {server.name}: Se encontraron {len(all_devices)} dispositivos en total")
        
        # Obtener sesiones activas
        sessions_url = f"{url}/Sessions?api_key={server.api_key}"
        sessions_response = await client.get(sessions_url)
        active_sessions = []
        
        if sessions_response.status_code == 200:
            sessions_data = sessions_response.json()
            if isinstance(sessions_data, list):
                active_sessions = sessions_data
            else:
                active_sessions = [sessions_data]
        
        # Construir un mapa de deviceId -> userId para sesiones activas
        active_device_map = {}
        for session in active_sessions:
            if isinstance(session, dict):
                user_id = session.get('UserId')
                device_id = session.get('DeviceId')
                if user_id and device_id:
                    active_device_map[device_id] = user_id
        
        # Procesar cada usuario en el servidor
        for user in server_users:
            if not isinstance(user, dict):
                continue
                
            user_id = user.get('Id')
            user_name = user.get('Name')
            
            if not user_id or not user_name:
                continue
            
            # Buscar la cuenta correspondiente en nuestra base de datos
            account = next((acc for acc in accounts if acc.service_user_id == user_id), None)
            
            # Saltar usuarios admin o sin cuenta en nuestra base de datos
            if not account or user.get('Policy', {}).get('IsAdministrator', False):
                continue
            
            report['users_checked'] += 1
            logger.info(f"Verificando límites para usuario {user_name} (ID: {user_id}, Plan: {account.plan})")
            
            # Determinar el límite de dispositivos según el plan
            device_limit = plan_to_limit.get(account.plan, 1)
            logger.info(f"Límite de dispositivos para {user_name}: {device_limit}")
            
            # Filtrar solo los dispositivos del usuario actual 
            # IMPORTANTE: En Jellyfin buscamos por LastUserId igual que en Emby
            user_devices = [d for d in all_devices if isinstance(d, dict) and d.get('LastUserId') == user_id]
            logger.info(f"Usuario {user_name}: Se encontraron {len(user_devices)} dispositivos")
            
            # Si el usuario no excede su límite, continuar con el siguiente
            if len(user_devices) <= device_limit:
                logger.info(f"Usuario {user_name} tiene {len(user_devices)} dispositivos, dentro del límite de {device_limit}")
                continue
            
            logger.warning(f"Usuario {user_name} excede límite: {len(user_devices)} dispositivos, límite {device_limit}")
            
            # Identificar dispositivos activos ahora
            active_devices = []
            devices_with_date = []
            devices_without_date = []
            
            for device in user_devices:
                device_id = device.get('Id')
                device_name = device.get('Name', 'Desconocido')
                app_name = device.get('AppName', '')
                last_activity = device.get('DateLastActivity')
                
                # Verificar si el dispositivo está activo ahora
                is_active_now = device_id in active_device_map and active_device_map[device_id] == user_id
                
                device_info = {
                    'id': device_id,
                    'name': device_name,
                    'app': app_name,
                    'active_now': is_active_now
                }
                
                if is_active_now:
                    # Priorizar mantener dispositivos activos
                    active_devices.append(device_info)
                elif last_activity:
                    # Formato ISO 8601 para fecha de actividad
                    try:
                        activity_date = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))
                        device_info['date'] = activity_date
                        devices_with_date.append(device_info)
                    except (ValueError, TypeError):
                        devices_without_date.append(device_info)
                else:
                    devices_without_date.append(device_info)
            
            # Ordenar dispositivos con fecha por más antiguos primero
            devices_with_date.sort(key=lambda x: x['date'])
            
            # NUEVA ESTRATEGIA DE ELIMINACIÓN:
            # 1. Preservar dispositivos activos si están dentro del límite
            # 2. Eliminar primero dispositivos sin fecha de actividad
            # 3. Luego eliminar los dispositivos más antiguos
            
            # Calcular cuántos dispositivos mantener vs cuántos eliminar
            keep_count = min(device_limit, len(active_devices))
            total_to_remove = len(user_devices) - device_limit
            devices_to_keep = active_devices[:keep_count]
            
            # IDs de dispositivos que mantendremos
            keep_ids = [d['id'] for d in devices_to_keep]
            
            # Crear lista de potenciales dispositivos a eliminar
            potential_removals = []
            
            # Primero agregar dispositivos sin fecha (menos importantes)
            potential_removals.extend(devices_without_date)
            
            # Luego agregar dispositivos con fecha, de más antiguos a más recientes
            potential_removals.extend(devices_with_date)
            
            # Finalmente agregar dispositivos activos que exceden el límite
            if len(active_devices) > keep_count:
                potential_removals.extend(active_devices[keep_count:])
            
            # Crear lista final de dispositivos a eliminar
            devices_to_remove = []
            for device in potential_removals:
                if device['id'] not in keep_ids and len(devices_to_remove) < total_to_remove:
                    devices_to_remove.append(device)
                    # Ya no necesitamos preservar este ID
                    if device['id'] in keep_ids:
                        keep_ids.remove(device['id'])
            
            # Log detallado sobre lo que vamos a hacer
            logger.info(f"Usuario {user_name}: Manteniendo {len(devices_to_keep)} dispositivos activos, eliminando {len(devices_to_remove)} de {len(user_devices)} totales")
            
            # Eliminar los dispositivos seleccionados
            user_removed_devices = []
            
            for device in devices_to_remove:
                try:
                    # URL de eliminación corregida y verificada
                    delete_url = f"{url}/Devices/{device['id']}?api_key={server.api_key}"
                    logger.info(f"Intentando eliminar dispositivo: {device['name']} (ID: {device['id']}) del usuario {user_name}")
                    
                    delete_response = await client.delete(delete_url)
                    
                    if delete_response.status_code in [204, 200]:
                        report['devices_removed'] += 1
                        user_removed_devices.append({
                            'name': device['name'],
                            'app': device['app']
                        })
                        logger.info(f"✓ Dispositivo {device['name']} eliminado para usuario {user_name}")
                    else:
                        logger.warning(f"✗ Error al eliminar dispositivo {device['name']} para usuario {user_name}: Código {delete_response.status_code}, Respuesta: {delete_response.text}")
                except Exception as e:
                    logger.error(f"✗ Error al eliminar dispositivo {device['name']} para usuario {user_name}: {e}")
            
            # Agregar detalles al reporte si se eliminaron dispositivos
            if user_removed_devices:
                report['users_details'].append({
                    'username': user_name,
                    'removed_devices': user_removed_devices,
                    'plan': account.plan,
                    'device_limit': device_limit,
                    'total_devices': len(user_devices)
                })
        
        return report
        
    except Exception as e:
        logger.error(f"Error al procesar límites de dispositivos en servidor Jellyfin {server.name}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'users_checked': 0,
            'devices_removed': 0,
            'users_details': [],
            'server_name': getattr(server, 'name', 'Desconocido')
        }

async def send_device_limits_report(context, total_users, total_devices, servers_report):
    """
    Envía un informe detallado sobre los límites de dispositivos a los administradores
    """
    try:
        from database import Session, User
        from config import ADMIN_IDS
        
        session = Session()
        
        # Obtener todos los usuarios con roles admin
        admin_users = session.query(User).filter(User.role.in_(["SUPER_ADMIN", "ADMIN"])).all()
        admin_telegram_ids = [user.telegram_id for user in admin_users]
        
        # Añadir los IDs de admin configurados 
        for admin_id in ADMIN_IDS:
            if admin_id not in admin_telegram_ids:
                admin_telegram_ids.append(admin_id)
        
        # Construir mensaje de informe - Parte 1 (resumen)
        summary_message = (
            f"📱 *CONTROL DE LÍMITES DE DISPOSITIVOS*\n\n"
            f"👤 Usuarios verificados: {total_users}\n"
            f"🗑️ Dispositivos eliminados: {total_devices}\n\n"
        )
        
        # Enviar resumen a todos los administradores
        if context and context.bot:
            for admin_id in admin_telegram_ids:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=summary_message,
                        parse_mode="MARKDOWN"
                    )
                except Exception as e:
                    logger.error(f"Error al enviar resumen de límites de dispositivos a admin {admin_id}: {e}")
        
        # Límite de longitud para mensajes de Telegram (4096 caracteres)
        MAX_MESSAGE_LENGTH = 4000  # Usamos un valor un poco menor para tener margen
        
        if servers_report:
            # Procesar cada servidor en un mensaje separado
            for server in servers_report:
                server_message = f"🖥️ *Servidor {server['name']}*:\n"
                server_message += f"   🗑️ Dispositivos eliminados: {server['devices_removed']}\n\n"
                
                # Enviar mensaje inicial de servidor
                if context and context.bot:
                    for admin_id in admin_telegram_ids:
                        try:
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=server_message,
                                parse_mode="MARKDOWN"
                            )
                        except Exception as e:
                            logger.error(f"Error al enviar informe de servidor a admin {admin_id}: {e}")
                
                # Procesar detalles de usuarios en mensajes separados
                for user_detail in server['users_details']:
                    username = user_detail['username']
                    plan = user_detail.get('plan', 'desconocido')
                    limit = user_detail.get('device_limit', '?')
                    total = user_detail.get('total_devices', '?')
                    
                    # Crear mensaje base para este usuario
                    user_message = f"   👤 *{username}* (Plan: {plan}, Límite: {limit}, Total: {total}):\n"
                    
                    # Añadir detalles de dispositivos, controlando longitud
                    devices_message = ""
                    for device in user_detail['removed_devices']:
                        device_name = device['name']
                        app_name = device['app']
                        device_info = f"{device_name}"
                        if app_name:
                            device_info += f" ({app_name})"
                        device_line = f"      • {device_info}\n"
                        
                        # Verificar si añadir esta línea excedería el límite
                        if len(user_message + devices_message + device_line) > MAX_MESSAGE_LENGTH:
                            # Enviar el mensaje actual y comenzar uno nuevo
                            if context and context.bot:
                                for admin_id in admin_telegram_ids:
                                    try:
                                        await context.bot.send_message(
                                            chat_id=admin_id,
                                            text=user_message + devices_message,
                                            parse_mode="MARKDOWN"
                                        )
                                    except Exception as e:
                                        logger.error(f"Error al enviar parte del informe a admin {admin_id}: {e}")
                            
                            # Reiniciar con una cabecera para continuación
                            user_message = f"   👤 *{username}* (continuación):\n"
                            devices_message = device_line
                        else:
                            devices_message += device_line
                    
                    # Enviar mensaje final para este usuario si hay contenido
                    if devices_message:
                        if context and context.bot:
                            for admin_id in admin_telegram_ids:
                                try:
                                    await context.bot.send_message(
                                        chat_id=admin_id,
                                        text=user_message + devices_message,
                                        parse_mode="MARKDOWN"
                                    )
                                except Exception as e:
                                    logger.error(f"Error al enviar informe final de usuario a admin {admin_id}: {e}")
        
        session.close()
    
    except Exception as e:
        logger.error(f"Error al enviar informe de límites de dispositivos: {e}")
        import traceback
        logger.error(traceback.format_exc())

# Funciones para gestionar las tareas en segundo plano
# No necesitamos esta función ya que la configuración se hará en bot.py directamente
# Esta función se mantiene por compatibilidad con código existente
async def setup_jobs(application):
    """
    Esta función es mantenida por compatibilidad, pero la configuración
    real se hace en bot.py con setup_jobs_background
    """
    logger.warning("Esta función setup_jobs está obsoleta. Use setup_jobs_background en bot.py.")
