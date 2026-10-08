"""Auditoría de IVA por pareja emisor → cliente. **SOLO LECTURA.**

BoHub factura desde dos países: Streamtec / Bomedia (España, series 5 y 1) y
MQ EUROPE BV (Bélgica, serie 2). El régimen de IVA no es del cliente, es de la
PAREJA (ver `vat_regime`), y hasta ahora el código daba España por supuesta:
todo lo que ha salido por la serie 2 se calculó como si lo emitiera una empresa
española. Este módulo busca las consecuencias:

1. **Fichas**: clientes a los que factura un emisor NO español cuya ficha F_CLI
   (`IFICLI`/`IVACLI`/`TIVCLI`) no cuadra con el régimen de esa pareja. Son las
   fichas que hay que repasar en FACTUSOL antes de la siguiente factura, y la
   raíz de la mayoría de los documentos descuadrados (la ficha es lo que manda
   en la zona donde la pareja permite elegir).
2. **Documentos de esa serie**: proformas, albaranes y facturas cuyo IVA no
   cuadra con el régimen de la pareja — con IVA donde debía ir exento o al
   revés. Cada hallazgo dice si el documento **coincide con su ficha** (el
   caso «repasa la ficha», que es el normal) o si no coincide ni con ella (ahí
   pasó algo más). Las **proformas van aparte**: son revisables y se vuelven a
   emitir; un albarán o una factura ya han salido y lo que se haga con ellos
   (factura rectificativa, abono) lo decide administración.

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
from app.integrations.factusol.documents import DOC_SPECS, visible_number
from app.integrations.factusol.quotes import _num
from app.integrations.factusol.vat_regime import (
    DEFAULT_ISSUER_ISO2,
    REGIME_LABELS,
    REGIME_NACIONAL,
    country_display,
    eu_vat_for,
    regime_for,
    regime_from_fcli_row,
    regime_reason,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ISSUER_ISO2", "DOC_LABELS", "DOC_TYPES", "EMITIDOS", "Emisor",
    "auditar", "base_de_cabecera", "codcli_key", "informe_texto",
    "iva_de_cabecera", "revisar_documento", "revisar_ficha",
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


def codcli_key(value: Any) -> str:
    """Código de cliente normalizado para cruzar tablas: `'04471'`, `4471` y
    `'4471.0'` son el mismo cliente.

    Sin esto el cruce se hacía con la cadena cruda y un `CLIPRE` con ceros a
    la izquierda dejaba al cliente «sin ficha» y a todos sus documentos fuera
    del informe, que salía tranquilizadoramente vacío."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except (TypeError, ValueError):
        return text.upper()


def serie_key(value: Any) -> str:
    """Serie normalizada de una columna `TIP*`: `'02'`, `2` y `'2.0'` → `'2'`."""
    return codcli_key(value)


def iva_de_cabecera(row: dict[str, Any], suffix: str) -> float:
    """El IVA de una cabecera de documento: el mayor de las bandas (porcentaje
    de la banda 1 o importe de cualquiera). > 0 significa «lleva IVA»."""
    return max(
        _num(row.get(f"PIVA1{suffix}")), _num(row.get(f"IIVA1{suffix}")),
        _num(row.get(f"IIVA2{suffix}")), _num(row.get(f"IIVA3{suffix}")),
    )


def base_de_cabecera(row: dict[str, Any], suffix: str) -> float:
    """Base imponible de la banda 1, o el total si no está. Sirve para no
    marcar documentos a cero (una proforma vacía no descuadra nada). Puede ser
    negativa (abonos): se revisan igual, por el valor absoluto."""
    base = max(_num(row.get(f"BAS1{suffix}")), _num(row.get(f"NET1{suffix}")),
               key=abs)
    return base or _num(row.get(f"TOT{suffix}"))


