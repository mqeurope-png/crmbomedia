"""El catálogo de intereses del clasificador: en datos, no en el código.

Cada interés tiene un **código** estable (lo que se guarda), una **etiqueta**
legible (lo que se ve), una **descripción** de cuándo aplica (lo que se le
manda al modelo para que sepa distinguirlos), si es **comercial** (los que no
lo son no llevan plantilla de venta) y un **orden** para las pantallas. Vive
en `lead_interests` y se gestiona desde Configuración ERP → Respuesta a
leads: añadir, editar y desactivar; no se borra si hay clasificaciones que lo
usan.

`DE_PARTIDA` es la lista con la que siembra la migración 0134 y la que se usa
cuando la tabla está vacía (una base recién creada en los tests): así el
clasificador funciona igual con o sin filas. Un lead puede tener VARIOS
intereses, ordenados por relevancia, el primero el principal: «placas de metal
y camisetas» es UV y DTF a la vez (10/10/2026).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

OTRO = "otro"
CODIGO_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

DE_PARTIDA: tuple[dict[str, Any], ...] = (
    {"codigo": "uv_pequeno", "etiqueta": "UV LED pequeño formato", "comercial": True,
     "descripcion": "Impresión UV directa sobre objetos pequeños: fundas de móvil, bolígrafos, "
                    "regalos y merchandising, botellas, objetos de sobremesa; impresoras de "
                    "A4 a A3 (Artisjet 3000U, 1500U, Pro V6)."},
    {"codigo": "uv_mediano", "etiqueta": "UV LED mediano formato", "comercial": True,
     "descripcion": "Impresión UV directa en formato medio (de A2 a 60×90 cm): placas, tableros, "
                    "puertas, series cortas de objetos; impresoras de sobremesa grande (Artisjet "
                    "5000U, 6090, flatbed)."},
    {"codigo": "uv_grande", "etiqueta": "UV LED gran formato", "comercial": True,
     "descripcion": "Impresión UV industrial o de gran formato: paneles, cartelería, rollo a "
                    "rollo, mesas de 2,5 m o más (2513, 3020), producción en volumen."},
    {"codigo": "dtf", "etiqueta": "DTF · impresión textil", "comercial": True,
     "descripcion": "Impresión textil: camisetas, sudaderas, gorras, bolsas, tejidos; "
                    "transferencia DTF (film y polvo), sublimación o impresión directa sobre "
                    "prenda."},
    {"codigo": "corte_laser", "etiqueta": "Corte láser", "comercial": True,
     "descripcion": "Corte de materiales con láser (CO2 o fibra): madera, metacrilato, cuero, "
                    "cartón, chapa fina; recortar piezas o formas."},
    {"codigo": "grabado_laser", "etiqueta": "Grabado láser", "comercial": True,
     "descripcion": "Grabado y marcaje con láser: logotipos, textos y fotos sobre madera, vidrio, "
                    "metal, cuero o plástico; personalización de objetos sin tinta."},
    {"codigo": "cnc", "etiqueta": "CNC", "comercial": True,
     "descripcion": "Fresado y mecanizado CNC: fresadoras, routers, corte y vaciado de madera, "
                    "aluminio, plásticos o composites."},
    {"codigo": "packaging", "etiqueta": "Packaging", "comercial": True,
     "descripcion": "Cajas, embalajes, estuches y etiquetas personalizados; impresión o corte "
                    "sobre cartón y materiales de embalaje."},
    {"codigo": "vending", "etiqueta": "Vending", "comercial": True,
     "descripcion": "Máquinas expendedoras (vending) personalizadas: snacks, bebidas, productos "
                    "propios, pantallas táctiles, eventos."},
    {"codigo": "distribucion", "etiqueta": "Distribución", "comercial": True,
     "descripcion": "Quiere ser distribuidor, revendedor, importador o partner de la marca en su "
                    "país o zona, no comprar una máquina para sí."},
    {"codigo": "soporte_postventa", "etiqueta": "Soporte postventa", "comercial": False,
     "descripcion": "Cliente que YA tiene la máquina y algo no va: averías, cabezales, calidad "
                    "de impresión, calibración, errores, garantía, mantenimiento. No es una "
                    "venta."},
    {"codigo": "tienda", "etiqueta": "Tienda · consumibles y repuestos", "comercial": True,
     "descripcion": "Tintas, películas de transferencia, barnices, primers, lámparas, cabezales y "
                    "otras piezas o consumibles para una máquina que ya tiene."},
    {"codigo": "otro", "etiqueta": "Otro", "comercial": False,
     "descripcion": "Gestiones que no son una venta: facturas, datos de transferencia, pedidos "
                    "ya hechos, correos sin consulta clara, candidaturas, proveedores que "
                    "ofrecen algo."},
)

#: De los códigos de la primera lista (08/10/2026) a los de esta, para los datos
#: que ya existen. `uv_pequeno_mediano` no se puede repartir sin adivinar: va a
#: `uv_mediano` y queda anotado; son pocos y Bart los corrige a mano.
RENOMBRADOS: dict[str, str] = {
    "uv_pequeno_mediano": "uv_mediano",
    "uv_gran_formato": "uv_grande",
    "laser_cnc": "corte_laser",
    "consumibles": "tienda",
    "repuestos": "tienda",
    "servicio_tecnico": "soporte_postventa",
}
#: Para el MAPA de plantillas, un contenido antiguo vale para todos los códigos
#: que salieron de él: «UV pequeño-mediano» para los dos tamaños y «Láser y
#: CNC» para corte, grabado y CNC. La plantilla sigue resolviendo igual que
#: antes para esos leads; separar contenidos es cosa de Bart, cuando los haya.
_REPARTO: dict[str, tuple[str, ...]] = {
    "uv_pequeno_mediano": ("uv_pequeno", "uv_mediano"),
    "laser_cnc": ("corte_laser", "grabado_laser", "cnc"),
}
RENOMBRADOS_MAPA: dict[str, tuple[str, ...]] = {
    codigo: _REPARTO.get(codigo, (nuevo,)) for codigo, nuevo in RENOMBRADOS.items()
}

#: Intereses que son tamaños del MISMO producto: un lead lleva como mucho uno
#: de cada familia (el que más puntúe).
FAMILIA_UNICA: dict[str, str] = {"uv_pequeno": "uv", "uv_mediano": "uv", "uv_grande": "uv"}


def migrar_codigo(codigo: str | None) -> str | None:
    if not codigo:
        return codigo
    return RENOMBRADOS.get(codigo, codigo)


def lista_desde_json(raw: str | None) -> list[str]:
    """La lista guardada en una columna JSON (`[]` si no hay o no se entiende)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return normalizar_lista(data)


