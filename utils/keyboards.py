from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database import Role, Session, User, check_demo_limit

def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🎬 Emby", callback_data="emby_menu"),
            InlineKeyboardButton("🍿 Jellyfin", callback_data="jellyfin_menu")
        ],
        [
            InlineKeyboardButton("👤 Cuentas creadas", callback_data="my_accounts"),
            InlineKeyboardButton("💰 Precios", callback_data="prices")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def service_menu_keyboard(service, role="DISTRIBUTOR"):
    """Crea un teclado basado en el rol del usuario"""
    keyboard = []
    
    # Opciones básicas para todos los usuarios
    keyboard.append([
        InlineKeyboardButton("✅ Crear nuevo usuario", callback_data=f"{service}_create_user"),
        InlineKeyboardButton("❌ Eliminar usuario", callback_data=f"{service}_delete_user")
    ])
    keyboard.append([
        InlineKeyboardButton("🔄 Renovar usuario", callback_data=f"{service}_renew_user")
    ])
    
    # Opciones adicionales para usuarios admin
    if role in ["SUPER_ADMIN", "ADMIN"]:
        keyboard.append([
            InlineKeyboardButton("⚙️ Gestionar servidores", callback_data=f"{service}_manage_servers")
        ])
        keyboard.append([
            InlineKeyboardButton("📊 Estado de servidores", callback_data=f"{service}_server_status")
        ])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Volver al menú principal", callback_data="main_menu")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def back_to_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔙 Volver al menú principal", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_account_keyboard(service, role="DISTRIBUTOR", user_telegram_id=None):
    """Crea un teclado para opciones de creación de cuenta"""
    keyboard = []
    
    # Verificar límite de demos si se proporciona el ID del usuario
    demo_available = True
    demo_info = ""
    
    if user_telegram_id:
        try:
            session = Session()
            db_user = session.query(User).filter_by(telegram_id=user_telegram_id).first()
            if db_user:
                can_create, current_count, limit = check_demo_limit(db_user.id, session)
                demo_available = can_create
                demo_info = f" ({current_count}/{limit})"
            session.close()
        except Exception:
            # En caso de error, permitir demos por defecto
            demo_available = True
    
    if service == "emby":
        keyboard = [
            [
                InlineKeyboardButton("💻 Cuenta Completa (2 pantallas)", callback_data=f"{service}_create_2_screens")
            ],
            [
                InlineKeyboardButton("👤 Perfil (1 pantalla)", callback_data=f"{service}_create_1_screen")
            ],
            [
                InlineKeyboardButton("📺 TV en vivo (1 pantalla)", callback_data=f"{service}_create_live_tv")
            ]
        ]
        
        # Agregar botón de demo con información de límite
        if demo_available:
            keyboard.append([
                InlineKeyboardButton(f"⏱️ Demo (1 hora){demo_info}", callback_data=f"{service}_create_demo")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(f"⏱️ Demo - Límite alcanzado{demo_info}", callback_data="demo_limit_reached")
            ])
        
        keyboard.append([
            InlineKeyboardButton("🛒 Compra masiva (Max. 3)", callback_data=f"{service}_create_bulk")
        ])
            
    elif service == "jellyfin":
        keyboard = [
            [
                InlineKeyboardButton("💻 Cuenta completa (3 pantallas)", callback_data=f"{service}_create_3_screens")
            ],
            [
                InlineKeyboardButton("👤 Perfil (1 pantalla)", callback_data=f"{service}_create_1_screen")
            ],
            [
                InlineKeyboardButton("📺 TV en vivo (1 pantalla)", callback_data=f"{service}_create_live_tv")
            ]
        ]
        
        # Agregar botón de demo con información de límite
        if demo_available:
            keyboard.append([
                InlineKeyboardButton(f"⏱️ Demo (1 hora){demo_info}", callback_data=f"{service}_create_demo")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(f"⏱️ Demo - Límite alcanzado{demo_info}", callback_data="demo_limit_reached")
            ])
        
        keyboard.append([
            InlineKeyboardButton("🛒 Compra masiva (Max. 5)", callback_data=f"{service}_create_bulk")
        ])
        
        # Add special TV button for eligible roles
        if role in ["SUPER_ADMIN", "ADMIN", "SUPERRESELLER"]:
            keyboard.insert(1, [
                InlineKeyboardButton("📺 TV Completa (3 pantallas)", callback_data=f"{service}_create_3_screens_tv")
            ])
    
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data=f"{service}_menu")])
    return InlineKeyboardMarkup(keyboard)
    
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

def accounts_menu_keyboard():
    """Teclado para seleccionar tipo de cuenta"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 Cuentas Emby", callback_data="emby_accounts"),
            InlineKeyboardButton("🍿 Cuentas Jellyfin", callback_data="jellyfin_accounts")
        ],
        [
            InlineKeyboardButton("🔙 Volver al menú principal", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
