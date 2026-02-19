from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, Boolean, JSON, DateTime, BigInteger, text, inspect
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import BigInteger
import datetime
from config import DB_URL, DEFAULT_EMBY_PRICES, DEFAULT_JELLYFIN_PRICES, DEFAULT_ROLES, SUPER_ADMIN_IDS

# Configurar engine con connection pooling
engine = create_engine(
    DB_URL,
    pool_size=10,  # Número de conexiones permanentes en el pool
    max_overflow=20,  # Conexiones adicionales cuando se necesiten
    pool_pre_ping=True,  # Verificar conexión antes de usar
    pool_recycle=3600,  # Reciclar conexiones cada hora
    echo=False  # No mostrar SQL en logs (cambiar a True para debugging)
)
Base = declarative_base()
Session = sessionmaker(bind=engine)


# Context manager para manejar sesiones de forma segura
from contextlib import contextmanager


@contextmanager
def get_db_session():
    """
    Context manager para garantizar que las sesiones se cierren correctamente.
    Previene fugas de memoria al asegurar que session.close() siempre se ejecute.

    Usage:
        with get_db_session() as session:
            user = session.query(User).first()
            # ... operaciones ...
        # session.close() se llama automáticamente al salir del contexto
    """
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

class Role(Base):
    __tablename__ = 'roles'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    description = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    created_date = Column(DateTime, default=datetime.datetime.utcnow)
    
    @staticmethod
    def initialize_default_roles(session):
        """Inicializa los roles predeterminados si no existen"""
        # Verificar si ya existen roles
        if session.query(Role).count() > 0:
            return
        
        # Agregar roles predeterminados desde la configuración
        for role_data in DEFAULT_ROLES:
            role = Role(**role_data)
            session.add(role)
        
        session.commit()
        
    @staticmethod
    def get_available_roles(session, include_admin=False):
        """Obtiene la lista de roles disponibles"""
        query = session.query(Role)
        if not include_admin:
            query = query.filter(Role.is_admin == False)
        
        return query.all()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True)
    username = Column(String, nullable=True)
    full_name = Column(String)
    role = Column(String, default="DISTRIBUTOR")
    credits = Column(Float, default=0)
    is_authorized = Column(Boolean, default=False)
    joined_date = Column(DateTime, default=datetime.datetime.utcnow)
    
    accounts = relationship("Account", back_populates="user")

class Price(Base):
    __tablename__ = 'prices'
    
    id = Column(Integer, primary_key=True)
    service = Column(String)  # "EMBY" or "JELLYFIN"
    role = Column(String)
    plan = Column(String)
    amount = Column(Float)
    
    @staticmethod
    def initialize_default_prices(session):
        """Inicializa los precios predeterminados si no existen"""
        # (ELIMINADO) No retornar temprano, verificar precio por precio
        # if session.query(Price).count() > 0:
        #     return
        
        # Obtener los roles disponibles
        roles = session.query(Role).all()
        role_names = [role.name for role in roles]
        
        # Obtener precios existentes para evitar duplicados
        existing_prices = session.query(Price).all()
        # Crear un set de claves únicas: (service, role, plan)
        existing_keys = set((p.service, p.role, p.plan) for p in existing_prices)

        # Agregar precios de Emby
        for role, plans in DEFAULT_EMBY_PRICES.items():
            # Verificar si el rol existe (ahora solo SUPERRESELLER debería existir en roles también)
            if role in role_names:
                for plan, amount in plans.items():
                    # Verificar si este precio ya existe
                    if ("EMBY", role, plan) not in existing_keys:
                        price = Price(service="EMBY", role=role, plan=plan, amount=amount)
                        session.add(price)
                        print(f"Agregando precio nuevo: EMBY - {role} - {plan}")
        
        # Agregar precios de Jellyfin
        for role, plans in DEFAULT_JELLYFIN_PRICES.items():
            # Verificar si el rol existe
            if role in role_names:
                for plan, amount in plans.items():
                     # Verificar si este precio ya existe
                    if ("JELLYFIN", role, plan) not in existing_keys:
                        price = Price(service="JELLYFIN", role=role, plan=plan, amount=amount)
                        session.add(price)
                        print(f"Agregando precio nuevo: JELLYFIN - {role} - {plan}")
        
        session.commit()

