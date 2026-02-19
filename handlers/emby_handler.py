import logging
import random
import string
import uuid
import json
import httpx

from database import Session, Account, Server, User as DbUser, check_demo_limit
from datetime import datetime, timedelta
from config import DEFAULT_ACCOUNT_PASSWORD
from audit_logger import log_account_created
from db_locks import atomic_server_update

logger = logging.getLogger(__name__)

def generate_device_id():
    """Genera un ID de dispositivo único en formato UUID"""
    return str(uuid.uuid4())

def generate_username(is_demo=False):
    """Genera un nombre de usuario único para Emby"""
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

async def create_emby_user(server, plan, duration_days=30):
    """
    Crea un usuario en Emby con las políticas correspondientes al plan
    """
    try:
        # Verificar el tipo de plan
        is_demo = plan == 'demo'
        is_live_tv = plan == 'live_tv' or plan == '2_screens_tv'
        is_two_screens = plan == '2_screens' or plan == '2_screens_tv'
        is_bulk = plan == 'bulk'
        
        # Determinar el límite de streams según el plan
        if is_two_screens:
            stream_limit = "2"
        elif is_bulk:
            stream_limit = "3"
        else:
            stream_limit = "1"
        
        # Generar nombre de usuario
        username = generate_username(is_demo)

        # Generar contraseña aleatoria única para esta cuenta
        password = generate_password()
        
        # Generar un device_id único
        device_id = generate_device_id()
        # NOTA DE SEGURIDAD: NO loggear device IDs ya que son credenciales sensibles
        
        # Normalizar URL del servidor
        url = server.url
        if url.endswith('/'):
            url = url[:-1]
            
        # Preparar las cabeceras comunes
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            'Accept': 'application/json'
        }
        
        # Parámetros comunes
        params = {
            'api_key': server.api_key  # Añadir api_key a los parámetros de consulta
        }
        
        # Headers específicos de Emby
        emby_headers = {
            'X-Emby-Client': 'Emby Web',
            'X-Emby-Device-Name': 'Chrome Windows',
            'X-Emby-Device-Id': device_id,
            'X-Emby-Client-Version': '4.8.9.0',
            'X-Emby-Token': server.api_key,
            'X-Emby-Language': 'es-419'
        }
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Crear usuario
            create_url = f"{url}/emby/Users/New"
            
            create_headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                **headers,
                **emby_headers  # Incluir los headers de Emby
            }
            
            create_data = {
                'Name': username,
                'CopyFromUserId': server.admin_id,
                'UserCopyOptions': 'UserPolicy,UserConfiguration'
            }
            
            response = await client.post(
                create_url, 
                headers=create_headers, 
                params=params,  # Solo incluir api_key como parámetro
                data=create_data
            )
            if response.status_code != 200:
                response_text = response.text
                return False, f"Error al crear usuario: {response_text}"

            # Obtener el ID del usuario creado
            response_json = response.json()
            user_id = response_json.get('Id')
        
            if not user_id:
                return False, "No se pudo obtener el ID del usuario creado"
            
            # 2. Actualizar políticas del usuario
            policy_url = f"{url}/emby/Users/{user_id}/Policy"
            
            # Parámetros adicionales para la política
            policy_params = params.copy()
            policy_params['reqformat'] = 'json'
            
            policy_headers = {
                'Content-Type': 'text/plain',
                **headers,
                **emby_headers  # Incluir los headers de Emby
            }
            
            # Establecer la fecha de expiración para cuentas demo
            if is_demo:
                # Configurar duración especial para demos (1 hora)
                expiry_date = datetime.utcnow() + timedelta(hours=1)
            else:
                expiry_date = datetime.utcnow() + timedelta(days=duration_days)
            
            # Configuración de política exactamente como en el ejemplo
            # Base policy from user request with dynamic overrides
            policy_data = {
                "IsAdministrator": False,
                "IsHidden": False,
                "IsHiddenRemotely": True,
                "IsHiddenFromUnusedDevices": True,
                "IsDisabled": False,
                "LockedOutDate": 0,
                "AllowTagOrRating": False,
                "BlockedTags": [],
                "IsTagBlockingModeInclusive": False,
                "IncludeTags": [],
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
                "AutoRemoteQuality": 0,
                "EnablePlaybackRemuxing": True,
                "EnableContentDeletion": False,
                "RestrictedFeatures": ["notifications"],
                "EnableContentDeletionFromFolders": [],
                "EnableContentDownloading": False,
                "EnableSubtitleDownloading": False,
                "EnableSubtitleManagement": False,
                "EnableSyncTranscoding": False,
                "EnableMediaConversion": False,
                "EnabledChannels": [],
                "EnableAllChannels": True,
                "EnabledFolders": [],
                "EnableAllFolders": True,
                "InvalidLoginAttemptCount": 0,
                "EnablePublicSharing": False,
                "RemoteClientBitrateLimit": 0,
                "AuthenticationProviderId": "Emby.Server.Implementations.Library.DefaultAuthenticationProvider",
                "ExcludedSubFolders": [],
                "SimultaneousStreamLimit": stream_limit,
                "EnabledDevices": [],
                "EnableAllDevices": True,
                "AllowCameraUpload": False,
                "AllowSharingPersonalItems": False,
                "EnableTranscodingQuality": False
            }

            policy_json = json.dumps(policy_data)
            
            policy_response = await client.post(
                policy_url, 
                headers=policy_headers, 
                params=policy_params, 
                data=policy_json
            )
            if policy_response.status_code != 204:
                response_text = policy_response.text
                return False, f"Error al actualizar políticas: {response_text}"
            
            # 3. Establecer contraseña
            password_url = f"{url}/emby/Users/{user_id}/Password"
            
            password_headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                **headers,
                **emby_headers  # Incluir los headers de Emby
            }
            
            password_data = {
                'NewPw': password
            }
            
            password_response = await client.post(
                password_url, 
                headers=password_headers, 
                params=params, 
                data=password_data
            )
            if password_response.status_code != 204:
                response_text = password_response.text
                return False, f"Error al establecer contraseña: {response_text}"

            # Todo se completó correctamente
            return True, {
                "username": username,
                "password": password,
                "user_id": user_id,
                "expiry_date": expiry_date,
                "plan": plan
            }

    except httpx.RequestError as e:
        logger.error(f"Error de conexión con el servidor Emby: {e}")
        return False, f"Error de conexión: {str(e)}"
    except Exception as e:
        logger.error(f"Error general al crear usuario en Emby: {e}")
        return False, f"Error: {str(e)}"

