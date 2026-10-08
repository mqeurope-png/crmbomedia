"""Textos de la interfaz de un formulario, en su idioma.

Un formulario alemán enseñaba «Enviar» en el botón y «No se pudo enviar»
al fallar: los textos estaban fijos en castellano dentro del JS embebido.
Aquí están todos los que ve quien rellena el formulario, por idioma; los
que faltan en un idioma caen al castellano.

Los mensajes de validación del navegador (campo obligatorio, correo mal
escrito) los pone el propio navegador en SU idioma, no se pueden traducir
desde aquí; los nuestros —el botón, el acuse, los errores de envío— sí.
"""

from __future__ import annotations

IDIOMA_BASE = "es"

#: Clave → texto, por idioma. Las claves `falta_*` son las que devuelve el
#: servidor al rechazar un envío (`spam_reason`), para poder decir qué pasó.
TEXTOS: dict[str, dict[str, str]] = {
    "es": {
        "submit": "Enviar",
        "enviando": "Enviando…",
        "gracias": "¡Gracias! Hemos recibido tu solicitud.",
        "error_datos": "No se pudo enviar. Revisa los datos e inténtalo de nuevo.",
        "error_conexion": "Error de conexión. Inténtalo de nuevo.",
        "no_cargado": "No se pudo cargar el formulario.",
        "obligatorio": "Campo obligatorio",
        "missing_required": "Faltan campos obligatorios.",
        "invalid_email": "El correo electrónico no parece válido.",
        "rate_limit": "Demasiados envíos desde esta conexión. Inténtalo más tarde.",
        "elige": "Elige una opción",
    },
    "en": {
        "submit": "Send",
        "enviando": "Sending…",
        "gracias": "Thank you! We have received your request.",
        "error_datos": "Could not send. Please check the details and try again.",
        "error_conexion": "Connection error. Please try again.",
        "no_cargado": "The form could not be loaded.",
        "obligatorio": "Required field",
        "missing_required": "Some required fields are missing.",
        "invalid_email": "That email address does not look valid.",
        "rate_limit": "Too many submissions from this connection. Please try later.",
        "elige": "Choose an option",
    },
    "fr": {
        "submit": "Envoyer",
        "enviando": "Envoi…",
        "gracias": "Merci ! Nous avons bien reçu votre demande.",
        "error_datos": "Envoi impossible. Vérifiez les informations et réessayez.",
        "error_conexion": "Erreur de connexion. Veuillez réessayer.",
        "no_cargado": "Le formulaire n'a pas pu être chargé.",
        "obligatorio": "Champ obligatoire",
        "missing_required": "Des champs obligatoires sont manquants.",
        "invalid_email": "Cette adresse e-mail ne semble pas valide.",
        "rate_limit": "Trop d'envois depuis cette connexion. Réessayez plus tard.",
        "elige": "Choisissez une option",
    },
    "de": {
        "submit": "Senden",
        "enviando": "Wird gesendet…",
        "gracias": "Vielen Dank! Wir haben Ihre Anfrage erhalten.",
        "error_datos": "Senden fehlgeschlagen. Bitte prüfen Sie die Angaben.",
        "error_conexion": "Verbindungsfehler. Bitte versuchen Sie es erneut.",
        "no_cargado": "Das Formular konnte nicht geladen werden.",
        "obligatorio": "Pflichtfeld",
        "missing_required": "Es fehlen Pflichtfelder.",
        "invalid_email": "Diese E-Mail-Adresse sieht nicht richtig aus.",
        "rate_limit": "Zu viele Sendungen von dieser Verbindung. Später erneut versuchen.",
        "elige": "Bitte wählen",
    },
    "pt": {
        "submit": "Enviar",
        "enviando": "A enviar…",
        "gracias": "Obrigado! Recebemos o seu pedido.",
        "error_datos": "Não foi possível enviar. Verifique os dados e tente de novo.",
        "error_conexion": "Erro de ligação. Tente novamente.",
        "no_cargado": "Não foi possível carregar o formulário.",
        "obligatorio": "Campo obrigatório",
        "missing_required": "Faltam campos obrigatórios.",
        "invalid_email": "O email não parece válido.",
        "rate_limit": "Demasiados envios desta ligação. Tente mais tarde.",
        "elige": "Escolha uma opção",
    },
    "nl": {
        "submit": "Versturen",
        "enviando": "Versturen…",
        "gracias": "Bedankt! We hebben je aanvraag ontvangen.",
        "error_datos": "Verzenden mislukt. Controleer de gegevens en probeer het opnieuw.",
        "error_conexion": "Verbindingsfout. Probeer het opnieuw.",
        "no_cargado": "Het formulier kon niet worden geladen.",
        "obligatorio": "Verplicht veld",
        "missing_required": "Er ontbreken verplichte velden.",
        "invalid_email": "Dit e-mailadres lijkt niet geldig.",
        "rate_limit": "Te veel inzendingen vanaf deze verbinding. Probeer het later.",
        "elige": "Kies een optie",
    },
}

#: Nombre del idioma en castellano, para la ficha y los avisos internos.
IDIOMAS: dict[str, str] = {
    "es": "español", "en": "inglés", "fr": "francés", "de": "alemán",
    "pt": "portugués", "nl": "neerlandés", "it": "italiano",
}


def normalizar_idioma(idioma: str | None) -> str:
    """`de-DE`, `DE`, `de_AT` → `de`. Vacío o raro → el idioma base."""
    codigo = (idioma or "").strip().lower().replace("_", "-").split("-")[0]
    return codigo if codigo in TEXTOS else IDIOMA_BASE


def textos(idioma: str | None) -> dict[str, str]:
    """Todos los textos en ese idioma, completando con el castellano lo que
    falte (un idioma nuevo nunca deja un hueco en blanco)."""
    return {**TEXTOS[IDIOMA_BASE], **TEXTOS.get(normalizar_idioma(idioma), {})}


def nombre_idioma(idioma: str | None) -> str:
    codigo = (idioma or "").strip().lower().split("-")[0]
    return IDIOMAS.get(codigo, codigo or "—")


def texto_submit(idioma: str | None, propio: str | None = None) -> str:
    """El texto del botón: el del formulario si lo tiene, y si no el de su
    idioma (los 24 formularios creados no lo tienen puesto)."""
    propio = (propio or "").strip()
    return propio or textos(idioma)["submit"]