def normalizar_lista(valores: Iterable[Any] | None) -> list[str]:
    """Códigos limpios y sin repetir, en el mismo orden."""
    vistos: list[str] = []
    for valor in valores or []:
        codigo = str(valor or "").strip().lower()
        if codigo and codigo not in vistos:
            vistos.append(codigo)
    return vistos


@dataclass(frozen=True)
class Interes:
    codigo: str
    etiqueta: str
    descripcion: str
    comercial: bool
    orden: int
    activo: bool = True

    def como_dict(self) -> dict[str, Any]:
        return {
            "id": self.codigo, "codigo": self.codigo, "label": self.etiqueta,
            "etiqueta": self.etiqueta, "descripcion": self.descripcion,
            "comercial": self.comercial, "orden": self.orden, "activo": self.activo,
        }


class Catalogo:
    """La lista de intereses con lo que el clasificador, el mapa y las
    pantallas preguntan de ella."""

    def __init__(self, intereses: Iterable[Interes]) -> None:
        self._todos = sorted(intereses, key=lambda i: (i.orden, i.codigo))
        self._por_codigo = {i.codigo: i for i in self._todos}

    @classmethod
    def de_partida(cls) -> Catalogo:
        return cls(
            Interes(codigo=d["codigo"], etiqueta=d["etiqueta"], descripcion=d["descripcion"],
                    comercial=bool(d["comercial"]), orden=orden)
            for orden, d in enumerate(DE_PARTIDA)
        )

    @property
    def todos(self) -> list[Interes]:
        return list(self._todos)

    @property
    def activos(self) -> list[Interes]:
        return [i for i in self._todos if i.activo]

    @property
    def codigos(self) -> tuple[str, ...]:
        """Los códigos activos, en orden."""
        return tuple(i.codigo for i in self.activos)

    @property
    def comerciales(self) -> frozenset[str]:
        return frozenset(i.codigo for i in self._todos if i.comercial)

    def conoce(self, codigo: str | None) -> bool:
        return bool(codigo) and codigo in self._por_codigo

    def activo(self, codigo: str | None) -> bool:
        interes = self._por_codigo.get(codigo or "")
        return interes is not None and interes.activo

    def comercial(self, codigo: str | None) -> bool:
        return codigo in self.comerciales

    def etiqueta(self, codigo: str | None) -> str:
        interes = self._por_codigo.get(codigo or "")
        return interes.etiqueta if interes is not None else (codigo or "—")

    def etiquetas(self, codigos: Iterable[str] | None) -> list[str]:
        return [self.etiqueta(c) for c in (codigos or [])]

    def texto(self, codigos: Iterable[str] | None) -> str:
        """«UV LED mediano formato + DTF · impresión textil»: el principal
        primero. Vacío si no hay ninguno."""
        return " + ".join(self.etiquetas(codigos))

    def limpiar(self, codigos: Iterable[Any] | None, *, solo_activos: bool = True) -> list[str]:
        """Lo que devuelve un proveedor o manda una corrección, dejado en una
        lista válida: códigos que existen (activos, salvo que se pida lo
        contrario), sin repetir, uno por familia (`FAMILIA_UNICA`), y `otro`
        si no queda ninguno."""
        salida: list[str] = []
        familias: set[str] = set()
        for codigo in normalizar_lista(codigos):
            if not self.conoce(codigo) or (solo_activos and not self.activo(codigo)):
                continue
            familia = FAMILIA_UNICA.get(codigo)
            if familia and familia in familias:
                continue
            if familia:
                familias.add(familia)
            salida.append(codigo)
        return salida or [OTRO]

    def para_prompt(self) -> str:
        """Los intereses activos como se le cuentan al modelo: código,
        etiqueta y la descripción de cuándo aplica."""
        lineas = []
        for i in self.activos:
            descripcion = f" — {i.descripcion}" if i.descripcion else ""
            comercial = "" if i.comercial else " (no es una venta)"
            lineas.append(f"- `{i.codigo}`: {i.etiqueta}{comercial}{descripcion}")
        return "\n".join(lineas)

    def opciones(self) -> list[dict[str, Any]]:
        """Para los desplegables: los activos, en orden."""
        return [{"id": i.codigo, "label": i.etiqueta, "comercial": i.comercial}
                for i in self.activos]

    def como_lista(self) -> list[dict[str, Any]]:
        """Para la pantalla de gestión: todos, con todo."""
        return [i.como_dict() for i in self._todos]