class Account(Base):
    __tablename__ = 'accounts'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    service = Column(String)  # "EMBY" or "JELLYFIN"
    username = Column(String)
    password = Column(String)
    plan = Column(String)  # "1_screen", "2_screens", etc.
    server_id = Column(Integer)
    service_user_id = Column(String)  # ID del usuario en el servicio (Emby/Jellyfin)
    expiry_date = Column(DateTime)
    is_active = Column(Boolean, default=True)
    created_date = Column(DateTime, default=datetime.datetime.utcnow)
    
    user = relationship("User", back_populates="accounts")

class Server(Base):
    __tablename__ = 'servers'
    
    id = Column(Integer, primary_key=True)
    name = Column(String)
    service = Column(String)  # "EMBY" o "JELLYFIN"
    url = Column(String)
    api_key = Column(String)
    admin_username = Column(String)  # Nombre de usuario del administrador
    admin_id = Column(String)        # ID del usuario administrador
    max_devices = Column(Integer)    # Límite máximo de dispositivos
    max_users = Column(Integer)      # Límite máximo de usuarios
    current_users = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

def check_demo_limit(user_id, session=None):
    """
    Verifica si un usuario ha alcanzado el límite diario de demos (3 por día)
    
    Args:
        user_id: ID del usuario en la base de datos
        session: Sesión de base de datos (opcional, se crea una nueva si no se proporciona)
    
    Returns:
        tuple: (can_create, current_count, limit) 
               - can_create: True si puede crear más demos
               - current_count: Número actual de demos activos creados hoy
               - limit: Límite máximo (3)
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        # Obtener la fecha de hoy (inicio del día)
        today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Contar demos activos creados hoy
        demo_count = session.query(Account).filter(
            Account.user_id == user_id,
            Account.plan == 'demo',
            Account.is_active == True,
            Account.created_date >= today
        ).count()
        
        limit = 3
        can_create = demo_count < limit
        
        return can_create, demo_count, limit
    
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error al verificar límite de demos: {e}")
        # En caso de error, NO permitir la creación (comportamiento seguro)
        return False, 0, 3
    
    finally:
        if close_session:
            session.close()

def update_servers_table():
    """Actualiza la tabla servers con las columnas necesarias"""
    from sqlalchemy import inspect
    
    connection = engine.connect()
    
    try:
        # Verificar si la tabla existe
        inspector = inspect(engine)
        if 'servers' not in inspector.get_table_names():
            print("La tabla servers no existe aún, se creará con todas las columnas.")
            return
        
        # Lista de columnas a verificar y añadir si no existen
        columns_to_add = {
            'admin_username': 'VARCHAR',
            'admin_id': 'VARCHAR',
            'max_devices': 'INTEGER',
            'max_users': 'INTEGER'
        }
        
        # Lista blanca de nombres de columnas y tipos permitidos
        ALLOWED_COLUMNS = {'admin_username', 'admin_id', 'max_devices', 'max_users'}
        ALLOWED_TYPES = {'VARCHAR', 'INTEGER'}

        # Verificar cada columna individualmente
        for column_name, column_type in columns_to_add.items():
            try:
                # VALIDACIÓN ESTRICTA: Solo permitir columnas de la lista blanca
                if column_name not in ALLOWED_COLUMNS:
                    raise ValueError(f"Nombre de columna no permitido: {column_name}")
                if column_type not in ALLOWED_TYPES:
                    raise ValueError(f"Tipo de columna no permitido: {column_type}")

                # Verificar si la columna existe usando parámetros preparados
                result = connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='servers' AND column_name=:col_name"
                ), {"col_name": column_name})
                rows = result.fetchall()

                if len(rows) == 0:
                    # Añadir la columna si no existe
                    # Seguro porque column_name y column_type están validados contra lista blanca
                    connection.execute(text(
                        f"ALTER TABLE servers ADD COLUMN {column_name} {column_type}"
                    ))
                    connection.commit()
                    print(f"Columna {column_name} añadida correctamente.")
                else:
                    print(f"La columna {column_name} ya existe.")
            except Exception as e:
                print(f"Error al verificar o añadir la columna {column_name}: {e}")
        
        print("Verificación y actualización de columnas completada.")
        
    except Exception as e:
        print(f"Error al actualizar la tabla servers: {e}")
    finally:
        connection.close()

def update_account_table():
    """Actualiza la tabla accounts con las columnas necesarias"""
    connection = engine.connect()
    
    try:
        # Verificar si la tabla existe
        inspector = inspect(engine)
        if 'accounts' not in inspector.get_table_names():
            print("La tabla accounts no existe aún, se creará con todas las columnas.")
            return
        
        # Verificar si la columna service_user_id existe
        result = connection.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='accounts' AND column_name=:col_name"
        ), {"col_name": "service_user_id"})
        rows = result.fetchall()

        if len(rows) == 0:
            # Añadir la columna si no existe
            connection.execute(text(
                "ALTER TABLE accounts ADD COLUMN service_user_id VARCHAR"
            ))
            connection.commit()
            print("Columna service_user_id añadida correctamente a la tabla accounts.")
        else:
            print("La columna service_user_id ya existe en la tabla accounts.")
        
    except Exception as e:
        print(f"Error al actualizar la tabla accounts: {e}")
    finally:
        connection.close()

def update_roles_table():
    """Crea y actualiza la tabla roles si es necesario"""
    connection = engine.connect()
    
    try:
        # Verificar si la tabla existe
        inspector = inspect(engine)
        if 'roles' not in inspector.get_table_names():
            print("La tabla roles no existe aún, se creará con todas las columnas.")
            return
        
        # Lista de columnas a verificar y añadir si no existen
        columns_to_add = {
            'description': 'VARCHAR',
            'is_admin': 'BOOLEAN DEFAULT FALSE',
            'created_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }
        
        # Lista blanca de nombres de columnas y tipos permitidos para roles
        ALLOWED_ROLE_COLUMNS = {'description', 'is_admin', 'created_date'}
        ALLOWED_ROLE_TYPES = {
            'VARCHAR',
            'BOOLEAN DEFAULT FALSE',
            'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }

        # Verificar cada columna individualmente
        for column_name, column_type in columns_to_add.items():
            try:
                # VALIDACIÓN ESTRICTA: Solo permitir columnas de la lista blanca
                if column_name not in ALLOWED_ROLE_COLUMNS:
                    raise ValueError(f"Nombre de columna no permitido para roles: {column_name}")
                if column_type not in ALLOWED_ROLE_TYPES:
                    raise ValueError(f"Tipo de columna no permitido para roles: {column_type}")

                # Verificar si la columna existe usando parámetros preparados
                result = connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='roles' AND column_name=:col_name"
                ), {"col_name": column_name})
                rows = result.fetchall()

                if len(rows) == 0:
                    # Añadir la columna si no existe
                    # Seguro porque column_name y column_type están validados contra lista blanca
                    connection.execute(text(
                        f"ALTER TABLE roles ADD COLUMN {column_name} {column_type}"
                    ))
                    connection.commit()
                    print(f"Columna {column_name} añadida correctamente a la tabla roles.")
                else:
                    print(f"La columna {column_name} ya existe en la tabla roles.")
            except Exception as e:
                print(f"Error al verificar o añadir la columna {column_name} a roles: {e}")
        
        print("Verificación y actualización de columnas de roles completada.")
        
    except Exception as e:
        print(f"Error al actualizar la tabla roles: {e}")
    finally:
        connection.close()

def init_db():
    """Inicializa la base de datos"""
    Base.metadata.create_all(engine)
    
    # Actualizar las tablas si es necesario
    update_servers_table()
    update_account_table()
    update_roles_table()
    
    session = Session()
    
    # Inicializar roles
    Role.initialize_default_roles(session)
    
    # Inicializar SUPER_ADMINS si no existen
    for super_admin_id in SUPER_ADMIN_IDS:
        super_admin = session.query(User).filter_by(telegram_id=super_admin_id).first()
        if not super_admin:
            super_admin = User(
                telegram_id=super_admin_id,
                username="SuperAdmin",
                full_name="Super Admin",
                role="SUPER_ADMIN",
                credits=float('inf'),
                is_authorized=True
            )
            session.add(super_admin)
        else:
            # Asegurar que tengan el rol correcto si ya existen
            if super_admin.role != "SUPER_ADMIN":
                super_admin.role = "SUPER_ADMIN"
                super_admin.is_authorized = True
    
    # Inicializar precios
    Price.initialize_default_prices(session)
    
    session.commit()
    session.close()

def get_role_by_name(session, role_name):
    """Obtiene un rol por su nombre"""
    return session.query(Role).filter_by(name=role_name).first()

def is_admin_role(session, role_name):
    """Verifica si un rol tiene privilegios de administrador"""
    role = get_role_by_name(session, role_name)
    return role and role.is_admin

def get_available_role_names(session, include_admin=False):
    """Obtiene los nombres de los roles disponibles"""
    roles = Role.get_available_roles(session, include_admin)
    return [role.name for role in roles]

if __name__ == "__main__":
    init_db()
