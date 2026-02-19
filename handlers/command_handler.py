from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackContext
from database import Session, User, Price, Account, Server, check_demo_limit
from utils.keyboards import main_menu_keyboard
from utils.helpers import format_credits, get_role_emoji
from config import ADMIN_IDS, SUPER_ADMIN_IDS
from handlers.auth_handler import notify_admins_about_new_user
from audit_logger import (
    log_user_created, log_user_deleted, log_credits_modified,
    log_role_changed, log_price_changed, log_unauthorized_access
)
import logging
from scheduled_tasks import check_expired_accounts
from database import Role
import psutil
import io
import csv
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: CallbackContext):
    """Maneja el comando /start"""
    user = update.effective_user
    session = Session()
    
    try:
        # Verificar si el usuario es un admin
        if user.id in ADMIN_IDS:
            # Verificar si el admin ya existe en la base de datos
            db_user = session.query(User).filter_by(telegram_id=user.id).first()
            
            if not db_user:
                # Crear usuario admin si no existe
                db_user = User(
                    telegram_id=user.id,
                    username=user.username,
                    full_name=f"{user.first_name} {user.last_name if user.last_name else ''}",
                    role="SUPER_ADMIN" if user.id in SUPER_ADMIN_IDS else "ADMIN",
                    credits=float('inf'),
                    is_authorized=True
                )
                session.add(db_user)
                session.commit()
            
            # Mostrar menú principal para admin
            welcome_message = (
                f"🎉 ¡Bienvenido al Bot!\n\n"
                f"👤 Usuario: {('@' + user.username) if user.username else user.first_name}\n"
                f"🎭 Rol: {get_role_emoji(db_user.role)} {db_user.role.replace('_', ' ').title()}\n"
                f"💰 Créditos disponibles: {format_credits(db_user.credits)}\n\n"
                f"💫 Selecciona una opción del menú:"
            )
            
            await update.message.reply_text(
                welcome_message,
                reply_markup=main_menu_keyboard()
            )
        else:
            # Verificar si el usuario no-admin ya está autorizado
            db_user = session.query(User).filter_by(telegram_id=user.id).first()
            
            if db_user and db_user.is_authorized:
                # Usuario existente y autorizado
                welcome_message = (
                    f"🎉 ¡Bienvenido al Bot!\n\n"
                    f"👤 Usuario: {('@' + user.username) if user.username else user.first_name}\n"
                    f"🎭 Rol: {get_role_emoji(db_user.role)} {db_user.role.replace('_', ' ').title()}\n"
                    f"💰 Créditos disponibles: {format_credits(db_user.credits)}\n\n"
                    f"💫 Selecciona una opción del menú:"
                )
                
                await update.message.reply_text(
                    welcome_message,
                    reply_markup=main_menu_keyboard()
                )
            else:
                # Usuario no autorizado - NO guardarlo en la base de datos
                # Notificar a los admins sobre el nuevo usuario
                await notify_admins_about_new_user(context, user)
                
                # Mensaje para usuario no autorizado
                await update.message.reply_text(
                    "⚠️ No estás autorizado para usar este bot.\n"
                    "Se ha notificado a los administradores sobre tu solicitud.\n"
                    "Por favor, espera a que te den acceso o contacta directamente con ellos."
                )
    except Exception as e:
        logger.error(f"Error en start_command: {e}")
        await update.message.reply_text("❌ Ocurrió un error al iniciar.")
    finally:
        session.close()

