import logging
import httpx
from sqlalchemy import func
from database import Session, Server
from database import Role

logger = logging.getLogger(__name__)

async def validate_server_connection(url, api_key, admin_username, service):
    """Valida la conexión a un servidor y obtiene el ID del administrador"""
    try:
        # Eliminar la barra final si existe
        if url.endswith('/'):
            url = url[:-1]
        
        timeout = 10.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Verificar conectividad básica
            system_info_url = f"{url}/System/Info?api_key={api_key}"
            response = await client.get(system_info_url)
            
            if response.status_code != 200:
                return False, "No se pudo conectar al servidor. Verifique la URL y el API key."

            system_info = response.json()
            server_name = system_info.get('ServerName', f'Servidor {service.capitalize()}')

            # Obtener el ID del administrador
            users_url = f"{url}/Users?api_key={api_key}"
            users_response = await client.get(users_url)
            
            if users_response.status_code != 200:
                return False, "No se pudo obtener la lista de usuarios."

            users = users_response.json()
            admin_user = next((user for user in users if user.get('Name') == admin_username), None)

            if not admin_user:
                return False, f"No se encontró al usuario administrador '{admin_username}'."

            admin_id = admin_user.get('Id')

            # Verificar que el usuario tiene privilegios de administrador
            if not admin_user.get('Policy', {}).get('IsAdministrator', False):
                return False, f"El usuario '{admin_username}' no tiene privilegios de administrador."

            return True, {
                "admin_id": admin_id,
                "server_name": server_name
            }

    except httpx.RequestError as e:
        logger.error(f"Error al conectar con el servidor: {e}")
        return False, f"Error de conexión: {str(e)}"
    except Exception as e:
        logger.error(f"Error general al validar servidor: {e}")
        return False, f"Error: {str(e)}"

async def add_server_to_db(service, url, api_key, admin_username, admin_id, server_name, max_devices, max_users):
    """Agrega un servidor a la base de datos"""
    session = Session()
    
    try:
        # Determinar el próximo ID disponible según el tipo de servicio
        service_upper = service.upper()
        
        if service_upper == "EMBY":
            # Para Emby, buscar el ID más alto existente menor que 100
            max_id_result = session.query(func.max(Server.id)).filter(
                Server.service == "EMBY",
                Server.id < 100
            ).scalar()
            
            next_id = 1 if max_id_result is None else max_id_result + 1
            
            # Verificar que el ID no exceda 100
            if next_id > 100:
                session.close()
                return False, "No se pueden agregar más servidores Emby. Límite máximo alcanzado."
                
            # Encontrar el siguiente ID disponible si este ya está ocupado
            while session.query(Server).filter_by(id=next_id).count() > 0 and next_id <= 100:
                next_id += 1
                
            if next_id > 100:
                session.close()
                return False, "No se pueden agregar más servidores Emby. Límite máximo alcanzado."
        else:  # JELLYFIN
            # Para Jellyfin, buscar el ID más alto existente mayor o igual a 101
            max_id_result = session.query(func.max(Server.id)).filter(
                Server.service == "JELLYFIN",
                Server.id >= 101
            ).scalar()
            
            next_id = 101 if max_id_result is None else max_id_result + 1
            
            # Encontrar el siguiente ID disponible si este ya está ocupado
            while session.query(Server).filter_by(id=next_id).count() > 0:
                next_id += 1
        
        # Crear nuevo servidor con el ID asignado
        new_server = Server(
            id=next_id,
            name=server_name,
            service=service_upper,
            url=url,
            api_key=api_key,
            admin_username=admin_username,
            admin_id=admin_id,
            max_devices=max_devices,
            max_users=max_users,
            current_users=0,
            is_active=True
        )
        
        session.add(new_server)
        session.commit()
        
        return True, f"Servidor '{server_name}' agregado correctamente con ID {next_id}."
    except Exception as e:
        session.rollback()
        logger.error(f"Error al agregar servidor a la base de datos: {e}")
        return False, f"Error al guardar el servidor: {str(e)}"
    finally:
        session.close()

async def update_server_in_db(server_id, url=None, api_key=None, name=None, 
                             max_devices=None, max_users=None, is_active=None):
    """Actualiza un servidor en la base de datos"""
    session = Session()
    
    try:
        server = session.query(Server).filter_by(id=server_id).first()
        
        if not server:
            session.close()
            return False, "Servidor no encontrado."
        
        # Actualizar campos si se proporcionan nuevos valores
        if url is not None:
            server.url = url
        if api_key is not None:
            server.api_key = api_key
        if name is not None:
            server.name = name
        if max_devices is not None:
            server.max_devices = max_devices
        if max_users is not None:
            server.max_users = max_users
        if is_active is not None:
            server.is_active = is_active
        
        session.commit()
        return True, f"Servidor '{server.name}' actualizado correctamente."
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error al actualizar servidor: {e}")
        return False, f"Error al actualizar el servidor: {str(e)}"
    finally:
        session.close()

async def delete_server_from_db(server_id, force=False):
    """
    Elimina un servidor de la base de datos
    
    Args:
        server_id: ID del servidor a eliminar
        force: Si es True, marcará todas las cuentas asociadas como inactivas en lugar de impedir la eliminación
    """
    session = Session()
    
    try:
        server = session.query(Server).filter_by(id=server_id).first()
        
        if not server:
            session.close()
            return False, "Servidor no encontrado."
        
        server_name = server.name
        
        # Verificar si hay cuentas asociadas a este servidor
        from database import Account
        accounts = session.query(Account).filter_by(server_id=server_id).all()
        
        if accounts and not force:
            session.close()
            return False, f"No se puede eliminar el servidor '{server_name}' porque tiene {len(accounts)} cuentas asociadas. Usa la opción forzar para eliminar de todos modos."
        
        # Si hay cuentas y se fuerza la eliminación, marcar todas como inactivas
        accounts_count = 0
        if accounts and force:
            for account in accounts:
                account.is_active = False
                accounts_count += 1
            
            # Mensaje para el log
            logger.info(f"Se marcaron {accounts_count} cuentas como inactivas al eliminar el servidor '{server_name}'")
        
        # Eliminar el servidor
        session.delete(server)
        session.commit()
        
        result_message = f"Servidor '{server_name}' eliminado correctamente."
        if accounts_count > 0:
            result_message += f" {accounts_count} cuentas marcadas como inactivas."
        
        return True, result_message
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error al eliminar servidor: {e}")
        return False, f"Error al eliminar el servidor: {str(e)}"
    finally:
        session.close()
