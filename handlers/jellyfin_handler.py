import logging
import random
import string
import json
import httpx
from database import Session, Account, Server, User as DbUser, check_demo_limit
from datetime import datetime, timedelta
from database import Role
from config import DEFAULT_ACCOUNT_PASSWORD
from audit_logger import log_account_created
from db_locks import atomic_server_update
import urllib.parse
import uuid

logger = logging.getLogger(__name__)

def generate_device_id():
    """Genera un ID de dispositivo único en formato UUID"""
    import uuid
    # Asegurarnos de generar un UUID válido en formato correcto
    device_uuid = uuid.uuid4()
    # Convertir a string en el formato requerido con guiones
    return str(device_uuid).lower()

def generate_username(is_demo=False):
    """Genera un nombre de usuario único para Jellyfin"""
    # Genera 4 dígitos aleatorios
    digits = ''.join(random.choices(string.digits, k=4))
    # Genera 2 letras mayúsculas aleatorias
    letters = ''.join(random.choices(string.ascii_uppercase, k=2))

    # Prefijo según si es demo o no
    prefix = "Demo" if is_demo else "User"

    return f"{prefix}{digits}{letters}"

def generate_password():
    """
    Genera una contraseña aleatoria única para cada cuenta
    Formato: 2 letras + 6 números + 2 letras (10 caracteres total)
    Ejemplo: Ab123456Cd
    """
    # 2 letras mayúsculas al principio
    first_letters = ''.join(random.choices(string.ascii_uppercase, k=2))
    # 6 números en el medio
    numbers = ''.join(random.choices(string.digits, k=6))
    # 2 letras mayúsculas al final
    last_letters = ''.join(random.choices(string.ascii_uppercase, k=2))

    return f"{first_letters}{numbers}{last_letters}"

