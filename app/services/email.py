import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


async def _send(to_email: str, to_name: str, subject: str, html: str) -> bool:
    """Envía un correo transaccional vía la API de Brevo. Nunca lanza: si algo falla
    (o Brevo no está configurado), registra el problema y devuelve False para no
    romper el flujo de registro / recuperación."""
    if not settings.brevo_api_key or not settings.brevo_sender_email:
        logger.warning("Brevo no configurado (falta API key o remitente); se omite el envío de correo")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                BREVO_URL,
                headers={"api-key": settings.brevo_api_key, "content-type": "application/json", "accept": "application/json"},
                json={
                    "sender": {"name": settings.brevo_sender_name, "email": settings.brevo_sender_email},
                    "to": [{"email": to_email, "name": to_name or to_email}],
                    "subject": subject,
                    "htmlContent": html,
                },
            )
        if resp.status_code >= 300:
            logger.error(f"Brevo respondió {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Error enviando correo con Brevo: {e}")
        return False


def _layout(titulo: str, cuerpo: str, boton_texto: str, boton_url: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;color:#1a1a1a">
      <h2 style="color:#0070C0">JurisMap</h2>
      <h3>{titulo}</h3>
      <p style="font-size:15px;line-height:1.5">{cuerpo}</p>
      <p style="text-align:center;margin:28px 0">
        <a href="{boton_url}" style="background:#0070C0;color:#fff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600">{boton_texto}</a>
      </p>
      <p style="font-size:12px;color:#666">Si no solicitaste esto, ignora este correo. También puedes copiar y pegar este enlace:<br>{boton_url}</p>
    </div>"""


async def send_verification_email(to_email: str, to_name: str, token: str) -> bool:
    url = f"{settings.frontend_url}/verify-email?token={token}"
    html = _layout(
        "Verifica tu cuenta",
        f"Hola {to_name or ''}, gracias por registrarte en JurisMap. Haz clic en el botón para activar tu cuenta.",
        "Verificar mi cuenta", url,
    )
    return await _send(to_email, to_name, "Verifica tu cuenta en JurisMap", html)


async def send_password_reset_email(to_email: str, to_name: str, token: str) -> bool:
    url = f"{settings.frontend_url}/reset-password?token={token}"
    html = _layout(
        "Restablecer contraseña",
        "Recibimos una solicitud para restablecer tu contraseña. El enlace expira en 1 hora.",
        "Restablecer contraseña", url,
    )
    return await _send(to_email, to_name, "Restablece tu contraseña de JurisMap", html)
