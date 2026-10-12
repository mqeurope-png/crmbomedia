"""Datos de CLIENTE para la cabecera de un presupuesto de FACTUSOL (`F_PRE`) a
partir de la empresa CRM vinculada. Lo comparten el alta de proformas
(`POST /api/erp/factusol/quotes`) y la conversión de una OFERTA en
presupuesto: un solo sitio para el país real, el régimen de IVA por pareja
emisor → cliente (#528) y qué series dejan decidir a la ficha F_CLI.

Es PURO: no habla con FACTUSOL. El llamador comprueba antes que la empresa
existe y está vinculada (`factusol_company_id`); sin CODCLI el presupuesto no
tendría dueño en la contabilidad.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def customer_from_company(
    session: Session, company: Any, *, serie: Any = None,
) -> dict[str, Any]:
    """`{codcli, nombre, nif, direccion, ciudad, cp, provincia, pais, vat,
    regime, regime_por_serie, ficha_decide_por_serie}` de la empresa CRM.

    `regime` es el de la `serie` pedida (la empresa que emite), y
    `regime_por_serie` lleva el de TODAS las series: al editar una proforma la
    serie definitiva es la de la fila que ya existe y la resuelve el worker."""
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        issuer_iso2_for_serie,
        issuer_iso2_por_serie,
    )
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.vat_regime import (  # noqa: PLC0415
        pareja_elegible,
        regime_for,
        regimes_por_serie,
    )
    from app.services.vies import company_vies_valid  # noqa: PLC0415

    # Tarea C: país REAL (antes CPAPRE salía siempre 724) y régimen de IVA por
    # la PAREJA país del emisor (la serie) → país del cliente: las proformas de
    # un intracomunitario / exportación se calculan SIN IVA (`quotes._totals`).
    country = normalize_country(company.country) if company.country else None
    vies_valid = company_vies_valid(company)
    return {
        "codcli": str(company.factusol_company_id),
        "nombre": company.name,
        "nif": company.tax_id or "",
        "direccion": company.address_line or "",
        "ciudad": company.city or "",
        "cp": company.postal_code or "",
        "provincia": company.state or "",
        "pais": country or "",
        "vat": company.vat or "",
        "regime": regime_for(
            country,
            issuer_iso2=issuer_iso2_for_serie(session, serie) if serie is not None else None,
            vat=company.vat, nif=company.tax_id, vies_valid=vies_valid,
        ),
        "regime_por_serie": regimes_por_serie(
            issuer_iso2_por_serie(session), country, vat=company.vat,
            nif=company.tax_id, vies_valid=vies_valid,
        ),
        # Series en las que la pareja permite ELEGIR (los dos países en la UE
        # y distintos): ahí manda la ficha F_CLI y el job la lee. Se calcula
        # aquí porque es pura (no llama a FACTUSOL) y el job solo consulta la
        # ficha cuando de verdad puede cambiar algo.
        "ficha_decide_por_serie": {
            serie: pareja_elegible(country, issuer_iso2=pais)
            for serie, pais in issuer_iso2_por_serie(session).items()
        },
    }