async def create_jellyfin_account_on_server(telegram_user_id, plan, server_id, duration_days=30):
    """
    Proceso completo para crear una cuenta de Jellyfin en un servidor específico
    """
    session = Session()
    
    try:
        # Obtener el usuario de la base de datos
        db_user = session.query(DbUser).filter_by(telegram_id=telegram_user_id).first()
        
        if not db_user:
            session.close()
            return False, "Usuario no encontrado"
        
        # VERIFICAR LÍMITE DE DEMOS
        if plan == 'demo':
            can_create, current_count, limit = check_demo_limit(db_user.id, session)
            if not can_create:
                session.close()
                return False, f"Has alcanzado el límite diario de demos ({current_count}/{limit}). Puedes eliminar una demo existente para crear otra."
        
        # Para cuentas demo o usuarios admin, no se cobra
        is_free = plan == 'demo' or db_user.role in ["SUPER_ADMIN", "ADMIN"]
        
        # Verificar créditos (excepto para admin o demo)
        if not is_free:
            from sqlalchemy import text
            # Obtener el precio del plan
            price_query = text("""
                SELECT amount FROM prices 
                WHERE service = 'JELLYFIN' AND role = :role AND plan = :plan
            """)
            
            result = session.execute(price_query, {"role": db_user.role, "plan": plan})
            price_row = result.fetchone()
            
            if not price_row:
                session.close()
                return False, "Plan no disponible para tu rol"
            
            price = float(price_row[0])
            
            # Verificar si el usuario tiene suficientes créditos
            if db_user.credits < price:
                session.close()
                return False, f"Créditos insuficientes. Necesitas ${price:,.0f}"
        
        # Buscar el servidor específico
        server = session.query(Server).filter_by(
            id=server_id,
            service="JELLYFIN",
            is_active=True
        ).first()
        
        if not server:
            session.close()
            return False, "Servidor no encontrado o no disponible"
        
        # Verificar si el servidor tiene capacidad
        if server.current_users >= server.max_users:
            session.close()
            return False, f"El servidor {server.name} está lleno ({server.current_users}/{server.max_users})"
        
        # Crear usuario en Jellyfin
        success, result = await create_jellyfin_user(server, plan, duration_days)
        
        if not success:
            session.close()
            return False, result
        
        # Crear la cuenta en la base de datos
        account = Account(
            user_id=db_user.id,
            service="JELLYFIN",
            username=result["username"],
            password=result["password"],
            plan=plan,
            server_id=server.id,
            service_user_id=result["user_id"],
            expiry_date=result["expiry_date"],
            is_active=True,
            created_date=datetime.utcnow()
        )
        
        session.add(account)
        
        # Actualizar contador de usuarios en el servidor
        server.current_users += 1
        
        # Deducir créditos si aplica
        if not is_free:
            db_user.credits -= price

        session.commit()

        # Registrar en auditoría
        log_account_created(db_user.id, "JELLYFIN", plan, server.id, result["username"])

        # Preparar respuesta
        response = {
            "username": result["username"],
            "password": result["password"],
            "server": server.name,
            "url": server.url,
            "expiry_date": result["expiry_date"],
            "plan": plan
        }
        
        # Agregar información de demos si es demo
        if plan == 'demo':
            can_create_after, current_count_after, limit = check_demo_limit(db_user.id, session)
            response["demo_info"] = f"Demo {current_count_after + 1}/{limit} del día"
        
        return True, response
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error al crear cuenta Jellyfin: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def create_jellyfin_user(server, plan, duration_days=30):
    """
    Crea un usuario en Jellyfin con las políticas correspondientes al plan
    """
    try:
        # Verificar el tipo de plan
        is_demo = plan == 'demo'
        is_live_tv = plan == 'live_tv' or plan == '3_screens_tv'
        is_three_screens = plan == '3_screens' or plan == '3_screens_tv'
        is_bulk = plan == 'bulk'
        
        # Determinar el límite de sesiones según el plan
        if is_three_screens:
            max_sessions = 3
        elif is_bulk:
            max_sessions = 5
        else:
            max_sessions = 1
        
        # Generar nombre de usuario
        username = generate_username(is_demo)

        # Generar contraseña aleatoria única para esta cuenta
        password = generate_password()
        
        # Generar un device_id único
        device_id = generate_device_id()
        # NOTA DE SEGURIDAD: NO loggear device IDs ya que son credenciales sensibles
        
        # Preparar las cabeceras comunes
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Accept-Language': 'es-ES,es;q=0.9',
            'X-Emby-Authorization': f'MediaBrowser Device="{device_id}", DeviceName="Chrome Windows", Version="10.8.0", Token="{server.api_key}"'
        }
        
        # Verificar si la URL tiene barra final
        base_url = server.url
        if base_url.endswith('/'):
            base_url = base_url[:-1]
        
        # 1. Crear usuario
        create_url = f"{base_url}/Users/New"
        
        create_data = {
            "Name": username,
            "Password": password
        }
        
        # Agregar API key en la URL
        if "?" in create_url:
            create_url += f"&api_key={server.api_key}"
        else:
            create_url += f"?api_key={server.api_key}"
        
        timeout = 15.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                create_url,
                headers=headers,
                content=json.dumps(create_data)
            )
            if response.status_code != 200:
                response_text = response.text
                return False, f"Error al crear usuario: {response_text}"

            # Obtener el ID del usuario creado
            response_json = response.json()
            user_id = response_json.get('Id')
        
        if not user_id:
            return False, "No se pudo obtener el ID del usuario creado"
        
        # 2. Establecer la política del usuario
        policy_url = f"{base_url}/Users/{user_id}/Policy"
        
        # Agregar API key en la URL
        if "?" in policy_url:
            policy_url += f"&api_key={server.api_key}"
        else:
            policy_url += f"?api_key={server.api_key}"
        
        # Configuración de política para Jellyfin
        policy_data = {
            "IsAdministrator": False,
            "IsHidden": True,
            "EnableCollectionManagement": False,
            "EnableSubtitleManagement": False,
            "EnableLyricManagement": False,
            "IsDisabled": False,
            "BlockedTags": [],
            "AllowedTags": [],
            "EnableUserPreferenceAccess": True,
            "AccessSchedules": [],
            "BlockUnratedItems": [],
            "EnableRemoteControlOfOtherUsers": False,
            "EnableSharedDeviceControl": False,
            "EnableRemoteAccess": True,
            "EnableLiveTvManagement": is_live_tv or is_demo,
            "EnableLiveTvAccess": is_live_tv or is_demo,
            "EnableMediaPlayback": True,
            "EnableAudioPlaybackTranscoding": True,
            "EnableVideoPlaybackTranscoding": True,
            "EnablePlaybackRemuxing": True,
            "ForceRemoteSourceTranscoding": False,
            "EnableContentDeletion": False,
            "EnableContentDeletionFromFolders": [],
            "EnableContentDownloading": False,
            "EnableSyncTranscoding": True,
            "EnableMediaConversion": True,
            "EnabledDevices": [],
            "EnableAllDevices": True,
            "EnabledChannels": [],
            "EnableAllChannels": False,
            "EnabledFolders": [],
            "EnableAllFolders": True,
            "InvalidLoginAttemptCount": 0,
            "LoginAttemptsBeforeLockout": -1,
            "MaxActiveSessions": max_sessions,
            "EnablePublicSharing": True,
            "BlockedMediaFolders": [],
            "BlockedChannels": [],
            "RemoteClientBitrateLimit": 0,
            "AuthenticationProviderId": "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider",
            "PasswordResetProviderId": "Jellyfin.Server.Implementations.Users.DefaultPasswordResetProvider",
            "SyncPlayAccess": "None"
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            policy_response = await client.post(
                policy_url,
                headers=headers,
                content=json.dumps(policy_data)
            )
            if policy_response.status_code != 204:
                response_text = policy_response.text
                return False, f"Error al actualizar políticas: {response_text}"

            # Establecer la fecha de expiración
            if is_demo:
                # Configurar duración especial para demos (1 hora)
                expiry_date = datetime.utcnow() + timedelta(hours=1)
            else:
                expiry_date = datetime.utcnow() + timedelta(days=duration_days)

            # Todo se completó correctamente
            return True, {
                "username": username,
                "password": password,
                "user_id": user_id,
                "expiry_date": expiry_date,
                "plan": plan
            }

    except httpx.RequestError as e:
        logger.error(f"Error de conexión con el servidor Jellyfin: {e}")
        return False, f"Error de conexión: {str(e)}"
    except Exception as e:
        logger.error(f"Error general al crear usuario en Jellyfin: {e}")
        return False, f"Error: {str(e)}"

async def create_jellyfin_account(telegram_user_id, plan, duration_days=30):
    """
    Proceso completo para crear una cuenta de Jellyfin
    """
    session = Session()
    
    try:
        # Obtener el usuario de la base de datos
        db_user = session.query(DbUser).filter_by(telegram_id=telegram_user_id).first()
        
        if not db_user:
            session.close()
            return False, "Usuario no encontrado"
        
        # VERIFICAR LÍMITE DE DEMOS
        if plan == 'demo':
            can_create, current_count, limit = check_demo_limit(db_user.id, session)
            if not can_create:
                session.close()
                return False, f"Has alcanzado el límite diario de demos ({current_count}/{limit}). Puedes eliminar una demo existente para crear otra."
        
        # Para cuentas demo o usuarios admin, no se cobra
        is_free = plan == 'demo' or db_user.role in ["SUPER_ADMIN", "ADMIN"]
        
        # Verificar créditos (excepto para admin o demo)
        if not is_free:
            from sqlalchemy import text
            # Obtener el precio del plan
            price_query = text("""
                SELECT amount FROM prices 
                WHERE service = 'JELLYFIN' AND role = :role AND plan = :plan
            """)
            
            result = session.execute(price_query, {"role": db_user.role, "plan": plan})
            price_row = result.fetchone()
            
            if not price_row:
                session.close()
                return False, "Plan no disponible para tu rol"
            
            price = float(price_row[0])
            
            # Verificar si el usuario tiene suficientes créditos
            if db_user.credits < price:
                session.close()
                return False, f"Créditos insuficientes. Necesitas ${price:,.0f}"
        
        # Buscar un servidor disponible
        server = session.query(Server).filter_by(
            service="JELLYFIN",
            is_active=True
        ).filter(Server.current_users < Server.max_users).first()
        
        if not server:
            session.close()
            return False, "No hay servidores disponibles"
        
        # Crear usuario en Jellyfin
        success, result = await create_jellyfin_user(server, plan, duration_days)
        
        if not success:
            session.close()
            return False, result
        
        # Crear la cuenta en la base de datos
        account = Account(
            user_id=db_user.id,
            service="JELLYFIN",
            username=result["username"],
            password=result["password"],
            plan=plan,
            server_id=server.id,
            service_user_id=result["user_id"],
            expiry_date=result["expiry_date"],
            is_active=True,
            created_date=datetime.utcnow()
        )
        
        session.add(account)
        
        # Actualizar contador de usuarios en el servidor
        server.current_users += 1
        
        # Deducir créditos si aplica
        if not is_free:
            db_user.credits -= price

        session.commit()

        # Registrar en auditoría
        log_account_created(db_user.id, "JELLYFIN", plan, server.id, result["username"])

        # Preparar respuesta
        response = {
            "username": result["username"],
            "password": result["password"],
            "server": server.name,
            "url": server.url,
            "expiry_date": result["expiry_date"],
            "plan": plan
        }
        
        # Agregar información de demos si es demo
        if plan == 'demo':
            can_create_after, current_count_after, limit = check_demo_limit(db_user.id, session)
            response["demo_info"] = f"Demo {current_count_after + 1}/{limit} del día"
        
        return True, response
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error al crear cuenta Jellyfin: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def delete_jellyfin_user(server, user_id):
    """
    Elimina un usuario del servidor Jellyfin
    
    Args:
        server: Objeto Server de la base de datos
        user_id: ID del usuario en Jellyfin a eliminar
        
    Returns:
        Tuple (success, message): Indica si la operación fue exitosa y un mensaje
    """
    try:
        # Generar un device_id único
        device_id = generate_device_id()
        
        # Preparar las cabeceras
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'es-ES,es;q=0.9',
            'Origin': server.url,
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Ch-Ua': '"Not:A-Brand";v="24", "Chromium";v="134"',
            'Sec-Ch-Ua-Mobile': '?0',
            'X-Emby-Authorization': f'MediaBrowser Device="{device_id}", DeviceName="Chrome Windows", Version="10.8.0", Token="{server.api_key}"'
        }
        
        # Verificar si la URL tiene barra final
        base_url = server.url
        if base_url.endswith('/'):
            base_url = base_url[:-1]
        
        # URL para eliminar usuario
        delete_url = f"{base_url}/Users/{user_id}"
        
        # Agregar API key en la URL
        if "?" in delete_url:
            delete_url += f"&api_key={server.api_key}"
        else:
            delete_url += f"?api_key={server.api_key}"
        
        # Realizar la solicitud DELETE
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(delete_url, headers=headers)
            # Verificar respuesta
            if response.status_code in [204, 200]:
                return True, "Usuario eliminado correctamente del servidor Jellyfin"
            elif response.status_code == 404:
                # Si devuelve 404, el usuario ya no existe, lo cual es un éxito para nosotros
                return True, "El usuario ya no existía en el servidor (404)"
            else:
                response_text = response.text
                return False, f"Error al eliminar usuario: {response_text}"

    except httpx.RequestError as e:
        logger.error(f"Error de conexión con el servidor Jellyfin: {e}")
        return False, f"Error de conexión: {str(e)}"
    except Exception as e:
        logger.error(f"Error general al eliminar usuario en Jellyfin: {e}")
        return False, f"Error: {str(e)}"