def revisar_documento(
    *, tipo: str, row: dict[str, Any], esperado: str | None,
    regimen_ficha: str | None = None,
) -> dict[str, Any] | None:
    """El hallazgo de un documento, o None si cuadra (o no se puede decidir).

    Cuadrar es: régimen nacional ⇒ lleva IVA; intracomunitario o exportación
    ⇒ no lo lleva. `esperado` es el régimen de la PAREJA (sin la ficha), que es
    lo que se audita; `regimen_ficha` sirve para decir si el documento al menos
    coincide con la ficha del cliente —el caso «repasa la ficha»— o si no
    coincide ni con ella. No se comprueba el TIPO de IVA (que el 21 % sea 21 y
    no 10): eso es de las líneas y no es lo que rompió la pareja."""
    spec = DOC_SPECS[tipo]
    if esperado is None:
        return None
    base = base_de_cabecera(row, spec.suffix)
    if abs(base) <= 0:
        return None
    iva = iva_de_cabecera(row, spec.suffix)
    lleva_iva = iva > 0
    debe_llevar = esperado == REGIME_NACIONAL
    if lleva_iva == debe_llevar:
        return None
    coincide_ficha = (
        regimen_ficha is not None
        and lleva_iva == (regimen_ficha == REGIME_NACIONAL)
    )
    return {
        "tipo": tipo,
        "tipo_label": DOC_LABELS[tipo],
        "emitido": tipo in EMITIDOS,
        "numero": visible_number(row.get(spec.tip), row.get(spec.cod)),
        "fecha": str(row.get(spec.fec) or ""),
        "codcli": codcli_key(row.get(spec.cli)),
        "cliente": str(row.get(spec.cno) or ""),
        "base": round(base, 2),
        "iva": round(iva, 2),
        "total": round(_num(row.get(spec.tot)), 2),
        "regimen_esperado": esperado,
        "regimen_ficha": regimen_ficha,
        "coincide_con_ficha": coincide_ficha,
        "problema": (
            (f"lleva IVA ({iva:g}) y la pareja da {REGIME_LABELS[esperado]}: "
             "debía salir sin IVA"
             if lleva_iva else
             f"sale sin IVA y la pareja da {REGIME_LABELS[esperado]}: debía "
             "llevarlo")
            + (" (coincide con la ficha del cliente: repásala)"
               if coincide_ficha else
               " (tampoco coincide con la ficha del cliente)")
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
    nombre = str(row.get("NOFCLI") or row.get("NOCCLI") or "")
    actual = regime_from_fcli_row(row)
    nif_iva = eu_vat_for(iso2, vat=vat, nif=row.get("NIFCLI"))
    base = {
        "codcli": codcli_key(row.get("CODCLI")), "cliente": nombre,
        "serie": emisor.serie, "emisor": emisor.nombre,
        "emisor_pais": emisor.pais_iso2,
        "pais_iso2": iso2, "fuente_pais": fuente_pais,
        "nif": str(row.get("NIFCLI") or ""),
        # El NIF-IVA que se ha tenido en cuenta: un NIF español sin prefijo
        # («B65623175») NO es un NIF-IVA, así que el veredicto es «nacional»
        # por falta de dato, no porque el cliente no pueda ser exento.
        "nif_iva": nif_iva,
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
            + ("" if nif_iva else " — ojo: sin NIF-IVA utilizable, se juzga "
                                 "como consumidor final")
        ),
    }


def _companies_por_codcli(session: Session, codclis: set[str]) -> dict[str, Any]:
    """`{codcli normalizado: Company}` de las empresas del CRM vinculadas a
    esos clientes. El país del CRM manda sobre el `PAICLI` de la ficha: es el
    que mantienen los comerciales y el que usa el cálculo de los documentos."""
    from app.models.crm import Company  # noqa: PLC0415

    if not codclis:
        return {}
    rows = session.scalars(
        select(Company).where(Company.factusol_company_id.is_not(None))
    ).all()
    return {
        codcli_key(c.factusol_company_id): c for c in rows
        if codcli_key(c.factusol_company_id) in codclis
    }


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
    return [r for r in rows if serie_key(r.get(spec.tip)) == str(serie)]


def _fichas(
    client: FactusolClient, ejercicio: str,
) -> dict[str, dict[str, Any]]:
    """`{codcli normalizado: fila REAL de F_CLI}`, en UNA lectura.

    Una lectura por cliente eran cientos de `CargaTabla` de una fila cada una
    (con renovación de token de por medio) para un informe que se lanza a
    mano: el resto del módulo ya lee las tablas enteras con `1=1`."""
    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    return {codcli_key(r.get("CODCLI")): r for r in rows
            if codcli_key(r.get("CODCLI"))}


def auditar(
    session: Session, client: FactusolClient, *, ejercicio: str, emisor: Emisor,
) -> dict[str, Any]:
    """Recorre los documentos de la serie del `emisor` y las fichas de sus
    clientes. **No escribe nada.**

    Devuelve `{emisor, documentos, fichas, resumen}` con los hallazgos ya
    separados entre proformas y documentos emitidos."""
    documentos: list[dict[str, Any]] = []
    revisados: dict[str, int] = {}
    evaluados: dict[str, int] = {}
    codclis: set[str] = set()

    filas: dict[str, list[dict[str, Any]]] = {}
    for tipo in DOC_TYPES:
        spec = DOC_SPECS[tipo]
        filas[tipo] = _rows_de_serie(client, tipo, ejercicio=ejercicio,
                                     serie=emisor.serie)
        revisados[tipo] = len(filas[tipo])
        codclis.update(
            c for c in (codcli_key(r.get(spec.cli)) for r in filas[tipo]) if c
        )

    todas = _fichas(client, ejercicio)
    cache_fichas = {c: todas[c] for c in sorted(codclis) if c in todas}
    companies = _companies_por_codcli(session, codclis)
    contextos = {
        codcli: _contexto_cliente(ficha, companies.get(codcli))
        for codcli, ficha in cache_fichas.items()
    }
    esperados: dict[str, str | None] = {
        codcli: (
            regime_for(iso2, issuer_iso2=emisor.pais_iso2, vat=vat,
                       nif=cache_fichas[codcli].get("NIFCLI"), vies_valid=vies)
            if iso2 else None
        )
        for codcli, (iso2, _fuente, vat, vies) in contextos.items()
    }
    regimenes_ficha = {
        codcli: regime_from_fcli_row(ficha)
        for codcli, ficha in cache_fichas.items()
    }

    for tipo in DOC_TYPES:
        evaluados[tipo] = 0
        for row in filas[tipo]:
            codcli = codcli_key(row.get(DOC_SPECS[tipo].cli))
            if esperados.get(codcli) is None:
                continue  # sin país o sin ficha: no se puede decidir
            evaluados[tipo] += 1
            hallazgo = revisar_documento(
                tipo=tipo, row=row, esperado=esperados[codcli],
                regimen_ficha=regimenes_ficha.get(codcli),
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
            "revisados": revisados, "evaluados": evaluados,
            "clientes": len(codclis),
            "clientes_sin_ficha": len(codclis) - len(cache_fichas),
            "clientes_sin_crm": sum(
                1 for c in cache_fichas if c not in companies),
            "fichas_a_repasar": len(fichas),
            "proformas": len(proformas), "emitidos": len(emitidos),
            "emitidos_fuera_de_ficha": sum(
                1 for d in emitidos if not d["coincide_con_ficha"]),
        },
    }