async def create_emby_account_on_server(telegram_user_id, plan, server_id, duration_days=30):
    """
    Proceso completo para crear una cuenta de Emby en un servidor específico
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
        
        # Para cuentas demo, no se cobra
        is_free = plan == 'demo' or db_user.role in ["SUPER_ADMIN", "ADMIN"]
        
        # Verificar créditos (excepto para admin o demo)
        if not is_free:
            from sqlalchemy import text
            # Obtener el precio del plan
            price_query = text("""
                SELECT amount FROM prices 
                WHERE service = 'EMBY' AND role = :role AND plan = :plan
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
            service="EMBY",
            is_active=True
        ).first()
        
        if not server:
            session.close()
            return False, "Servidor no encontrado o no disponible"
        
        # Verificar si el servidor tiene capacidad
        if server.current_users >= server.max_users:
            session.close()
            return False, f"El servidor {server.name} está lleno ({server.current_users}/{server.max_users})"
        
        # Crear usuario en Emby
        success, result = await create_emby_user(server, plan, duration_days)
        
        if not success:
            session.close()
            return False, result
        
        # Crear la cuenta en la base de datos
        account = Account(
            user_id=db_user.id,
            service="EMBY",
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

        # CORRECCIÓN: Usar lock para prevenir race conditions al actualizar contadores
        with atomic_server_update(server.id):
            # Actualizar contador de usuarios en el servidor
            server.current_users += 1

            # Deducir créditos si aplica
            if not is_free:
                db_user.credits -= price

            session.commit()

        # Registrar en auditoría
        log_account_created(db_user.id, "EMBY", plan, server.id, result["username"])

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
        logger.error(f"Error al crear cuenta Emby: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def create_emby_account(telegram_user_id, plan, duration_days=30):
    """
    Proceso completo para crear una cuenta de Emby
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
        
        # Para cuentas demo, no se cobra
        is_free = plan == 'demo' or db_user.role in ["SUPER_ADMIN", "ADMIN"]
        
        # Verificar créditos (excepto para admin o demo)
        if not is_free:
            from sqlalchemy import text
            # Obtener el precio del plan
            price_query = text("""
                SELECT amount FROM prices 
                WHERE service = 'EMBY' AND role = :role AND plan = :plan
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
            service="EMBY",
            is_active=True
        ).filter(Server.current_users < Server.max_users).first()
        
        if not server:
            session.close()
            return False, "No hay servidores disponibles"
        
        # Crear usuario en Emby
        success, result = await create_emby_user(server, plan, duration_days)
        
        if not success:
            session.close()
            return False, result
        
        # Crear la cuenta en la base de datos
        account = Account(
            user_id=db_user.id,
            service="EMBY",
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

        # CORRECCIÓN: Usar lock para prevenir race conditions al actualizar contadores
        with atomic_server_update(server.id):
            # Actualizar contador de usuarios en el servidor
            server.current_users += 1

            # Deducir créditos si aplica
            if not is_free:
                db_user.credits -= price

            session.commit()

        # Registrar en auditoría
        log_account_created(db_user.id, "EMBY", plan, server.id, result["username"])

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
        logger.error(f"Error al crear cuenta Emby: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def delete_emby_user(server, user_id):
    """
    Elimina un usuario del servidor Emby
    
    Args:
        server: Objeto Server de la base de datos
        user_id: ID del usuario en Emby a eliminar
        
    Returns:
        Tuple (success, message): Indica si la operación fue exitosa y un mensaje
    """
    try:
        # Eliminar la barra final si existe en la URL
        url = server.url
        if url.endswith('/'):
            url = url[:-1]
            
        # Generar un device_id único
        device_id = generate_device_id()
        
        # Preparar las cabeceras
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'X-Emby-Client': 'Emby Web',
            'X-Emby-Device-Name': 'Chrome Windows',
            'X-Emby-Device-Id': device_id,
            'X-Emby-Client-Version': '4.8.9.0',
            'X-Emby-Token': server.api_key,
            'X-Emby-Language': 'es-419'
        }
        
        # URL para eliminar usuario
        delete_url = f"{url}/emby/Users/{user_id}?api_key={server.api_key}"

        # Realizar la solicitud DELETE
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(delete_url, headers=headers)
            # Verificar respuesta
            if response.status_code in [204, 200]:
                return True, "Usuario eliminado correctamente del servidor Emby"
            elif response.status_code == 404:
                # Si devuelve 404, el usuario ya no existe, lo cual es un éxito para nosotros
                return True, "El usuario ya no existía en el servidor (404)"
            else:
                response_text = response.text
                return False, f"Error al eliminar usuario: {response_text}"

    except httpx.RequestError as e:
        logger.error(f"Error de conexión con el servidor Emby: {e}")
        return False, f"Error de conexión: {str(e)}"
    except Exception as e:
        logger.error(f"Error general al eliminar usuario en Emby: {e}")
        return False, f"Error: {str(e)}"

async def renew_emby_account(telegram_user_id, username, duration_days):
    """
    Renueva una cuenta de Emby
    """
    session = Session()
    
    try:
        # Buscar la cuenta por nombre de usuario
        account = session.query(Account).filter_by(
            service="EMBY",
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
                WHERE service = 'EMBY' AND role = :role AND plan = :plan
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
        logger.error(f"Error al renovar cuenta Emby: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def delete_emby_account(username):
    """
    Elimina una cuenta Emby completamente (del servidor y de la base de datos)
    
    Args:
        username: Nombre de usuario a eliminar
        
    Returns:
        Tuple (success, message): Indica si la operación fue exitosa y un mensaje
    """
    session = Session()
    
    try:
        # Buscar la cuenta por nombre de usuario (incluyendo inactivas para asegurar eliminación completa)
        account = session.query(Account).filter_by(
            service="EMBY",
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
            
        # Obtener el ID del usuario en Emby (si la cuenta está activa)
        service_user_id = account.service_user_id
        
        # Si la cuenta está activa, intentar eliminarla del servidor
        if account.is_active and service_user_id:
            try:
                success, message = await delete_emby_user(server, service_user_id)
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
        logger.error(f"Error al eliminar cuenta Emby: {e}")
        return False, f"Error: {str(e)}"
    finally:
        session.close()

async def delete_orphaned_emby_devices(server):
    """
    Elimina dispositivos huérfanos en un servidor Emby

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
            devices_url = f"{url}/emby/Devices?api_key={server.api_key}"
            devices_response = await client.get(devices_url)
            if devices_response.status_code != 200:
                response_text = devices_response.text
                return 0, f"Error al obtener dispositivos: {response_text}", []

            devices_json = devices_response.json()
            devices = devices_json.get('Items', [])

            # Obtener todas las sesiones activas
            sessions_url = f"{url}/emby/Sessions?api_key={server.api_key}"
            sessions_response = await client.get(sessions_url)
            if sessions_response.status_code != 200:
                response_text = sessions_response.text
                return 0, f"Error al obtener sesiones: {response_text}", []

            active_sessions = sessions_response.json()
            active_device_ids = [session.get('DeviceId') for session in active_sessions if session.get('DeviceId')]
        
            # Obtener usuarios activos de la base de datos
            from database import Session, Account
            db_session = Session()
            active_accounts = db_session.query(Account).filter_by(
                server_id=server.id,
                service="EMBY",
                is_active=True
            ).all()
            db_session.close()

            # Obtener todos los usuarios del servidor
            users_url = f"{url}/emby/Users?api_key={server.api_key}"
            users_response = await client.get(users_url)
            # IDs de usuarios activos (que tienen cuentas en la BD)
            active_user_ids = set()
            active_usernames = set()  # Nombres de usuario activos en la BD

            if users_response.status_code == 200:
                server_users = users_response.json()
                for user in server_users:
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
            device_id = device.get('Id')
            user_id = device.get('UserId')
            last_user_name = device.get('LastUserName', '')
            app_name = device.get('AppName', '')
            device_name = device.get('Name', 'Desconocido')
            
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
                    delete_url = f"{url}/emby/Devices?Id={device_id}&api_key={server.api_key}"
                    delete_response = await client.delete(delete_url)
                    if delete_response.status_code in [204, 200]:
                        deleted_count += 1
                        logger.info(f"Dispositivo Emby {device_id} ({deleted_devices_info[i]['device_name']}) eliminado del servidor {server.name}")
                    else:
                        response_text = delete_response.text
                        logger.warning(f"Error al eliminar dispositivo Emby {device_id}: {response_text}")
                except Exception as e:
                    logger.error(f"Error al eliminar dispositivo Emby {device_id}: {e}")

        return deleted_count, f"Se eliminaron {deleted_count} dispositivos huérfanos de {len(devices)} totales", deleted_devices_info
        
    except Exception as e:
        logger.error(f"Error al eliminar dispositivos huérfanos en Emby: {e}")
        return 0, f"Error: {str(e)}", []

async def get_emby_servers_status():
    """
    Obtiene el estado de todos los servidores Emby
    """
    session = None
    
    try:
        # Obtener todos los servidores Emby activos
        session = Session()
        servers = session.query(Server).filter_by(service="EMBY", is_active=True).all()
        
        if not servers:
            if session:
                session.close()
            return False, "No hay servidores Emby configurados"
        
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
                'active_users': 0,
                'active_devices': 0,
                'users_percentage': (server.current_users / server.max_users * 100) if server.max_users > 0 else 0,
                'devices_percentage': 0  # Se calculará
            }
            
            try:
                # Obtener recuento de dispositivos de la base de datos (basado en cuentas)
                accounts = session.query(Account).filter_by(
                    server_id=server.id,
                    is_active=True
                ).all()
                
                device_count = 0
                for account in accounts:
                    if account.plan == '2_screens':
                        device_count += 2
                    elif account.plan in ['1_screen', 'live_tv', 'demo']:
                        device_count += 1
                
                server_status['db_devices'] = device_count
                server_status['devices_percentage'] = (device_count / server.max_devices * 100) if server.max_devices > 0 else 0
                
                # Intentar conectar al servidor para obtener recuentos activos
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # Verificar si el servidor está en línea y obtener información del sistema
                    system_url = f"{server.url}/emby/System/Info?api_key={server.api_key}"
                    system_response = await client.get(system_url)
                    if system_response.status_code == 200:
                        server_status['online'] = True

                        # Obtener sesiones activas
                        sessions_url = f"{server.url}/emby/Sessions?api_key={server.api_key}"
                        sessions_response = await client.get(sessions_url)
                        if sessions_response.status_code == 200:
                            sessions_data = sessions_response.json()
                            # Contar usuarios y dispositivos únicos
                            active_users = set()
                            active_devices = set()

                            for user_session in sessions_data:
                                if user_session.get('UserId'):
                                    active_users.add(user_session.get('UserId'))
                                if user_session.get('DeviceId'):
                                    active_devices.add(user_session.get('DeviceId'))

                            server_status['active_users'] = len(active_users)
                            server_status['active_devices'] = len(active_devices)
            
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
        logger.error(f"Error al obtener estado de los servidores Emby: {e}")
        return False, f"Error: {str(e)}"
    finally:
        if session:	
            session.close()