async def renew_jellyfin_account(telegram_user_id, username, duration_days):
    """
    Renueva una cuenta de Jellyfin
    """
    session = Session()
    
    try:
        # Buscar la cuenta por nombre de usuario
        account = session.query(Account).filter_by(
            service="JELLYFIN",
            username=username,
            is_active=True
        ).first()
        
        if not account:
            session.close()
            return False, f"No se encontró una cuenta activa con el nombre de usuario {username}"
        
        # Verificar si es una cuenta demo (las demos no se pueden renovar)
        if account.plan == 'demo':
            session.close()
            return False, "Las cuentas demo no pueden ser renovadas"
        
        # Obtener el usuario que está renovando la cuenta
        db_user = session.query(DbUser).filter_by(telegram_id=telegram_user_id).first()
        
        if not db_user:
            session.close()
            return False, "Usuario no encontrado"
        
        # Obtener el servidor
        server = session.query(Server).filter_by(id=account.server_id).first()
        
        if not server:
            session.close()
            return False, "Servidor no encontrado"
        
        # Calcular precio (gratis para admin)
        is_free = db_user.role in ["SUPER_ADMIN", "ADMIN"]
        
        if not is_free:
            from sqlalchemy import text
            # Obtener el precio del plan
            price_query = text("""
                SELECT amount FROM prices 
                WHERE service = 'JELLYFIN' AND role = :role AND plan = :plan
            """)
            
            result = session.execute(price_query, {"role": db_user.role, "plan": account.plan})
            price_row = result.fetchone()
            
            if not price_row:
                session.close()
                return False, "Plan no disponible para tu rol"
            
            price = float(price_row[0])
            
            # Verificar si el usuario tiene suficientes créditos
            if db_user.credits < price:
                session.close()
                return False, f"Créditos insuficientes. Necesitas ${price:,.0f}"
            
            # Deducir créditos
            db_user.credits -= price
        
        # Actualizar fecha de vencimiento
        from datetime import datetime, timedelta
        new_expiry_date = datetime.utcnow() + timedelta(days=duration_days)
        account.expiry_date = new_expiry_date
        
        session.commit()
        
        # Preparar respuesta
        response = {
            "username": account.username,
            "password": account.password,
            "server": server.name,
            "url": server.url,
            "expiry_date": new_expiry_date,
            "plan": account.plan
        }
        
        return True, response
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error al renovar cuenta Jellyfin: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def delete_jellyfin_account(username):
    """
    Elimina una cuenta Jellyfin completamente (del servidor y de la base de datos)
    
    Args:
        username: Nombre de usuario a eliminar
        
    Returns:
        Tuple (success, message): Indica si la operación fue exitosa y un mensaje
    """
    session = Session()
    
    try:
        # Buscar la cuenta por nombre de usuario (incluyendo inactivas para asegurar eliminación completa)
        account = session.query(Account).filter_by(
            service="JELLYFIN",
            username=username
        ).first()  # Eliminamos el filtro is_active para encontrar cuentas ya marcadas como inactivas
        
        if not account:
            session.close()
            return False, f"No se encontró una cuenta con el nombre de usuario {username}"
        
        # Obtener el servidor
        server = session.query(Server).filter_by(id=account.server_id).first()
        
        if not server:
            session.close()
            return False, "No se encontró el servidor asociado a esta cuenta"
            
        # Obtener el ID del usuario en Jellyfin (si la cuenta está activa)
        service_user_id = account.service_user_id
        
        # Si la cuenta está activa, intentar eliminarla del servidor
        if account.is_active and service_user_id:
            try:
                success, message = await delete_jellyfin_user(server, service_user_id)
                # Incluso si falla, continuamos para eliminar de la BD
                if not success:
                    logger.warning(f"No se pudo eliminar del servidor: {message}, pero se eliminará de la BD")
            except Exception as e:
                logger.warning(f"Error al eliminar del servidor: {e}, pero se eliminará de la BD")
        
        # Guardar información para el log
        logger.info(f"Eliminando completamente la cuenta {username} (ID: {account.id}) de la base de datos")
        
        # ELIMINAR COMPLETAMENTE de la base de datos
        session.delete(account)
            
        # Actualizar el contador de usuarios del servidor si la cuenta estaba activa
        if account.is_active and server.current_users > 0:
            server.current_users -= 1
            
        # Confirmar cambios
        session.commit()
        logger.info(f"Cuenta {username} eliminada completamente de la base de datos")
        
        return True, f"Cuenta {username} eliminada completamente"
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error al eliminar cuenta Jellyfin: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def delete_orphaned_jellyfin_devices(server):
    """
    Elimina dispositivos huérfanos en un servidor Jellyfin

    Args:
        server: Objeto Server de la base de datos

    Returns:
        Tuple (count, message, deleted_devices): Número de dispositivos eliminados, mensaje y lista de dispositivos
    """
    try:
        # Eliminar la barra final si existe
        url = server.url
        if url.endswith('/'):
            url = url[:-1]

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Obtener todos los dispositivos
            devices_url = f"{url}/Devices?api_key={server.api_key}"
            devices_response = await client.get(devices_url)
            if devices_response.status_code != 200:
                response_text = devices_response.text
                return 0, f"Error al obtener dispositivos: {response_text}", []

            # Manejar diferentes formatos de respuesta
            devices_data = devices_response.json()

            # Asegurarse de que devices es una lista
            if isinstance(devices_data, list):
                devices = devices_data
            elif isinstance(devices_data, dict) and 'Items' in devices_data:
                devices = devices_data.get('Items', [])
            else:
                devices = [devices_data]

            # Obtener todas las sesiones activas
            sessions_url = f"{url}/Sessions?api_key={server.api_key}"
            sessions_response = await client.get(sessions_url)
            if sessions_response.status_code != 200:
                response_text = sessions_response.text
                return 0, f"Error al obtener sesiones: {response_text}", []

            # Manejar diferentes formatos de respuesta
            sessions_data = sessions_response.json()

            # Asegurarse de que sessions es una lista
            if isinstance(sessions_data, list):
                active_sessions = sessions_data
            else:
                active_sessions = [sessions_data]

            # Extraer IDs de dispositivos en sesiones activas
            active_device_ids = []
            for ses in active_sessions:
                if isinstance(ses, dict) and ses.get('DeviceId'):
                    active_device_ids.append(ses.get('DeviceId'))
                
            # Obtener usuarios activos de la base de datos
            from database import Session, Account
            db_session = Session()
            active_accounts = db_session.query(Account).filter_by(
                server_id=server.id,
                service="JELLYFIN",
                is_active=True
            ).all()
            db_session.close()

            # Obtener todos los usuarios del servidor
            users_url = f"{url}/Users?api_key={server.api_key}"
            users_response = await client.get(users_url)
            # IDs de usuarios activos (que tienen cuentas en la BD)
            active_user_ids = set()
            active_usernames = set()  # Nombres de usuario activos en la BD

            if users_response.status_code == 200:
                server_users = users_response.json()
                if not isinstance(server_users, list):
                    server_users = [server_users]

                for user in server_users:
                    if isinstance(user, dict):
                        server_user_id = user.get('Id')
                        server_username = user.get('Name')
                        
                        # Verificar si el usuario tiene cuenta activa en nuestra BD
                        for account in active_accounts:
                            # Coincidencia por ID de servicio
                            if account.service_user_id == server_user_id:
                                active_user_ids.add(server_user_id)
                                if server_username:
                                    active_usernames.add(server_username)
                                break
                            
                            # Coincidencia por nombre de usuario (fallback)
                            if account.username == server_username:
                                active_user_ids.add(server_user_id)
                                active_usernames.add(server_username)
                                break
        
        # Dispositivos a eliminar
        devices_to_delete = []
        deleted_devices_info = []
        
        for device in devices:
            if not isinstance(device, dict):
                continue
                
            device_id = device.get('Id')
            user_id = device.get('UserId')
            last_user_name = device.get('LastUserName', '')
            app_name = device.get('AppName', '')
            device_name = device.get('Name', 'Desconocido')
            
            if not device_id:
                continue
            
            # Condición 1: El usuario asociado (UserId) NO es un usuario activo
            is_valid_user = user_id is not None and user_id in active_user_ids
            
            # Condición 2: El último usuario (LastUserName) NO es un usuario activo
            # Esto es crítico: algunos dispositivos pierden el UserId pero conservan el LastUserName
            is_valid_last_user = last_user_name and last_user_name in active_usernames
            
            # Condición 3: No tiene sesiones activas
            is_active_session = device_id in active_device_ids
            
            # Un dispositivo es huérfano si:
            # 1. No pertenece a un usuario activo (ni por ID ni por Nombre)
            # 2. Y no tiene una sesión activa en este momento
            if not is_valid_user and not is_valid_last_user and not is_active_session:
                devices_to_delete.append(device_id)
                deleted_devices_info.append({
                    "device_id": device_id,
                    "device_name": device_name, 
                    "app_name": app_name,
                    "reason": f"No UserID match, No LastUser match ({last_user_name})"
                })
                logger.info(f"Marcando dispositivo como huérfano: {device_name} - {app_name} (LastUser: {last_user_name})")
        
        # Salir del contexto del cliente actual y crear uno nuevo para las eliminaciones
        # Esto previene el error "client has been closed" si la operación anterior tomó mucho tiempo
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Eliminar dispositivos
            deleted_count = 0
            for i, device_id in enumerate(devices_to_delete):
                try:
                    # CORRECCIÓN: URL de eliminación correcta para Jellyfin
                    # Usar parámetro de consulta Id en lugar de path parameter
                    device_id_escaped = urllib.parse.quote(device_id, safe='')
                    delete_url = f"{url}/Devices?Id={device_id_escaped}&api_key={server.api_key}"

                    logger.info(f"Eliminando dispositivo huérfano: {deleted_devices_info[i]['device_name']}")

                    # Generar un device_id único para esta operación
                    auth_device_id = str(uuid.uuid4()).replace('-', '')

                    # Headers corregidos para Jellyfin
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                        'Accept': '*/*',
                        'Accept-Language': 'es-ES,es;q=0.9',
                        'Authorization': f'MediaBrowser Client="Jellyfin Web", Device="Chrome Windows", DeviceId="{auth_device_id}", Version="10.10.6", Token="{server.api_key}"'
                    }

                    delete_response = await client.delete(delete_url, headers=headers)
                    if delete_response.status_code in [204, 200]:
                        deleted_count += 1
                        logger.info(f"Dispositivo Jellyfin {device_id} ({deleted_devices_info[i]['device_name']}) eliminado del servidor {server.name}")
                    else:
                        response_text = delete_response.text
                        logger.warning(f"Error al eliminar dispositivo Jellyfin {device_id}: Código {delete_response.status_code}, Respuesta: {response_text}")
                except Exception as e:
                    logger.error(f"Error al eliminar dispositivo Jellyfin {device_id}: {e}")

        return deleted_count, f"Se eliminaron {deleted_count} dispositivos huérfanos de {len(devices)} totales", deleted_devices_info
        
    except Exception as e:
        logger.error(f"Error al eliminar dispositivos huérfanos en Jellyfin: {e}")
        return 0, f"Error: {str(e)}", []