def informe_texto(resultado: dict[str, Any], *, detalle: int = 40) -> str:
    """El informe para leer en consola. Solo texto: no decide nada."""
    emisor: Emisor = resultado["emisor"]
    r = resultado["resumen"]
    lineas = [
        f"Auditoría de IVA · serie {emisor.serie} · {emisor.nombre} "
        f"({country_display(emisor.pais_iso2)}) · ejercicio {resultado['ejercicio']}",
        "SOLO LECTURA: no se ha tocado ninguna ficha ni ningún documento.",
        "",
        "Documentos de la serie: " + " · ".join(
            f"{DOC_LABELS[t]}s {r['evaluados'].get(t, 0)} de {n}"
            for t, n in r["revisados"].items()
        ),
        "   (el resto no se puede juzgar: cliente sin ficha legible o sin país)",
        f"Clientes de la serie: {r['clientes']}"
        + (f" · {r['clientes_sin_ficha']} sin ficha legible" if r["clientes_sin_ficha"] else "")
        + (f" · {r['clientes_sin_crm']} sin empresa en el CRM (se juzgan con el "
           "país y el NIF de la ficha)" if r["clientes_sin_crm"] else ""),
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
        f"{r['emitidos']} (de ellos {r['emitidos_fuera_de_ficha']} no coinciden "
        "ni con la ficha del cliente)",
    ]
    for d in resultado["emitidos"][:detalle]:
        lineas.append(f"   · {d['tipo_label']} {d['numero']} {d['fecha']} "
                      f"{d['cliente'][:40]} ({d['total']:.2f} €) → {d['problema']}")
    if r["emitidos"] > detalle:
        lineas.append(f"   … y {r['emitidos'] - detalle} más (ver CSV)")
    lineas += [
        "",
        "Cómo leerlo: en la zona donde la pareja permite elegir manda la ficha "
        "F_CLI, así que un documento «coincide con la ficha» es el caso normal "
        "y lo que hay que repasar es la ficha (bloque 1), no necesariamente el "
        "documento. Las fichas se corrigen en FACTUSOL (o desde la empresa, "
        "«Régimen de IVA en FACTUSOL», cuando todas las empresas emisoras "
        "coinciden); las proformas se vuelven a emitir; lo ya emitido "
        "(rectificativa o abono) lo decide administración. BoHub no corrige "
        "nada por su cuenta.",
    ]
    return "\n".join(lineas)
