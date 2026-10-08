"""Auditoría de IVA por pareja emisor → cliente. **SOLO LECTURA.**

BoHub factura desde dos países: Streamtec / Bomedia (España, series 5 y 1) y
MQ EUROPE BV (Bélgica, serie 2). El régimen de IVA no es del cliente, es de la
PAREJA (ver `vat_regime`), y hasta ahora el código daba España por supuesta:
todo lo que ha salido por la serie 2 se calculó como si lo emitiera una empresa
española. Este módulo busca las consecuencias:

1. **Fichas**: clientes a los que factura un emisor NO español cuya ficha F_CLI
   (`IFICLI`/`IVACLI`/`TIVCLI`) no cuadra con el régimen de esa pareja. Son las
   fichas que hay que repasar en FACTUSOL antes de la siguiente factura.
2. **Documentos de esa serie**: proformas, albaranes y facturas cuyo IVA no
   cuadra con el régimen de la pareja — con IVA donde debía ir exento o al
   revés. Las **proformas van aparte**: son revisables y se vuelven a emitir;
   un albarán o una factura ya han salido y lo que se haga con ellos (factura
   rectificativa, abono) lo decide administración.

No escribe NADA: ni F_CLI, ni documentos, ni la base de BoHub. El informe se
entrega y se para.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.language import normalize_country
from app.integrations.factusol.client import FactusolClient
from app.integrations.factusol.documents import DOC_SPECS
from app.integrations.factusol.vat_regime import (
    DEFAULT_ISSUER_ISO2,
    REGIME_LABELS,
    REGIME_NACIONAL,
    country_display,
    regime_for,
    regime_from_fcli_row,
    regime_reason,
)

logger = logging.getLogger(__name__)

#: Reexportado para el script: el país del emisor por defecto (España). Las
#: series que emite una empresa española no hace falta auditarlas: la pareja
#: que calculaba el código viejo era exactamente esa.
__all__ = [
    "DEFAULT_ISSUER_ISO2", "DOC_LABELS", "DOC_TYPES", "EMITIDOS", "Emisor",
    "auditar", "base_de_cabecera", "informe_texto", "iva_de_cabecera",
    "revisar_documento", "revisar_ficha",
]

#: Los documentos que se revisan. Los pedidos de cliente (F_PCL) quedan fuera:
#: los crea la app Woo→FACTUSOL con los importes que pagó el cliente y no son
#: de la serie 2.
DOC_TYPES: tuple[str, ...] = ("presupuestos", "albaranes", "facturas")
#: Los que ya han salido: ahí no se toca nada sin administración.
EMITIDOS: tuple[str, ...] = ("albaranes", "facturas")
DOC_LABELS: dict[str, str] = {
    "presupuestos": "Proforma", "albaranes": "Albarán", "facturas": "Factura",
}


@dataclass(frozen=True)
class Emisor:
    """Empresa que factura una serie: la otra punta de la pareja."""

    serie: int
    nombre: str
    pais_iso2: str | None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def iva_de_cabecera(row: dict[str, Any], suffix: str) -> float:
    """El IVA de una cabecera de documento: el mayor de las bandas (porcentaje
    de la banda 1 o importe de cualquiera). > 0 significa «lleva IVA»."""
    return max(
        _num(row.get(f"PIVA1{suffix}")), _num(row.get(f"IIVA1{suffix}")),
        _num(row.get(f"IIVA2{suffix}")), _num(row.get(f"IIVA3{suffix}")),
    )


def base_de_cabecera(row: dict[str, Any], suffix: str) -> float:
    """Base imponible de la banda 1, o el total si no está. Sirve para no
    marcar documentos a cero (una proforma vacía no descuadra nada)."""
    base = max(_num(row.get(f"BAS1{suffix}")), _num(row.get(f"NET1{suffix}")))
    return base or _num(row.get(f"TOT{suffix}"))


def revisar_documento(
    *, tipo: str, row: dict[str, Any], esperado: str | None,
) -> dict[str, Any] | None:
    """El hallazgo de un documento, o None si cuadra (o no se puede decidir).

    Cuadrar es: régimen nacional ⇒ lleva IVA; intracomunitario o exportación
    ⇒ no lo lleva. No se comprueba el TIPO de IVA (que el 21 % sea 21 y no
    10): eso es de las líneas y no es lo que rompió la pareja."""
    spec = DOC_SPECS[tipo]
    if esperado is None:
        return None
    base = base_de_cabecera(row, spec.suffix)
    if base <= 0:
        return None
    iva = iva_de_cabecera(row, spec.suffix)
    lleva_iva = iva > 0
    debe_llevar = esperado == REGIME_NACIONAL
    if lleva_iva == debe_llevar:
        return None
    return {
        "tipo": tipo,
        "tipo_label": DOC_LABELS[tipo],
        "emitido": tipo in EMITIDOS,
        "numero": f"{row.get(spec.tip)}-{row.get(spec.cod)}",
        "fecha": str(row.get(spec.fec) or ""),
        "codcli": str(row.get(spec.cli) or ""),
        "cliente": str(row.get(spec.cno) or ""),
        "base": round(base, 2),
        "iva": round(iva, 2),
        "total": round(_num(row.get(spec.tot)), 2),
        "regimen_esperado": esperado,
        "problema": (
            f"lleva IVA ({iva:g}) y la pareja da "
            f"{REGIME_LABELS[esperado]}: debía salir sin IVA"
            if lleva_iva else
            f"sale sin IVA y la pareja da {REGIME_LABELS[esperado]}: "
            "debía llevarlo"
        ),
    }


def revisar_ficha(
    *, row: dict[str, Any], emisor: Emisor, iso2: str | None,
    fuente_pais: str, vat: Any = None, vies_valid: bool | None = None,
) -> dict[str, Any] | None:
    """El hallazgo de una ficha F_CLI frente a la pareja, o None si cuadra.

    `iso2` es el país del cliente y `fuente_pais` de dónde sale (el CRM o el
    `PAICLI` de la propia ficha). Sin país no se decide nada: se devuelve el
    hallazgo «sin país», que es un problema en sí."""
    codcli = str(row.get("CODCLI") or "")
    nombre = str(row.get("NOFCLI") or row.get("NOCCLI") or "")
    actual = regime_from_fcli_row(row)
    base = {
        "codcli": codcli, "cliente": nombre, "serie": emisor.serie,
        "emisor": emisor.nombre, "emisor_pais": emisor.pais_iso2,
        "pais_iso2": iso2, "fuente_pais": fuente_pais,
        "nif": str(row.get("NIFCLI") or ""),
        "regimen_ficha": actual,
        "regimen_ficha_label": REGIME_LABELS.get(actual) if actual else None,
    }
    if iso2 is None:
        return {**base, "regimen_esperado": None, "regimen_esperado_label": None,
                "motivo": "sin país ni en el CRM ni en la ficha (PAICLI)",
                "problema": "no se puede decidir el régimen sin país"}
    esperado = regime_for(
        iso2, issuer_iso2=emisor.pais_iso2, vat=vat, nif=row.get("NIFCLI"),
        vies_valid=vies_valid,
    )
    motivo = regime_reason(
        iso2, issuer_iso2=emisor.pais_iso2, vat=vat, nif=row.get("NIFCLI"),
        vies_valid=vies_valid,
    )
    if actual == esperado:
        return None
    return {
        **base, "regimen_esperado": esperado,
        "regimen_esperado_label": REGIME_LABELS[esperado],
        "motivo": motivo,
        "problema": (
            f"la ficha está como "
            f"{REGIME_LABELS.get(actual) if actual else 'régimen desconocido'} "
            f"y {emisor.nombre} ({country_display(emisor.pais_iso2)}) le "
            f"factura {REGIME_LABELS[esperado]}"
        ),
    }


def _companies_por_codcli(session: Session, codclis: set[str]) -> dict[str, Any]:
    """`{codcli: Company}` de las empresas del CRM vinculadas a esos clientes.
    El país del CRM manda sobre el `PAICLI` de la ficha: es el que mantienen
    los comerciales y el que usa el cálculo de los documentos."""
    from app.models.crm import Company  # noqa: PLC0415

    if not codclis:
        return {}
    rows = session.scalars(
        select(Company).where(Company.factusol_company_id.in_(sorted(codclis)))
    ).all()
    return {str(c.factusol_company_id): c for c in rows}


def _contexto_cliente(
    row: dict[str, Any], company: Any,
) -> tuple[str | None, str, Any, bool | None]:
    """`(iso2, fuente, vat, vies_valid)` del cliente: el país del CRM si la
    empresa está vinculada y lo tiene, y si no el `PAICLI` de la ficha."""
    from app.services.vies import company_vies_valid  # noqa: PLC0415

    if company is not None and company.country:
        return (normalize_country(company.country), "CRM", company.vat,
                company_vies_valid(company))
    paicli = str(row.get("PAICLI") or "").strip()
    return (normalize_country(paicli) if paicli else None, "ficha F_CLI",
            None, None)


def _rows_de_serie(
    client: FactusolClient, tipo: str, *, ejercicio: str, serie: int,
) -> list[dict[str, Any]]:
    """Cabeceras de ESA serie. El filtro de serie se aplica en Python (como en
    `documents`): `TIP*` llega como texto o número según la tabla, y un filtro
    SQL que no casa devolvería [] en silencio (gotcha nº 1)."""
    spec = DOC_SPECS[tipo]
    rows = client.load_table(spec.table, filtro="1=1", ejercicio=ejercicio)
    return [r for r in rows
            if str(r.get(spec.tip) or "").strip().lstrip("0") == str(serie)]


def auditar(
    session: Session, client: FactusolClient, *, ejercicio: str, emisor: Emisor,
) -> dict[str, Any]:
    """Recorre los documentos de la serie del `emisor` y las fichas de sus
    clientes. **No escribe nada.**

    Devuelve `{emisor, documentos, fichas, resumen}` con los hallazgos ya
    separados entre proformas y documentos emitidos."""
    documentos: list[dict[str, Any]] = []
    revisados: dict[str, int] = {}
    codclis: set[str] = set()
    cache_fichas: dict[str, dict[str, Any]] = {}

    filas: dict[str, list[dict[str, Any]]] = {}
    for tipo in DOC_TYPES:
        filas[tipo] = _rows_de_serie(client, tipo, ejercicio=ejercicio,
                                     serie=emisor.serie)
        revisados[tipo] = len(filas[tipo])
        codclis.update(
            str(r.get(DOC_SPECS[tipo].cli) or "").strip()
            for r in filas[tipo] if str(r.get(DOC_SPECS[tipo].cli) or "").strip()
        )

    companies = _companies_por_codcli(session, codclis)
    # Una lectura de F_CLI por cliente (la API de DELSOL no es para apretarla:
    # son `CargaTabla` de una fila cada una, y el informe es manual).
    contextos: dict[str, tuple[str | None, str, Any, bool | None]] = {}
    for codcli in sorted(codclis, key=lambda c: (len(c), c)):
        ficha = _ficha(client, codcli, ejercicio=ejercicio)
        if ficha is None:
            continue
        cache_fichas[codcli] = ficha
        contextos[codcli] = _contexto_cliente(ficha, companies.get(codcli))

    esperados: dict[str, str | None] = {
        codcli: (
            regime_for(iso2, issuer_iso2=emisor.pais_iso2, vat=vat,
                       nif=cache_fichas[codcli].get("NIFCLI"), vies_valid=vies)
            if iso2 else None
        )
        for codcli, (iso2, _fuente, vat, vies) in contextos.items()
    }

    for tipo in DOC_TYPES:
        for row in filas[tipo]:
            codcli = str(row.get(DOC_SPECS[tipo].cli) or "").strip()
            hallazgo = revisar_documento(
                tipo=tipo, row=row, esperado=esperados.get(codcli),
            )
            if hallazgo is not None:
                documentos.append(hallazgo)

    fichas: list[dict[str, Any]] = []
    for codcli, ficha in cache_fichas.items():
        iso2, fuente, vat, vies = contextos[codcli]
        hallazgo = revisar_ficha(
            row=ficha, emisor=emisor, iso2=iso2, fuente_pais=fuente, vat=vat,
            vies_valid=vies,
        )
        if hallazgo is not None:
            fichas.append(hallazgo)

    proformas = [d for d in documentos if not d["emitido"]]
    emitidos = [d for d in documentos if d["emitido"]]
    return {
        "emisor": emisor, "ejercicio": ejercicio,
        "documentos": documentos, "proformas": proformas, "emitidos": emitidos,
        "fichas": fichas,
        "resumen": {
            "revisados": revisados, "clientes": len(cache_fichas),
            "clientes_sin_ficha": len(codclis) - len(cache_fichas),
            "fichas_a_repasar": len(fichas),
            "proformas": len(proformas), "emitidos": len(emitidos),
        },
    }


def _ficha(
    client: FactusolClient, codcli: str, *, ejercicio: str,
) -> dict[str, Any] | None:
    """La fila REAL de F_CLI de ese cliente, o None (cliente borrado)."""
    from app.integrations.factusol.customers import customer_row  # noqa: PLC0415

    try:
        return customer_row(client, codcli, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — una ficha ilegible no para el informe
        logger.warning("auditoría IVA: no se pudo leer el cliente %s: %s", codcli, exc)
        return None


def informe_texto(resultado: dict[str, Any], *, detalle: int = 40) -> str:
    """El informe para leer en consola. Solo texto: no decide nada."""
    emisor: Emisor = resultado["emisor"]
    r = resultado["resumen"]
    lineas = [
        f"Auditoría de IVA · serie {emisor.serie} · {emisor.nombre} "
        f"({country_display(emisor.pais_iso2)}) · ejercicio {resultado['ejercicio']}",
        "SOLO LECTURA: no se ha tocado ninguna ficha ni ningún documento.",
        "",
        "Documentos revisados: " + " · ".join(
            f"{DOC_LABELS[t]}s {n}" for t, n in r["revisados"].items()
        ),
        f"Clientes de la serie: {r['clientes']}"
        + (f" ({r['clientes_sin_ficha']} sin ficha legible)"
           if r["clientes_sin_ficha"] else ""),
        "",
        f"1) Fichas F_CLI a repasar: {r['fichas_a_repasar']}",
    ]
    for f in resultado["fichas"][:detalle]:
        lineas.append(
            f"   · {f['codcli']} {f['cliente'][:45]} [{f['pais_iso2'] or '—'}"
            f" según {f['fuente_pais']}] → {f['problema']}"
        )
    if len(resultado["fichas"]) > detalle:
        lineas.append(f"   … y {len(resultado['fichas']) - detalle} más (ver CSV)")
    lineas += [
        "",
        f"2) Proformas con el IVA descuadrado: {r['proformas']} "
        "(revisables: se vuelven a emitir)",
    ]
    for d in resultado["proformas"][:detalle]:
        lineas.append(f"   · {d['numero']} {d['fecha']} {d['cliente'][:40]} "
                      f"({d['total']:.2f} €) → {d['problema']}")
    if r["proformas"] > detalle:
        lineas.append(f"   … y {r['proformas'] - detalle} más (ver CSV)")
    lineas += [
        "",
        f"3) Albaranes y facturas YA EMITIDOS con el IVA descuadrado: "
        f"{r['emitidos']}",
    ]
    for d in resultado["emitidos"][:detalle]:
        lineas.append(f"   · {d['tipo_label']} {d['numero']} {d['fecha']} "
                      f"{d['cliente'][:40]} ({d['total']:.2f} €) → {d['problema']}")
    if r["emitidos"] > detalle:
        lineas.append(f"   … y {r['emitidos'] - detalle} más (ver CSV)")
    lineas += [
        "",
        "Qué hacer con esto: las fichas se corrigen en FACTUSOL (o desde la "
        "empresa, «Régimen de IVA en FACTUSOL»); las proformas se vuelven a "
        "emitir; lo ya emitido (rectificativa o abono) lo decide "
        "administración. BoHub no corrige nada por su cuenta.",
    ]
    return "\n".join(lineas)