async def get_jellyfin_servers_status():
    """
    Obtiene el estado de todos los servidores Jellyfin
    """
    session = None
    
    try:
        # Obtener todos los servidores Jellyfin activos
        session = Session()
        servers = session.query(Server).filter_by(service="JELLYFIN", is_active=True).all()
        
        if not servers:
            if session:
                session.close()
            return False, "No hay servidores Jellyfin configurados"
        
        result = []
        
        for server in servers:
            # Estado predeterminado (en caso de que el servidor esté offline)
            server_status = {
                'name': server.name,
                'url': server.url,
                'online': False,
                'db_users': server.current_users,
                'max_users': server.max_users,
                'db_devices': 0,  # Se calculará a partir de las cuentas
                'max_devices': server.max_devices,
                'total_registered_devices': 0,  # NUEVO: Total de dispositivos registrados en el servidor
                'active_users': 0,
                'active_devices': 0,
                'users_percentage': (server.current_users / server.max_users * 100) if server.max_users > 0 else 0,
                'devices_percentage': 0  # Se calculará
            }
            
            try:
                # Obtener recuento de dispositivos teóricos de la base de datos (basado en cuentas)
                accounts = session.query(Account).filter_by(
                    server_id=server.id,
                    is_active=True
                ).all()
                
                theoretical_device_count = 0
                for account in accounts:
                    if account.plan == '3_screens':
                        theoretical_device_count += 3
                    elif account.plan in ['1_screen', 'live_tv', 'demo']:
                        theoretical_device_count += 1
                
                server_status['db_devices'] = theoretical_device_count
                server_status['devices_percentage'] = (theoretical_device_count / server.max_devices * 100) if server.max_devices > 0 else 0
                
                # Intentar conectar al servidor para obtener recuentos reales
                # Intentar conectar al servidor para obtener recuentos reales
                
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # Verificar si el servidor está en línea y obtener información del sistema
                    system_url = f"{server.url}/System/Info?api_key={server.api_key}"
                    if server.url.endswith('/'):
                        system_url = f"{server.url}System/Info?api_key={server.api_key}"
                    
                    system_response = await client.get(system_url)
                    
                    if system_response.status_code == 200:
                        server_status['online'] = True
                        
                        # CORRECCIÓN 1: Obtener el total real de dispositivos registrados
                        devices_url = f"{server.url}/Devices?api_key={server.api_key}"
                        if server.url.endswith('/'):
                            devices_url = f"{server.url}Devices?api_key={server.api_key}"
                        
                        devices_response = await client.get(devices_url)
                        
                        if devices_response.status_code == 200:
                            devices_data = devices_response.json()
                            
                            # Manejar diferentes formatos de respuesta para dispositivos
                            if isinstance(devices_data, dict):
                                if 'TotalRecordCount' in devices_data:
                                    server_status['total_registered_devices'] = devices_data['TotalRecordCount']
                                elif 'Items' in devices_data:
                                    server_status['total_registered_devices'] = len(devices_data.get('Items', []))
                                else:
                                    server_status['total_registered_devices'] = 1
                            elif isinstance(devices_data, list):
                                server_status['total_registered_devices'] = len(devices_data)
                            else:
                                server_status['total_registered_devices'] = 0
                        
                        # CORRECCIÓN 2: Obtener total de usuarios reales en el servidor
                        users_url = f"{server.url}/Users?api_key={server.api_key}"
                        if server.url.endswith('/'):
                            users_url = f"{server.url}Users?api_key={server.api_key}"
                        
                        users_response = await client.get(users_url)
                        total_server_users = 0
                        
                        if users_response.status_code == 200:
                            users_data = users_response.json()
                            if isinstance(users_data, list):
                                total_server_users = len(users_data)
                        
                        # Asignar los totales a las variables que usa el reporte
                        server_status['active_users'] = total_server_users
                        
                        # Para dispositivos, ya habíamos obtenido 'devices_response' antes (ver líneas 922-926 original)
                        # en 'server_status['total_registered_devices']'.
                        # Así que asignamos ese valor a active_devices.
                        server_status['active_devices'] = server_status.get('total_registered_devices', 0)
                        
                        # Guardamos también los valores explícitos
                        server_status['total_server_users'] = total_server_users
                        server_status['total_server_devices'] = server_status.get('total_registered_devices', 0)
                        
                        if sessions_response.status_code == 200:
                            sessions_data = sessions_response.json()
                            
                            # Asegurarse de que sessions es una lista
                            if isinstance(sessions_data, list):
                                active_sessions = sessions_data
                            elif isinstance(sessions_data, dict):
                                if 'Sessions' in sessions_data:
                                    active_sessions = sessions_data.get('Sessions', [])
                                elif 'Items' in sessions_data:
                                    active_sessions = sessions_data.get('Items', [])
                                else:
                                    # Si no tiene estructura conocida, intentar usar los datos directamente
                                    active_sessions = [sessions_data] if sessions_data else []
                            else:
                                active_sessions = []
                            
                            # CORRECCIÓN 3: Contar usuarios y dispositivos únicos correctamente
                            active_users = set()
                            active_devices = set()
                            
                            logger.info(f"Servidor {server.name}: Procesando {len(active_sessions)} sesiones")
                            
                            for user_session in active_sessions:
                                if isinstance(user_session, dict):
                                    user_id = user_session.get('UserId')
                                    device_id = user_session.get('DeviceId')
                                    
                                    # Solo contar si tanto UserId como DeviceId están presentes y no son vacíos
                                    if user_id and str(user_id).strip():
                                        active_users.add(user_id)
                                        
                                    if device_id and str(device_id).strip():
                                        active_devices.add(device_id)
                                        logger.debug(f"Dispositivo activo agregado: {device_id}")
                        
                            server_status['active_users'] = len(active_users)
                            server_status['active_devices'] = len(active_devices)
                            
                            logger.info(f"Servidor {server.name}: {len(active_users)} usuarios únicos, {len(active_devices)} dispositivos únicos")
                        else:
                            logger.warning(f"No se pudieron obtener sesiones del servidor {server.name}: {sessions_response.status_code}")
                    else:
                        logger.warning(f"Servidor {server.name} no responde o está offline")
            
            except Exception as e:
                logger.error(f"Error al obtener estado del servidor {server.name}: {e}")
                # Mantener estado offline predeterminado
            
            result.append(server_status)
        
        if session:
            session.close()
        return True, result
        
    except Exception as e:
        if session:
            session.rollback()
        logger.error(f"Error al obtener estado de los servidores Jellyfin: {e}")
        return False, f"Error: {str(e)}"
    finally:
        if session:
            session.close()
