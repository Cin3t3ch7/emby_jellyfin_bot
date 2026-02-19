import string
import random
from database import Session, User, Price, Account, Server
from datetime import datetime, timedelta
from database import Role

def generate_password(length=10):
    """Genera una contraseña aleatoria"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def get_user_by_telegram_id(telegram_id):
    """Obtiene un usuario por su ID de Telegram"""
    session = Session()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    session.close()
    return user

def is_user_authorized(telegram_id):
    """Verifica si un usuario está autorizado"""
    user = get_user_by_telegram_id(telegram_id)
    return user and user.is_authorized

def get_role_emoji(role):
    """Devuelve un emoji basado en el rol"""
    emojis = {
        "SUPER_ADMIN": "⭐️",
        "ADMIN": "🔑",
        "RESELLER": "💼",
        "DISTRIBUTOR": "🏪"
    }
    return emojis.get(role, "👤")

def get_price_for_user(user_id, service, plan):
    """Obtiene el precio para un usuario específico basado en su rol"""
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    if not user:
        session.close()
        return None
    
    price = session.query(Price).filter_by(
        service=service.upper(),
        role=user.role,
        plan=plan
    ).first()
    
    session.close()
    return price.amount if price else None

def create_account(user_id, service, plan, duration_days=30):
    """Crea una nueva cuenta para un usuario"""
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    if not user:
        session.close()
        return False, "Usuario no encontrado"
    
    # Obtener precio
    price = session.query(Price).filter_by(
        service=service.upper(),
        role=user.role,
        plan=plan
    ).first()
    
    if not price:
        session.close()
        return False, "Plan no disponible"
    
    # Verificar créditos
    if user.credits < price.amount and user.role != "SUPER_ADMIN" and user.role != "ADMIN":
        session.close()
        return False, f"Créditos insuficientes. Necesitas ${price.amount}"
    
    # Buscar servidor disponible
    server = session.query(Server).filter_by(
        service=service.upper(),
        is_active=True
    ).filter(Server.current_users < Server.max_users).first()
    
    if not server:
        session.close()
        return False, "No hay servidores disponibles"
    
    # Generar credenciales
    username = f"{service.lower()}_{random.randint(1000, 9999)}"
    password = generate_password()
    
    # Crear cuenta
    expiry_date = datetime.utcnow() + timedelta(days=duration_days)
    account = Account(
        user_id=user.id,
        service=service.upper(),
        username=username,
        password=password,
        plan=plan,
        server_id=server.id,
        expiry_date=expiry_date
    )
    session.add(account)
    
    # Actualizar servidor
    server.current_users += 1
    
    # Restar créditos (excepto SUPER_ADMIN y ADMIN)
    if user.role != "SUPER_ADMIN" and user.role != "ADMIN":
        user.credits -= price.amount
    
    session.commit()
    session.close()
    
    return True, {
        "username": username,
        "password": password,
        "expiry_date": expiry_date,
        "server": server.name,
        "url": server.url
    }

def format_credits(credits):
    """Formatea los créditos para mostrar"""
    if credits == float('inf'):
        return "$∞"
    return f"${credits:,.0f}"
