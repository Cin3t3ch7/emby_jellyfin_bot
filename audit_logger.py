"""
Sistema de logging de auditoría para el bot
Registra todas las operaciones importantes del sistema
"""
import logging
from datetime import datetime
from database import Session, User
from config import ADMIN_IDS

# Configurar logger de auditoría
audit_logger = logging.getLogger('audit')
audit_handler = logging.FileHandler('audit.log', encoding='utf-8')
audit_formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
audit_handler.setFormatter(audit_formatter)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)


def log_user_created(telegram_id, created_by_id, role, credits):
    """Registra la creación de un usuario"""
    audit_logger.info(
        f"USER_CREATED - TelegramID: {telegram_id} | "
        f"CreatedBy: {created_by_id} | "
        f"Role: {role} | "
        f"Credits: {credits}"
    )


def log_user_deleted(telegram_id, deleted_by_id, user_info):
    """Registra la eliminación de un usuario"""
    audit_logger.warning(
        f"USER_DELETED - TelegramID: {telegram_id} | "
        f"DeletedBy: {deleted_by_id} | "
        f"UserInfo: {user_info}"
    )


def log_credits_modified(target_id, modified_by_id, action, amount, old_credits, new_credits):
    """Registra modificación de créditos"""
    audit_logger.info(
        f"CREDITS_MODIFIED - TargetID: {target_id} | "
        f"ModifiedBy: {modified_by_id} | "
        f"Action: {action} | "
        f"Amount: {amount} | "
        f"OldCredits: {old_credits} | "
        f"NewCredits: {new_credits}"
    )


def log_role_changed(target_id, modified_by_id, old_role, new_role):
    """Registra cambio de rol"""
    audit_logger.warning(
        f"ROLE_CHANGED - TargetID: {target_id} | "
        f"ModifiedBy: {modified_by_id} | "
        f"OldRole: {old_role} | "
        f"NewRole: {new_role}"
    )


def log_account_created(user_id, service, plan, server_id, username):
    """Registra creación de cuenta de servicio"""
    audit_logger.info(
        f"ACCOUNT_CREATED - UserID: {user_id} | "
        f"Service: {service} | "
        f"Plan: {plan} | "
        f"ServerID: {server_id} | "
        f"Username: {username}"
    )


def log_account_deleted(user_id, service, username, server_id, reason="manual"):
    """Registra eliminación de cuenta de servicio"""
    audit_logger.info(
        f"ACCOUNT_DELETED - UserID: {user_id} | "
        f"Service: {service} | "
        f"Username: {username} | "
        f"ServerID: {server_id} | "
        f"Reason: {reason}"
    )


def log_server_added(added_by_id, service, server_name, server_url):
    """Registra adición de servidor"""
    audit_logger.info(
        f"SERVER_ADDED - AddedBy: {added_by_id} | "
        f"Service: {service} | "
        f"Name: {server_name} | "
        f"URL: {server_url}"
    )


def log_server_deleted(deleted_by_id, service, server_name, server_id):
    """Registra eliminación de servidor"""
    audit_logger.warning(
        f"SERVER_DELETED - DeletedBy: {deleted_by_id} | "
        f"Service: {service} | "
        f"Name: {server_name} | "
        f"ServerID: {server_id}"
    )


def log_server_modified(modified_by_id, server_id, server_name, changes):
    """Registra modificación de servidor"""
    audit_logger.info(
        f"SERVER_MODIFIED - ModifiedBy: {modified_by_id} | "
        f"ServerID: {server_id} | "
        f"Name: {server_name} | "
        f"Changes: {changes}"
    )


def log_price_changed(modified_by_id, service, role, plan, old_price, new_price):
    """Registra cambio de precio"""
    audit_logger.info(
        f"PRICE_CHANGED - ModifiedBy: {modified_by_id} | "
        f"Service: {service} | "
        f"Role: {role} | "
        f"Plan: {plan} | "
        f"OldPrice: {old_price} | "
        f"NewPrice: {new_price}"
    )


def log_unauthorized_access(telegram_id, username, command):
    """Registra intento de acceso no autorizado"""
    audit_logger.warning(
        f"UNAUTHORIZED_ACCESS - TelegramID: {telegram_id} | "
        f"Username: {username} | "
        f"Command: {command}"
    )


def log_error(context, error_message):
    """Registra errores del sistema"""
    audit_logger.error(
        f"SYSTEM_ERROR - Context: {context} | "
        f"Error: {error_message}"
    )


def log_expired_accounts_cleanup(accounts_removed, details):
    """Registra limpieza de cuentas expiradas"""
    audit_logger.info(
        f"EXPIRED_CLEANUP - AccountsRemoved: {accounts_removed} | "
        f"Details: {details}"
    )


def log_device_cleanup(devices_removed, servers_affected):
    """Registra limpieza de dispositivos"""
    audit_logger.info(
        f"DEVICE_CLEANUP - DevicesRemoved: {devices_removed} | "
        f"ServersAffected: {servers_affected}"
    )


def log_device_limit_enforcement(user_count, devices_removed, servers_details):
    """Registra aplicación de límites de dispositivos"""
    audit_logger.info(
        f"DEVICE_LIMITS_ENFORCED - UsersChecked: {user_count} | "
        f"DevicesRemoved: {devices_removed} | "
        f"Servers: {servers_details}"
    )


def get_user_info_for_log(telegram_id):
    """Obtiene información resumida del usuario para logs"""
    try:
        session = Session()
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        session.close()

        if user:
            return f"{user.full_name} (@{user.username if user.username else 'N/A'})"
        return "Unknown User"
    except Exception:
        return "Error retrieving user"
