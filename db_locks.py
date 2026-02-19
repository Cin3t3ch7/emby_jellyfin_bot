"""
Sistema de locks para prevenir race conditions en operaciones críticas de la base de datos.
"""
import threading
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Locks globales para operaciones críticas
_server_update_locks = {}
_lock_creation_lock = threading.Lock()


def get_server_lock(server_id):
    """
    Obtiene un lock para un servidor específico.
    Esto previene race conditions al actualizar contadores de usuarios y dispositivos.

    Args:
        server_id: ID del servidor

    Returns:
        threading.Lock: Lock para el servidor
    """
    with _lock_creation_lock:
        if server_id not in _server_update_locks:
            _server_update_locks[server_id] = threading.Lock()
        return _server_update_locks[server_id]


@contextmanager
def atomic_server_update(server_id):
    """
    Context manager para garantizar actualizaciones atómicas de servidores.

    Usage:
        with atomic_server_update(server.id):
            server.current_users += 1
            session.commit()
    """
    lock = get_server_lock(server_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


# Lock global para operaciones de dispositivos
_device_cleanup_lock = threading.Lock()


@contextmanager
def atomic_device_cleanup():
    """
    Context manager para garantizar que solo una tarea de limpieza de dispositivos
    se ejecute a la vez. Esto previene que múltiples procesos intenten eliminar
    los mismos dispositivos simultáneamente.
    """
    _device_cleanup_lock.acquire()
    try:
        yield
    finally:
        _device_cleanup_lock.release()