# --- lo que hay en la base -------------------------------------------------


def _desde_fila(fila: Any) -> Interes:
    return Interes(
        codigo=fila.code, etiqueta=fila.label, descripcion=fila.description or "",
        comercial=bool(fila.is_commercial), orden=int(fila.position or 0),
        activo=bool(fila.is_active),
    )


def cargar(session: Session) -> Catalogo:
    """El catálogo de la base; si la tabla está vacía, el de partida."""
    from app.models.leads import LeadInterest  # noqa: PLC0415

    filas = list(session.scalars(
        select(LeadInterest).order_by(LeadInterest.position, LeadInterest.code)
    ))
    if not filas:
        return Catalogo.de_partida()
    return Catalogo(_desde_fila(f) for f in filas)


def sembrar_si_vacio(session: Session) -> int:
    """Escribe la lista de partida si la tabla está vacía (antes de la
    primera edición desde la pantalla). Devuelve cuántos sembró."""
    from app.models.leads import LeadInterest  # noqa: PLC0415

    if session.scalar(select(LeadInterest.code).limit(1)) is not None:
        return 0
    for orden, d in enumerate(DE_PARTIDA):
        session.add(LeadInterest(
            code=d["codigo"], label=d["etiqueta"], description=d["descripcion"],
            is_commercial=bool(d["comercial"]), position=orden, is_active=True,
        ))
    session.flush()
    return len(DE_PARTIDA)


class InteresEnUso(Exception):
    def __init__(self, codigo: str, clasificaciones: int, en_mapa: int) -> None:
        partes = []
        if clasificaciones:
            partes.append(f"{clasificaciones} clasificaciones")
        if en_mapa:
            partes.append(f"{en_mapa} filas del mapa de plantillas")
        super().__init__(
            f"El interés «{codigo}» está en uso ({' y '.join(partes)}): desactívalo en vez "
            "de borrarlo."
        )
        self.codigo = codigo


def usos(session: Session) -> dict[str, tuple[int, int]]:
    """Por código: `(clasificaciones que lo llevan, filas del mapa que lo
    usan)`, en UNA pasada por las clasificaciones (una fila cuenta una vez
    por código, esté en la columna o en la lista, clasificado o corregido) y
    una lectura de la configuración."""
    from collections import Counter  # noqa: PLC0415

    from app.models.leads import LeadClassification  # noqa: PLC0415
    from app.services.leads.config import configuracion  # noqa: PLC0415

    clasificaciones: Counter[str] = Counter()
    filas = session.execute(select(
        LeadClassification.interest, LeadClassification.corrected_interest,
        LeadClassification.interests_json, LeadClassification.corrected_interests_json,
    )).all()
    for interest, corrected, lista_json, corregida_json in filas:
        codigos = set(lista_desde_json(lista_json)) | set(lista_desde_json(corregida_json))
        codigos |= {c for c in (interest, corrected) if c}
        for codigo in codigos:
            clasificaciones[codigo] += 1
    en_mapa: Counter[str] = Counter()
    for clave in configuracion(session).get("mapa") or {}:
        for codigo in set(str(clave).split(":", 1)[0].split("+")):
            if codigo:
                en_mapa[codigo] += 1
    return {
        codigo: (clasificaciones.get(codigo, 0), en_mapa.get(codigo, 0))
        for codigo in set(clasificaciones) | set(en_mapa)
    }


