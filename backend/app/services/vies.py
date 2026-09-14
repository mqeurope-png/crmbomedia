"""VIES en la empresa del CRM (Fase VIES): cuándo validar, qué guardar y qué
significa el resultado para el régimen de IVA.

- Se valida cuando la empresa es de la UE (no España) y tiene NIF-IVA: al
  crear / editar la empresa (best-effort, timeout corto, nunca bloquea) y con
  «Revalidar en VIES» (fuerza, salta la caché).
- Se guarda en la empresa: `vies_status` (valido / no_valido / desconocido),
  `vies_checked_at`, `vies_vat` (el NIF-IVA que se validó), y el nombre /
  dirección que devuelve VIES.
- Lectura del resultado: `company_vies_valid(company)` → True confirma el
  régimen intracomunitario; False (VAT no válido) impide eximir → nacional
  con IVA (`regime_for(..., vies_valid=False)`); None = pendiente /
  desconocido → se sigue con la regla país + NIF-IVA sin bloquear.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.erp.language import normalize_country
from app.integrations.factusol.vat_regime import eu_vat_for
from app.integrations.vies.client import (
    VIES_DESCONOCIDO,
    VIES_NO_VALIDO,
    VIES_VALIDO,
    ViesResult,
    check_vat,
)
from app.models.crm import Company

logger = logging.getLogger(__name__)

#: Un resultado firme se da por bueno este tiempo; uno «desconocido» se
#: reintenta antes.
RECHECK_AFTER = timedelta(days=30)
RECHECK_UNKNOWN_AFTER = timedelta(hours=1)


def company_eu_vat(company: Any) -> str | None:
    """El NIF-IVA intracomunitario a validar, o None si la empresa no es de la
    UE (fuera de España) o no tiene NIF-IVA: VIES no aplica."""
    country = normalize_country(getattr(company, "country", None))
    if country is None or country == "ES":
        return None
    return eu_vat_for(
        country, vat=getattr(company, "vat", None), nif=getattr(company, "tax_id", None),
    )


def company_vies_valid(company: Any) -> bool | None:
    """Veredicto VIES aplicable a la empresa: True / False solo si el
    resultado guardado es firme Y corresponde al NIF-IVA actual; None si está
    pendiente, es desconocido, o el NIF-IVA cambió desde la validación."""
    vat = company_eu_vat(company)
    if not vat:
        return None
    if (getattr(company, "vies_vat", None) or "") != vat:
        return None
    status = getattr(company, "vies_status", None)
    if status == VIES_VALIDO:
        return True
    if status == VIES_NO_VALIDO:
        return False
    return None


def vies_state(company: Any) -> dict[str, Any]:
    """Bloque `vies` de la API: estado aplicable al NIF-IVA ACTUAL."""
    vat = company_eu_vat(company)
    checked_at = getattr(company, "vies_checked_at", None)
    status = getattr(company, "vies_status", None)
    stale = bool(vat) and (getattr(company, "vies_vat", None) or "") != vat
    if not vat:
        applies = False
        shown = None
    elif stale or not status:
        applies = True
        shown = "pendiente"
    else:
        applies = True
        shown = status
    return {
        "applies": applies,
        "vat": vat,
        "status": shown,
        "valid": company_vies_valid(company),
        "checked_at": checked_at.isoformat() if checked_at else None,
        "name": getattr(company, "vies_name", None) if not stale else None,
        "address": getattr(company, "vies_address", None) if not stale else None,
        "stale": stale,
    }


def needs_vies_check(company: Any, *, now: datetime | None = None) -> bool:
    """¿Toca (re)validar? Sin NIF-IVA UE no; sin resultado o con otro NIF-IVA
    sí; firme → cada 30 días; desconocido → cada hora."""
    vat = company_eu_vat(company)
    if not vat:
        return False
    stale = (getattr(company, "vies_vat", None) or "") != vat
    if stale or not getattr(company, "vies_status", None):
        return True
    checked = getattr(company, "vies_checked_at", None)
    if checked is None:
        return True
    now = now or datetime.now(UTC)
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    limit = RECHECK_UNKNOWN_AFTER if company.vies_status == VIES_DESCONOCIDO else RECHECK_AFTER
    return now - checked >= limit


def apply_result(company: Company, result: ViesResult) -> None:
    company.vies_status = result.status
    company.vies_checked_at = result.checked_at or datetime.now(UTC)
    company.vies_vat = result.vat or None
    company.vies_name = result.name
    company.vies_address = result.address


def vies_enabled() -> bool:
    return bool(getattr(get_settings(), "vies_enabled", True))


def check_vat_live(vat: str, *, force: bool = False) -> ViesResult | None:
    """Consulta VIES (con la caché en proceso del cliente salvo `force`) con
    la configuración de la app. None si VIES está desactivado. Nunca lanza:
    cualquier fallo es `desconocido`."""
    settings = get_settings()
    if not getattr(settings, "vies_enabled", True):
        return None
    try:
        return check_vat(
            vat, force=force, base_url=getattr(settings, "vies_base_url", None),
            timeout=getattr(settings, "vies_timeout_seconds", None),
        )
    except Exception as exc:  # noqa: BLE001 — el cliente ya no lanza; por si acaso
        logger.warning("vies: fallo inesperado validando %s: %s", vat, exc)
        return ViesResult(
            status=VIES_DESCONOCIDO, valid=None, vat=vat, country_code=vat[:2],
            number=vat[2:], error=str(exc)[:120], checked_at=datetime.now(UTC),
        )


def result_block(result: ViesResult | None, *, vat: str | None) -> dict[str, Any]:
    """Bloque `vies` de `fiscal-check` a partir de una consulta en vivo (sin
    empresa detrás): mismo formato que `vies_state`."""
    if result is None:
        return {
            "applies": bool(vat), "vat": vat, "status": "pendiente" if vat else None,
            "valid": None, "checked_at": None, "name": None, "address": None,
            "stale": False, "error": None,
        }
    return {
        "applies": True, "vat": result.vat, "status": result.status, "valid": result.valid,
        "checked_at": result.checked_at.isoformat() if result.checked_at else None,
        "name": result.name, "address": result.address, "stale": False,
        "error": result.error,
    }


def validate_company_vies(
    session: Session, company: Company, *, force: bool = False,
) -> dict[str, Any] | None:
    """Valida el NIF-IVA de la empresa en VIES y lo guarda en ella (sin
    commit: lo hace el caller). Devuelve el bloque `vies`, o None si no aplica
    (no UE / sin NIF-IVA) o VIES está desactivado. Nunca lanza."""
    _ = session  # la escritura la hace el caller con su commit
    vat = company_eu_vat(company)
    if not vat or not vies_enabled():
        return None
    if not force and not needs_vies_check(company):
        return vies_state(company)
    result = check_vat_live(vat, force=force)
    if result is None:
        return None
    apply_result(company, result)
    return vies_state(company)