async def price_command(update: Update, context: CallbackContext):
    """Maneja el comando /price para gestionar precios"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN y ADMIN pueden gestionar precios
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para gestionar precios.")
        session.close()
        return
    
    # Verificar si hay argumentos
    args = context.args
    if not args or len(args) < 3:
        # Obtener todos los roles desde la base de datos
        roles = session.query(Role).all()
        role_names = [role.name for role in roles]
        
        # Mostrar precios actuales
        emby_prices = session.query(Price).filter_by(service="EMBY").all()
        jellyfin_prices = session.query(Price).filter_by(service="JELLYFIN").all()
        
        prices_message = "📊 *PRECIOS ACTUALES*\n\n"
        
        # Precios de Emby
        prices_message += "*EMBY*\n"
        for role_name in role_names:
            prices_for_role = [p for p in emby_prices if p.role == role_name]
            if prices_for_role:  # Solo mostrar roles que tengan precios
                prices_message += f"_{role_name}_:\n"
                for price in prices_for_role:
                    plan_display = price.plan.replace('_', ' ')
                    prices_message += f"  • {plan_display}: ${price.amount:,.0f}\n"
        
        prices_message += "\n*JELLYFIN*\n"
        for role_name in role_names:
            prices_for_role = [p for p in jellyfin_prices if p.role == role_name]
            if prices_for_role:  # Solo mostrar roles que tengan precios
                prices_message += f"_{role_name}_:\n"
                for price in prices_for_role:
                    plan_display = price.plan.replace('_', ' ')
                    prices_message += f"  • {plan_display}: ${price.amount:,.0f}\n"
        
        prices_message += "\n*Uso:*\n"
        prices_message += "/price SERVICE ROLE PLAN AMOUNT\n"
        prices_message += "Ejemplo: /price EMBY RESELLER 1\\_screen 5000\n\n"
        prices_message += "*Para eliminar un plan:*\n"
        prices_message += "/price delete SERVICE ROLE PLAN\n"
        prices_message += "Ejemplo: /price delete EMBY SUPERRESELLER 2\\_screens"
        
        await update.message.reply_text(prices_message, parse_mode=ParseMode.MARKDOWN)
        session.close()
        return
    
    # Verificar si es comando de eliminar
    if args[0].lower() == "delete":
        # Debe tener al menos 4 argumentos para eliminar: "delete", servicio, rol y plan
        if len(args) < 4:
            await update.message.reply_text(
                "⚠️ Formato incorrecto para eliminar precio.\n"
                "Uso: /price delete SERVICE ROLE PLAN\n"
                "Ejemplo: /price delete EMBY SUPERRESELLER 2\\_screens",
                parse_mode=ParseMode.MARKDOWN
            )
            session.close()
            return
            
        try:
            service = args[1].upper()
            role = args[2].upper()
            plan = args[3].lower()
            
            if service not in ["EMBY", "JELLYFIN"]:
                await update.message.reply_text("⚠️ Servicio no válido. Use EMBY o JELLYFIN.")
                session.close()
                return
                
            # Buscar el precio a eliminar
            price = session.query(Price).filter_by(
                service=service,
                role=role,
                plan=plan
            ).first()
            
            if not price:
                await update.message.reply_text(
                    f"⚠️ No se encontró el precio para:\n"
                    f"Servicio: {service}\n"
                    f"Rol: {role}\n"
                    f"Plan: {plan.replace('_', ' ')}"
                )
                session.close()
                return
                
            # Guardar información para el mensaje
            amount = price.amount
            
            # Eliminar el precio
            session.delete(price)
            session.commit()
            
            await update.message.reply_text(
                f"✅ Precio eliminado correctamente:\n"
                f"Servicio: {service}\n"
                f"Rol: {role}\n"
                f"Plan: {plan.replace('_', ' ')}\n"
                f"Monto: ${amount:,.0f}"
            )
            
        except Exception as e:
            logger.error(f"Error al eliminar precio: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
            
        session.close()
        return
    
    # Actualizar precio (código existente)
    try:
        service = args[0].upper()
        role = args[1].upper()
        plan = args[2].lower()
        amount = float(args[3])
        
        if service not in ["EMBY", "JELLYFIN"]:
            await update.message.reply_text("⚠️ Servicio no válido. Use EMBY o JELLYFIN.")
            session.close()
            return
        
        # Verificar si el rol existe, si no, crearlo automáticamente
        existing_role = session.query(Role).filter_by(name=role).first()
        if not existing_role:
            # Crear el rol automáticamente
            new_role = Role(
                name=role,
                description=f"Rol creado automáticamente desde comando price",
                is_admin=False
            )
            session.add(new_role)
            session.commit()
            
            await update.message.reply_text(
                f"✅ Rol '{role}' creado automáticamente."
            )
        
        # Buscar precio existente
        price = session.query(Price).filter_by(
            service=service,
            role=role,
            plan=plan
        ).first()
        
        if price:
            # Actualizar precio existente
            old_amount = price.amount
            price.amount = amount
            session.commit()

            # Registrar en auditoría
            log_price_changed(user.id, service, role, plan, old_amount, amount)

            await update.message.reply_text(
                f"✅ Precio actualizado:\n"
                f"Servicio: {service}\n"
                f"Rol: {role}\n"
                f"Plan: {plan.replace('_', ' ')}\n"
                f"Precio anterior: ${old_amount:,.0f}\n"
                f"Precio nuevo: ${amount:,.0f}"
            )
        else:
            # Crear nuevo precio
            new_price = Price(
                service=service,
                role=role,
                plan=plan,
                amount=amount
            )
            session.add(new_price)
            session.commit()
            await update.message.reply_text(
                f"✅ Nuevo precio añadido:\n"
                f"Servicio: {service}\n"
                f"Rol: {role}\n"
                f"Plan: {plan.replace('_', ' ')}\n"
                f"Precio: ${amount:,.0f}"
            )
    except ValueError:
        await update.message.reply_text("⚠️ El monto debe ser un número.")
    except Exception as e:
        logger.error(f"Error al actualizar precio: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    session.close()

async def role_command(update: Update, context: CallbackContext):
    """Maneja el comando /role para gestionar roles"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN puede gestionar roles
    if db_user.role != "SUPER_ADMIN":
        await update.message.reply_text("⚠️ No tienes permiso para gestionar roles.")
        session.close()
        return
    
    # Verificar si hay argumentos
    args = context.args
    if not args:
        # Mostrar ayuda
        await show_role_help(update, session)
        session.close()
        return
    
    action = args[0].lower()
    
    # Listar roles
    if action == "list" or action == "help":
        await list_roles(update, session)
        session.close()
        return
    
    # Acciones que requieren más argumentos
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ Argumentos insuficientes. Usa `/role help` para ver la ayuda.",
            parse_mode=ParseMode.MARKDOWN
        )
        session.close()
        return
    
    role_name = args[1].upper()
    
    # Añadir rol
    if action == "add":
        await add_role(update, context, session, role_name, args[2:])
    
    # Eliminar rol
    elif action == "del" or action == "delete" or action == "remove":
        await delete_role(update, session, role_name)
    
    # Comando no reconocido
    else:
        await update.message.reply_text(
            f"⚠️ Acción '{action}' no reconocida. Usa `/role help` para ver la ayuda.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    session.close()

async def show_role_help(update, session):
    """Muestra la ayuda del comando role"""
    help_message = (
        "👥 *Gestión de Roles*\n\n"
        "*Comandos disponibles:*\n"
        "• `/role list` - Muestra todos los roles\n"
        "• `/role add ROLE DESCRIPCIÓN [admin]` - Añade un nuevo rol\n"
        "• `/role del ROLE` - Elimina un rol\n\n"
        "*Ejemplos:*\n"
        "• `/role add PREMIUM \"Cliente Premium\"`\n"
        "• `/role add ADMIN \"Administrador\" admin` (con permisos de admin)\n"
        "• `/role del PREMIUM`\n\n"
        "⚠️ Nota: No se pueden eliminar roles que tengan usuarios asignados."
    )
    
    await update.message.reply_text(help_message, parse_mode=ParseMode.MARKDOWN)

async def list_roles(update, session):
    """Lista todos los roles disponibles"""
    roles = session.query(Role).all()
    
    if not roles:
        await update.message.reply_text("⚠️ No hay roles definidos en el sistema.")
        return
    
    message = "👥 *Roles Disponibles*\n\n"
    
    for role in roles:
        admin_status = "✅" if role.is_admin else "❌"
        user_count = session.query(User).filter_by(role=role.name).count()
        message += (
            f"• *{role.name}*\n"
            f"  Descripción: {role.description}\n"
            f"  Admin: {admin_status}\n"
            f"  Usuarios: {user_count}\n\n"
        )
    
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def add_role(update, context, session, role_name, args):
    """Añade un nuevo rol"""
    # Verificar si el rol ya existe
    existing_role = session.query(Role).filter_by(name=role_name).first()
    if existing_role:
        await update.message.reply_text(
            f"⚠️ El rol '{role_name}' ya existe.\n"
            f"Descripción: {existing_role.description}\n"
            f"Admin: {'✅' if existing_role.is_admin else '❌'}"
        )
        return
    
    # Procesar argumentos
    description = ""
    is_admin = False
    
    if len(args) > 0:
        # Reconstruir la descripción desde los argumentos
        description_parts = []
        for arg in args:
            if arg.lower() == "admin":
                is_admin = True
            else:
                description_parts.append(arg)
        
        description = " ".join(description_parts)
        
        # Eliminar comillas si las hay
        if description.startswith('"') and description.endswith('"'):
            description = description[1:-1]
    
    # Crear el nuevo rol
    new_role = Role(
        name=role_name,
        description=description,
        is_admin=is_admin
    )
    
    try:
        session.add(new_role)
        session.commit()
        
        await update.message.reply_text(
            f"✅ Rol '{role_name}' creado correctamente.\n"
            f"Descripción: {description}\n"
            f"Admin: {'✅' if is_admin else '❌'}"
        )
    except Exception as e:
        session.rollback()
        logger.error(f"Error al crear rol: {e}")
        await update.message.reply_text(f"❌ Error al crear rol: {str(e)}")

async def delete_role(update, session, role_name):
    """Elimina un rol existente"""
    # Verificar si el rol existe
    role = session.query(Role).filter_by(name=role_name).first()
    if not role:
        await update.message.reply_text(f"⚠️ El rol '{role_name}' no existe.")
        return
    
    # Verificar si hay usuarios con este rol
    user_count = session.query(User).filter_by(role=role_name).count()
    if user_count > 0:
        await update.message.reply_text(
            f"⚠️ No se puede eliminar el rol '{role_name}' porque tiene {user_count} usuarios asignados.\n"
            f"Cambia el rol de estos usuarios antes de eliminarlo."
        )
        return
    
    # Verificar si es un rol protegido
    if role_name in ["SUPER_ADMIN"]:
        await update.message.reply_text(
            f"⚠️ No se puede eliminar el rol '{role_name}' porque es un rol protegido del sistema."
        )
        return
    
    # Eliminar el rol
    try:
        # También eliminar los precios asociados a este rol
        session.query(Price).filter_by(role=role_name).delete()
        
        # Eliminar el rol
        session.delete(role)
        session.commit()
        
        await update.message.reply_text(f"✅ Rol '{role_name}' eliminado correctamente.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error al eliminar rol: {e}")
        await update.message.reply_text(f"❌ Error al eliminar rol: {str(e)}")

async def adduser_command(update: Update, context: CallbackContext):
    """Maneja el comando /adduser para agregar usuarios"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Verificar si es administrador
    admin_roles = session.query(Role).filter_by(is_admin=True).all()
    admin_role_names = [role.name for role in admin_roles]
    
    # Solo usuarios con roles de administrador pueden agregar usuarios
    if db_user.role not in admin_role_names:
        await update.message.reply_text("⚠️ No tienes permiso para agregar usuarios.")
        session.close()
        return
    
    # Verificar argumentos
    args = context.args
    if not args or len(args) < 3:
        # Obtener roles disponibles para mostrar
        available_roles = session.query(Role).filter(Role.name != "SUPER_ADMIN").all()
        roles_list = "\n".join([f"• {role.name} - {role.description}" for role in available_roles])
        
        await update.message.reply_text(
            "📝 <b>Uso:</b> /adduser TELEGRAM_ID ROLE CREDITS\n\n"
            "Ejemplo: /adduser 123456789 RESELLER 50000\n\n"
            "<b>Roles disponibles:</b>\n"
            f"{roles_list}",
            parse_mode=ParseMode.HTML
        )
        session.close()
        return
    
    try:
        telegram_id = int(args[0])

        # Validar que el telegram_id sea positivo
        if telegram_id <= 0:
            await update.message.reply_text("⚠️ El ID de Telegram debe ser un número positivo.")
            session.close()
            return

        role = args[1].upper()
        credits = float(args[2])

        # Validar que los créditos no sean negativos
        if credits < 0:
            await update.message.reply_text("⚠️ Los créditos no pueden ser negativos.")
            session.close()
            return
        
        # Verificar si el rol existe
        role_exists = session.query(Role).filter_by(name=role).first()
        
        if not role_exists:
            # Obtener roles disponibles para mostrar
            available_roles = session.query(Role).filter(Role.name != "SUPER_ADMIN").all()
            roles_list = ", ".join([role.name for role in available_roles])
            
            await update.message.reply_text(
                f"⚠️ Rol '{role}' no válido. Roles disponibles: {roles_list}\n\n"
                "Para añadir un nuevo rol, usa el comando `/role add ROLE DESCRIPCION`"
            )
            session.close()
            return
        
        # No permitir asignar el rol SUPER_ADMIN a través de este comando
        if role == "SUPER_ADMIN" and db_user.role != "SUPER_ADMIN":
            await update.message.reply_text("⚠️ Solo el Super Admin puede asignar el rol SUPER_ADMIN.")
            session.close()
            return
        
        # Verificar si el usuario ya existe
        existing_user = session.query(User).filter_by(telegram_id=telegram_id).first()
        
        if existing_user:
            # Actualizar usuario existente
            old_role = existing_user.role
            old_credits = existing_user.credits
            
            existing_user.role = role
            existing_user.credits = credits
            existing_user.is_authorized = True
            
            session.commit()
            
            await update.message.reply_text(
                f"✅ Usuario actualizado:\n"
                f"ID: {telegram_id}\n"
                f"Rol anterior: {old_role}\n"
                f"Rol nuevo: {role}\n"
                f"Créditos anteriores: {format_credits(old_credits)}\n"
                f"Créditos nuevos: {format_credits(credits)}"
            )
        else:
            # Crear nuevo usuario
            new_user = User(
                telegram_id=telegram_id,
                role=role,
                credits=credits,
                is_authorized=True,
                full_name="Usuario Pendiente"
            )
            session.add(new_user)
            session.commit()

            # Registrar en auditoría
            log_user_created(telegram_id, user.id, role, credits)

            await update.message.reply_text(
                f"✅ Nuevo usuario agregado:\n"
                f"ID: {telegram_id}\n"
                f"Rol: {role}\n"
                f"Créditos: {format_credits(credits)}\n\n"
                f"El nombre y username se actualizarán cuando el usuario interactúe con el bot."
            )
    except ValueError:
        await update.message.reply_text("⚠️ ID y créditos deben ser números.")
    except Exception as e:
        logger.error(f"Error al agregar usuario: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    session.close()

async def deluser_command(update: Update, context: CallbackContext):
    """Maneja el comando /deluser para eliminar usuarios"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN y ADMIN pueden eliminar usuarios
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para eliminar usuarios.")
        session.close()
        return
    
    # Verificar argumentos
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 <b>Uso:</b> /deluser TELEGRAM_ID\n"
            "Ejemplo: <code>/deluser 123456789</code>",
            parse_mode=ParseMode.HTML
        )
        session.close()
        return
    
    try:
        telegram_id = int(args[0])
        
        # No se puede eliminar al SUPER_ADMIN
        if telegram_id in SUPER_ADMIN_IDS:
            await update.message.reply_text("⚠️ No puedes eliminar a un Super Admin.")
            session.close()
            return
        
        # Buscar usuario
        user_to_delete = session.query(User).filter_by(telegram_id=telegram_id).first()
        
        if not user_to_delete:
            await update.message.reply_text(f"⚠️ Usuario con ID {telegram_id} no encontrado.")
            session.close()
            return
        
        # Guardar info para el mensaje
        user_info = {
            "id": user_to_delete.telegram_id,
            "name": user_to_delete.full_name,
            "username": user_to_delete.username,
            "role": user_to_delete.role
        }
        
        # Eliminar usuario
        session.delete(user_to_delete)
        session.commit()

        # Registrar en auditoría
        log_user_deleted(telegram_id, user.id, user_info)

        await update.message.reply_text(
            f"✅ Usuario eliminado:\n"
            f"ID: {user_info['id']}\n"
            f"Nombre: {user_info['name']}\n"
            f"Username: {('@' + user_info['username']) if user_info['username'] else 'No establecido'}\n"
            f"Rol: {user_info['role']}"
        )
    except ValueError:
        await update.message.reply_text("⚠️ El ID debe ser un número.")
    except Exception as e:
        logger.error(f"Error al eliminar usuario: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    session.close()

async def credits_command(update: Update, context: CallbackContext):
    """Maneja el comando /credits para gestionar créditos"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN y ADMIN pueden gestionar créditos
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para gestionar créditos.")
        session.close()
        return
    
    # Verificar argumentos
    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "📝 <b>Uso:</b> /credits [add|remove] TELEGRAM_ID AMOUNT\n"
            "Ejemplos:\n"
            "• <code>/credits add 123456789 10000</code> - Agrega créditos\n"
            "• <code>/credits remove 123456789 5000</code> - Quita créditos",
            parse_mode=ParseMode.HTML
        )
        session.close()
        return
    
    try:
        action = args[0].lower()
        telegram_id = int(args[1])
        amount = float(args[2])

        # Validar telegram_id
        if telegram_id <= 0:
            await update.message.reply_text("⚠️ El ID de Telegram debe ser un número positivo.")
            session.close()
            return

        # Validar amount
        if amount < 0:
            await update.message.reply_text("⚠️ El monto debe ser un número positivo.")
            session.close()
            return

        if action not in ["add", "remove"]:
            await update.message.reply_text("⚠️ Acción no válida. Use 'add' o 'remove'.")
            session.close()
            return
        
        # Buscar usuario
        target_user = session.query(User).filter_by(telegram_id=telegram_id).first()
        
        if not target_user:
            await update.message.reply_text(f"⚠️ Usuario con ID {telegram_id} no encontrado.")
            session.close()
            return
        
        old_credits = target_user.credits
        
        # Aplicar cambios
        if action == "add":
            if target_user.credits == float('inf'):
                await update.message.reply_text(f"⚠️ El usuario ya tiene créditos infinitos.")
                session.close()
                return
            
            target_user.credits += amount
            action_text = "agregados"
        else:  # remove
            if target_user.credits == float('inf'):
                await update.message.reply_text(f"⚠️ No se pueden quitar créditos de un usuario con créditos infinitos.")
                session.close()
                return
            
            if target_user.credits < amount:
                await update.message.reply_text(f"⚠️ El usuario solo tiene {format_credits(target_user.credits)}.")
                session.close()
                return
            
            target_user.credits -= amount
            action_text = "quitados"

        session.commit()

        # Registrar en auditoría
        log_credits_modified(
            telegram_id, user.id, action, amount,
            old_credits, target_user.credits
        )

        await update.message.reply_text(
            f"✅ Créditos {action_text}:\n"
            f"Usuario: {target_user.full_name} (ID: {target_user.telegram_id})\n"
            f"Monto: {format_credits(amount)}\n"
            f"Créditos anteriores: {format_credits(old_credits)}\n"
            f"Créditos actuales: {format_credits(target_user.credits)}"
        )
    except ValueError:
        await update.message.reply_text("⚠️ ID y monto deben ser números.")
    except Exception as e:
        logger.error(f"Error al gestionar créditos: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    session.close()

# NUEVO COMANDO PARA VERIFICAR DEMOS
async def demos_command(update: Update, context: CallbackContext):
    """Maneja el comando /demos para verificar el estado de demos"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    if not db_user or not db_user.is_authorized:
        await update.message.reply_text("⚠️ No tienes acceso a este comando.")
        session.close()
        return
    
    try:
        # Verificar límite de demos
        can_create, current_count, limit = check_demo_limit(db_user.id, session)
        
        # Obtener demos activas del usuario
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        active_demos = session.query(Account).filter(
            Account.user_id == db_user.id,
            Account.plan == 'demo',
            Account.is_active == True,
            Account.created_date >= today
        ).all()
        
        # Construir mensaje
        status_emoji = "✅" if can_create else "❌"
        message = f"🎭 *Estado de Demos*\n\n"
        message += f"{status_emoji} Demos del día: {current_count}/{limit}\n"
        message += f"🆕 Puedes crear: {'Sí' if can_create else 'No'}\n\n"
        
        if active_demos:
            message += "*Demos activas hoy:*\n"
            for demo in active_demos:
                server = session.query(Server).filter_by(id=demo.server_id).first()
                server_name = server.name if server else "Desconocido"
                time_remaining = demo.expiry_date - datetime.utcnow()
                
                if time_remaining.total_seconds() > 0:
                    minutes_left = int(time_remaining.total_seconds() / 60)
                    message += f"• `{demo.username}` ({server_name}) - {minutes_left} min restantes\n"
                else:
                    message += f"• `{demo.username}` ({server_name}) - ⏰ Vencida\n"
        else:
            message += "No tienes demos activas hoy.\n"
        
        message += f"\n💡 *Tip:* Elimina demos vencidas para crear nuevas"
        
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error al verificar demos: {e}")
        await update.message.reply_text(f"❌ Error al obtener información de demos: {str(e)}")
    
    finally:
        session.close()

# NUEVOS COMANDOS

async def monitor_command(update: Update, context: CallbackContext):
    """Muestra información de monitoreo del sistema"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN y ADMIN pueden ver estadísticas del sistema
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para ver la información del sistema.")
        session.close()
        return
    
    try:
        # Obtener estadísticas de la base de datos
        user_count = session.query(User).count()
        account_count = session.query(Account).count()
        active_account_count = session.query(Account).filter_by(is_active=True).count()
        server_count = session.query(Server).count()
        
        # Obtener tiempo de actividad del sistema
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        
        # Obtener uso de memoria
        memory = psutil.virtual_memory()
        memory_used_percent = memory.percent
        memory_used = memory.used / (1024 * 1024 * 1024)  # GB
        memory_total = memory.total / (1024 * 1024 * 1024)  # GB
        
        # Formatear mensaje
        message = (
            "📊 *MONITOR DEL SISTEMA*\n\n"
            f"🗄️ *Base de datos*\n"
            f"  • Usuarios: {user_count}\n"
            f"  • Cuentas totales: {account_count}\n"
            f"  • Cuentas activas: {active_account_count}\n"
            f"  • Servidores: {server_count}\n\n"
            
            f"⏱️ *Tiempo de actividad*\n"
            f"  • {days} días, {hours} horas, {minutes} minutos\n\n"
            
            f"🖥️ *Uso de recursos*\n"
            f"  • Memoria: {memory_used:.2f} GB / {memory_total:.2f} GB ({memory_used_percent}%)\n"
            f"  • CPU: {psutil.cpu_percent()}%\n\n"
            
            f"🕒 *Último reinicio*\n"
            f"  • {boot_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    
    except Exception as e:
        logger.error(f"Error al obtener información del sistema: {e}")
        await update.message.reply_text(f"❌ Error al obtener información del sistema: {str(e)}")
    
    finally:
        session.close()

async def reset_command(update: Update, context: CallbackContext):
    """Restablece componentes del sistema"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN puede restablecer componentes
    if db_user.role != "SUPER_ADMIN":
        await update.message.reply_text("⚠️ Solo el Super Admin puede ejecutar este comando.")
        session.close()
        return
    
    # Verificar argumentos
    if not context.args:
        await update.message.reply_text(
            "📝 *Uso del comando reset*\n\n"
            "• `/reset expired` - Elimina cuentas vencidas\n"
            "• `/reset devices` - Limpia dispositivos huérfanos\n"
            "• `/reset counters SERVER_ID` - Reinicia contadores de un servidor\n"
            "• `/reset all` - Ejecuta todas las acciones anteriores",
            parse_mode=ParseMode.MARKDOWN
        )
        session.close()
        return
    
    action = context.args[0].lower()
    
    if action == "expired":
        # Procesar cuentas vencidas
        await update.message.reply_text("🔄 Procesando cuentas vencidas... Por favor, espera.")
        from scheduled_tasks import check_expired_accounts
        await check_expired_accounts()
        await update.message.reply_text("✅ Cuentas vencidas procesadas correctamente.")
    
    elif action == "devices":
        # Limpiar dispositivos huérfanos
        await update.message.reply_text("🔄 Limpiando dispositivos huérfanos... Por favor, espera.")
        from scheduled_tasks import cleanup_orphaned_devices
        await cleanup_orphaned_devices(context)
        await update.message.reply_text("✅ Dispositivos huérfanos limpiados correctamente.")
    
    elif action == "counters":
        # Restablecer contadores de servidor
        if len(context.args) < 2:
            await update.message.reply_text("⚠️ Debes especificar el ID del servidor.")
            session.close()
            return
        
        try:
            server_id = int(context.args[1])
            server = session.query(Server).filter_by(id=server_id).first()
            
            if not server:
                await update.message.reply_text(f"❌ Servidor con ID {server_id} no encontrado.")
                session.close()
                return
            
            # Obtener recuento real de usuarios de las cuentas
            active_accounts = session.query(Account).filter_by(
                server_id=server_id,
                is_active=True
            ).count()
            
            # Actualizar contador del servidor
            server.current_users = active_accounts
            session.commit()
            
            await update.message.reply_text(
                f"✅ Contadores del servidor '{server.name}' restablecidos.\n"
                f"Usuarios actuales: {active_accounts}"
            )
        
        except ValueError:
            await update.message.reply_text("⚠️ El ID del servidor debe ser un número.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error al restablecer contadores: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    elif action == "all":
        # Ejecutar todas las opciones de restablecimiento
        await update.message.reply_text("🔄 Ejecutando reset completo... Por favor, espera.")
        
        # Procesar cuentas vencidas
        from scheduled_tasks import check_expired_accounts
        await check_expired_accounts()
        
        # Limpiar dispositivos huérfanos
        from scheduled_tasks import cleanup_orphaned_devices
        await cleanup_orphaned_devices(context)
        
        # Restablecer contadores de todos los servidores
        try:
            servers = session.query(Server).all()
            for server in servers:
                active_accounts = session.query(Account).filter_by(
                    server_id=server.id,
                    is_active=True
                ).count()
                server.current_users = active_accounts
            
            session.commit()
            
            await update.message.reply_text("✅ Reset completo ejecutado correctamente.")
        
        except Exception as e:
            session.rollback()
            logger.error(f"Error al ejecutar reset completo: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    else:
        await update.message.reply_text("⚠️ Acción no reconocida. Usa `/reset` para ver opciones disponibles.")
    
    session.close()

async def list_command(update: Update, context: CallbackContext):
    """Muestra la lista de usuarios con sus créditos y roles"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN y ADMIN pueden ver la lista de usuarios
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para ver la lista de usuarios.")
        session.close()
        return
    
    # Obtener todos los usuarios
    users = session.query(User).order_by(User.role, User.id).all()
    
    if not users:
        await update.message.reply_text("📝 No hay usuarios registrados.")
        session.close()
        return
    
    # Primero enviamos un título
    await update.message.reply_text("👥 <b>LISTA DE USUARIOS</b>", parse_mode=ParseMode.HTML)
    
    # Luego procesamos cada usuario individualmente
    for user_item in users:
        # Formatear créditos
        if user_item.credits == float('inf'):
            credits_display = "∞"
        else:
            credits_display = f"${user_item.credits:,.0f}"
        
        # Crear mensaje para este usuario
        role_emoji = get_role_emoji(user_item.role)
        username_display = f"@{user_item.username}" if user_item.username else "Sin username"
        
        user_message = (
            f"🆔 {user_item.telegram_id} | {role_emoji} {user_item.role} | 💰 {credits_display}\n"
            f"👤 {user_item.full_name} ({username_display})"
        )
        
        # Crear teclado específico para este usuario
        keyboard = [[
            InlineKeyboardButton(
                f"📥 Descargar cuentas de {user_item.full_name}", 
                callback_data=f"download_accounts_{user_item.telegram_id}"
            )
        ]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Enviar mensaje para este usuario con su botón
        await update.message.reply_text(
            user_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    # Finalmente, enviamos un botón para volver al menú principal
    back_keyboard = [[InlineKeyboardButton("🔙 Volver al menú principal", callback_data="main_menu")]]
    back_markup = InlineKeyboardMarkup(back_keyboard)
    
    await update.message.reply_text(
        "Selecciona un usuario para descargar sus cuentas o vuelve al menú principal:",
        reply_markup=back_markup
    )
    
    session.close()

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

async def checkdevices_command(update: Update, context: CallbackContext):
    """Verifica y elimina dispositivos excedentes"""
    user = update.effective_user
    
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    # Solo SUPER_ADMIN y ADMIN pueden usar este comando
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para ejecutar este comando.")
        session.close()
        return
    
    # Verificar si se proporcionó un nombre de usuario específico
    target_username = None
    if context.args and len(context.args) > 0:
        target_username = context.args[0]
    
    # Enviar mensaje inicial
    status_message = await update.message.reply_text(
        f"🔄 Verificando límites de dispositivos..."
        f"{f' para usuario {target_username}' if target_username else ' para todos los usuarios'}"
    )
    
    try:
        # Almacenar el mensaje para actualizar con resultados
        context.user_data['status_message'] = status_message
        if target_username:
            context.user_data['target_username'] = target_username
        
        # Ejecutar la verificación específica para este comando
        from scheduled_tasks import check_and_enforce_device_limits
        await check_and_enforce_device_limits(context)
        
    except Exception as e:
        logger.error(f"Error al verificar límites de dispositivos: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await status_message.edit_text(
            f"❌ Error al verificar límites de dispositivos: {str(e)}"
        )
    
    session.close()

async def check_expired_command(update: Update, context: CallbackContext):
    """
    Comando manual para verificar y eliminar cuentas vencidas.
    Solo accesible para administradores.
    """
    user = update.effective_user
    session = Session()
    db_user = session.query(User).filter_by(telegram_id=user.id).first()
    
    if db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
        await update.message.reply_text("⚠️ No tienes permiso para ejecutar este comando.")
        session.close()
        return

    await update.message.reply_text("⏳ Iniciando verificación de cuentas vencidas...")
    
    try:
        # Ejecutar la verificación de cuentas vencidas
        from scheduled_tasks import check_expired_accounts
        await check_expired_accounts(context)
        await update.message.reply_text("✅ Verificación de cuentas vencidas completada.")
    except Exception as e:
        logger.error(f"Error al ejecutar check_expired manual: {e}")
        await update.message.reply_text(f"❌ Error al ejecutar verificación: {str(e)}")
    finally:
        session.close()

async def list_accounts_command(update: Update, context: CallbackContext):
    """
    Comando para listar todas las cuentas agrupadas por servidor.
    Genera un archivo CSV por cada servidor con:
    - Usuario
    - Fecha de vencimiento (Año-Mes-Dia)
    - Creado por
    - Estado (Activo/Inactivo)
    
    Solo accesible para administradores.
    """
    user = update.effective_user
    session = Session()
    
    try:
        db_user = session.query(User).filter_by(telegram_id=user.id).first()
        
        if not db_user or db_user.role not in ["SUPER_ADMIN", "ADMIN"]:
            await update.message.reply_text("⚠️ No tienes permiso para ejecutar este comando.")
            return

        await update.message.reply_text("⏳ Generando reportes por servidor...")

        # Obtener todos los servidores
        servers = session.query(Server).all()
        
        if not servers:
            await update.message.reply_text("📂 No hay servidores registrados.")
            return

        files_sent = 0

        # Iterar por cada servidor
        for server in servers:
            # Obtener cuentas de este servidor
            # Hacemos JOIN con User para obtener el nombre del creador y su Telegram ID
            accounts = session.query(Account, User.username, User.full_name, User.telegram_id)\
                .outerjoin(User, Account.user_id == User.id)\
                .filter(Account.server_id == server.id)\
                .order_by(Account.expiry_date.asc())\
                .all()
            
            if not accounts:
                continue

            # Generar CSV para este servidor
            output = io.StringIO()
            writer = csv.writer(output)
            # Headers solicitados
            writer.writerow(["Usuario Cuenta", "Vencimiento", "Creado Por", "ID Telegram", "Estado"])
            
            for acc, creator_username, creator_fullname, creator_telegram_id in accounts:
                # Formatear fecha: Año-Mes-Día
                expiry_str = acc.expiry_date.strftime("%Y-%m-%d")
                
                # Nombre del creador
                if creator_username:
                    creator_name = f"@{creator_username}"
                elif creator_fullname:
                    creator_name = creator_fullname
                else:
                    creator_name = "Desconocido"
                
                # ID Telegram
                telegram_id = str(creator_telegram_id) if creator_telegram_id else "N/A"
                
                # Estado
                status = "Activo" if acc.is_active else "Inactivo/Pendiente"
                
                writer.writerow([
                    acc.username,
                    expiry_str,
                    creator_name,
                    telegram_id,
                    status
                ])
            
            output.seek(0)
            
            # Nombre del archivo: ServerName_Service_Date.csv
            # Limpiar nombre del servidor de caracteres inválidos para archivo
            safe_server_name = "".join(c for c in server.name if c.isalnum() or c in (' ', '_', '-')).strip()
            filename = f"{safe_server_name}_{server.service}_{datetime.now().strftime('%Y%m%d')}.csv"
            
            await update.message.reply_document(
                document=io.BytesIO(output.getvalue().encode('utf-8')),
                filename=filename,
                caption=f"📂 Reporte: {server.name} ({len(accounts)} cuentas)"
            )
            files_sent += 1
        
        if files_sent == 0:
            await update.message.reply_text("⚠️ No se encontraron cuentas en ninguno de los servidores.")
        else:
            await update.message.reply_text(f"✅ Se enviaron {files_sent} reportes.")

    except Exception as e:
        logger.error(f"Error en list_accounts_command: {e}")
        await update.message.reply_text("❌ Ocurrió un error al generar los reportes.")
    finally:
        session.close()

async def cleanup_orphaned_command(update: Update, context: CallbackContext):
    """
    Comando manual para iniciar la limpieza de dispositivos huérfanos.
    """
    user = update.effective_user
    
    # Verificar permisos (solo ADMIN y SUPER_ADMIN)
    if user.id not in ADMIN_IDS and user.id not in SUPER_ADMIN_IDS:
        await update.message.reply_text("⚠️ No tienes permiso para ejecutar este comando.")
        return

    # Enviar mensaje inicial
    status_message = await update.message.reply_text("⏳ Iniciando limpieza de dispositivos huérfanos...")
    
    # Guardar el mensaje en context para que la tarea pueda actualizarlo
    context.user_data['status_message'] = status_message
    
    try:
        # Ejecutar la limpieza
        from scheduled_tasks import cleanup_orphaned_devices
        await cleanup_orphaned_devices(context)
        
    except Exception as e:
        logger.error(f"Error al ejecutar limpieza manual: {e}")
        await status_message.edit_text(f"❌ Error al ejecutar la limpieza: {str(e)}")
