from fastapi import Request
from slowapi.util import get_remote_address


def client_ip(request: Request) -> str:
    """IP real del cliente, para el límite de intentos (slowapi).

    En Cloud Run la conexión llega desde el proxy de Google, así que `request.client.host` es el
    mismo para todos los usuarios y el límite se compartía entre todos (p. ej. 10 inicios de sesión
    por minuto para toda la clase). La IP real es la ÚLTIMA de X-Forwarded-For: la agrega Google;
    las anteriores las puede enviar el propio cliente para falsificar su origen."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        ip = forwarded.split(",")[-1].strip()
        if ip:
            return ip
    return get_remote_address(request)