def en_uso(session: Session, codigo: str) -> tuple[int, int]:
    """`(clasificaciones que lo llevan, filas del mapa que lo usan)`."""
    return usos(session).get(codigo, (0, 0))


def validar_codigo(codigo: str) -> str:
    limpio = (codigo or "").strip().lower()
    if not CODIGO_RE.match(limpio):
        raise ValueError(
            "El código va en minúsculas, con letras, números y guiones bajos, "
            "de 2 a 40 caracteres y empezando por letra (p. ej. uv_pequeno)."
        )
    return limpio


def crear(
    session: Session, *, codigo: str, etiqueta: str, descripcion: str = "",
    comercial: bool = True, orden: int | None = None,
) -> Any:
    from app.models.leads import LeadInterest  # noqa: PLC0415

    sembrar_si_vacio(session)
    codigo = validar_codigo(codigo)
    etiqueta = (etiqueta or "").strip()
    if not etiqueta:
        raise ValueError("El interés necesita una etiqueta.")
    if session.get(LeadInterest, codigo) is not None:
        raise ValueError(f"Ya hay un interés con el código «{codigo}».")
    if orden is None:
        ultimo = session.scalar(select(LeadInterest.position).order_by(
            LeadInterest.position.desc()).limit(1))
        orden = int(ultimo or 0) + 1
    fila = LeadInterest(
        code=codigo, label=etiqueta[:80], description=(descripcion or "").strip() or None,
        is_commercial=bool(comercial), position=int(orden), is_active=True,
    )
    session.add(fila)
    session.flush()
    return fila


def actualizar(session: Session, codigo: str, cambios: dict[str, Any]) -> Any:
    """Etiqueta, descripción, comercial, orden y activo; el código no cambia
    (es lo que se guarda en las clasificaciones)."""
    from app.models.leads import LeadInterest  # noqa: PLC0415

    sembrar_si_vacio(session)
    fila = session.get(LeadInterest, codigo)
    if fila is None:
        raise LookupError(f"No hay ningún interés con el código «{codigo}».")
    if "etiqueta" in cambios:
        etiqueta = str(cambios["etiqueta"] or "").strip()
        if not etiqueta:
            raise ValueError("El interés necesita una etiqueta.")
        fila.label = etiqueta[:80]
    if "descripcion" in cambios:
        fila.description = str(cambios["descripcion"] or "").strip() or None
    if "comercial" in cambios:
        if not isinstance(cambios["comercial"], bool):
            raise ValueError("«Comercial» tiene que ser sí o no.")
        fila.is_commercial = cambios["comercial"]
    if "orden" in cambios:
        try:
            fila.position = int(cambios["orden"])
        except (TypeError, ValueError):
            raise ValueError("El orden tiene que ser un número.") from None
    if "activo" in cambios:
        if not isinstance(cambios["activo"], bool):
            raise ValueError("«Activo» tiene que ser sí o no.")
        if codigo == OTRO and not cambios["activo"]:
            raise ValueError("«Otro» no se puede desactivar: es a donde va lo que no encaja.")
        fila.is_active = cambios["activo"]
    session.flush()
    return fila


def borrar(session: Session, codigo: str) -> None:
    """Solo si nada lo usa; si no, `InteresEnUso` (desactivar es lo suyo).
    «Otro» es fijo: `ValueError`."""
    from app.models.leads import LeadInterest  # noqa: PLC0415

    sembrar_si_vacio(session)
    fila = session.get(LeadInterest, codigo)
    if fila is None:
        raise LookupError(f"No hay ningún interés con el código «{codigo}».")
    if codigo == OTRO:
        raise ValueError("«Otro» es fijo: no se borra ni se desactiva (es a donde va lo que no "
                         "encaja).")
    clasificaciones, en_mapa = en_uso(session, codigo)
    if clasificaciones or en_mapa:
        raise InteresEnUso(codigo, clasificaciones, en_mapa)
    session.delete(fila)
    session.flush()
