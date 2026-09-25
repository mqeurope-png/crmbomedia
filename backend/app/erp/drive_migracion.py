"""Migración de la hoja de seguimiento a una hoja cuya PROPIETARIA no es una persona.

Por qué: Google nunca aplica un rango protegido al PROPIETARIO de la hoja. Con la
hoja de Bart, las columnas bloqueadas del espejo bloquean a su equipo pero no a
él. Para que bloqueen a TODAS las personas, la hoja tiene que ser de otra
cuenta:

- **la cuenta de servicio** (`--crear`): BoHub crea la hoja nueva y es su dueña
  desde el origen. Google solo lo permite si la cuenta tiene cuota de Drive; las
  creadas desde el 15/04/2025 no la tienen y no pueden ser propietarias de nada.
  La propiedad tampoco se puede TRANSFERIR a una cuenta de servicio. La prueba de
  propiedad (`probar_propiedad`) lo comprueba sin tocar nada;
- **una cuenta dedicada** (`--destino`): una cuenta de Google que no usa nadie a
  diario crea una hoja vacía y la comparte con la cuenta de servicio como editora.
  BoHub migra dentro. Esa cuenta es la única que se salta la protección; se
  guarda como cuenta de emergencia.

Qué hace, en este orden (y si algo falla antes del paso 7, deshace lo suyo):

  1. Crea la hoja nueva (o valida la de destino).
  2. Copia «Seguimiento (app)» e «Incidencias (app)» con `sheets.copyTo`, que
     arrastra valores, formato, la columna `id` oculta, anchos y validación, y
     comprueba que la copia es IDÉNTICA, celda a celda, a la original.
  3. Hace una pasada normal del espejo sobre la hoja nueva. Pone los rangos
     protegidos (solo la cuenta de servicio), el formato y la foto, y lee de
     vuelta lo que alguien hubiera editado en la vieja desde la última pasada.
  4. Verifica: propietaria, rangos protegidos cuyos únicos editores son la
     cuenta de servicio, y quién se los podría saltar.
  5. Comprueba que nadie ha tocado la hoja vieja mientras tanto.
  6. Comparte la nueva con el equipo como EDITORES (las mismas personas que
     tenían acceso a la vieja).
  7. Re-apunta la configuración de BoHub a la nueva (la vieja queda anotada como
     archivo). El llamador confirma la sesión.
  8. `archivar_origen`: pasa la vieja a solo lectura y la renombra «ARCHIVO ·».

Los overrides, el histórico importado (`seguimiento_legacy`), las filas
manuales y la foto viven en la base de datos, casados por id. No dependen de la
hoja, así que migrar no los toca.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.drive_managed import _texto, is_separator, managed_tab_titles
from app.erp.drive_sheets import DriveSyncError, ManagedTabTransport
from app.erp.seguimiento import ID_INDEX

logger = logging.getLogger(__name__)

#: Título de la hoja nueva y de la hoja de la prueba de propiedad.
TITULO_NUEVA = "Seguimiento BoHub"
TITULO_PRUEBA = "BoHub · prueba de propiedad (se borra sola)"
#: Prefijo con el que se renombra la hoja vieja al archivarla.
PREFIJO_ARCHIVO = "ARCHIVO · "
#: Claves de `factusol_series_json` donde queda anotada la migración.
ARCHIVO_SETTING = "drive_spreadsheet_archivo_id"
MIGRACION_SETTING = "drive_migracion"

#: Roles de Drive que dejan editar la hoja.
_ROLES_EDICION = {"owner", "organizer", "fileOrganizer", "writer"}


class HojaMigrable(ManagedTabTransport, Protocol):
    """Lo que necesita la migración de una hoja (lo implementa
    `GoogleSheetsClient`)."""

    spreadsheet_id: str

    def spreadsheet_properties(self) -> dict[str, Any]: ...
    def set_spreadsheet_properties(self, props: dict[str, Any]) -> None: ...
    def copy_tab_to(self, title: str, destination_id: str) -> int: ...
    def rename_tab(self, sheet_id: int, title: str, *, index: int | None = None) -> None: ...
    def delete_tab(self, title: str) -> None: ...
    def refresh_tabs(self) -> None: ...
    def protections(self, title: str) -> list[dict[str, Any]]: ...


class ArchivosDrive(Protocol):
    """Lo que necesita de la API de Drive (lo implementa `GoogleDriveFiles`)."""

    def file_info(self, file_id: str) -> dict[str, Any]: ...
    def permissions(self, file_id: str) -> list[dict[str, Any]]: ...
    def share(self, file_id: str, email: str, role: str, *, kind: str = "user") -> None: ...
    def set_role(self, file_id: str, permission_id: str, role: str) -> None: ...
    def update(self, file_id: str, fields: dict[str, Any]) -> None: ...
    def delete(self, file_id: str) -> None: ...


#: (título, propiedades) → id de la hoja creada por la cuenta de servicio.
CrearHoja = Callable[[str, dict[str, Any]], str]
#: Pasada del espejo sobre una hoja (por defecto, la misma del botón).
Push = Callable[[Session, Any], dict[str, Any]]


def _mail(value: Any) -> str:
    return _texto(value).casefold()


# --- estado de una hoja -------------------------------------------------------


def estado_hoja(drive: ArchivosDrive, file_id: str, sa_email: str) -> dict[str, Any]:
    """Quién es el propietario, quién tiene acceso y quién se saltaría los
    rangos protegidos (el propietario, si no es la cuenta de servicio)."""
    sa = _mail(sa_email)
    info = drive.file_info(file_id)
    propietarios = [_mail(o.get("emailAddress")) for o in info.get("owners") or []]
    personas: list[dict[str, Any]] = []
    por_enlace: list[str] = []
    for p in drive.permissions(file_id):
        if p.get("deleted"):
            continue
        if p.get("type") in ("anyone", "domain"):
            por_enlace.append(f"{p.get('type')}:{p.get('role')}")
            continue
        email = _mail(p.get("emailAddress"))
        if not email or email == sa:
            continue
        personas.append({
            "id": p.get("id"), "email": email, "rol": p.get("role"),
            "tipo": p.get("type") or "user",
        })
    capacidades = info.get("capabilities") or {}
    return {
        "id": file_id,
        "nombre": info.get("name"),
        "propietarios": propietarios,
        "sa_es_propietaria": sa in propietarios,
        "sa_puede_editar": bool(capacidades.get("canEdit")),
        "editores_pueden_compartir": info.get("writersCanShare"),
        "personas": personas,
        "por_enlace": por_enlace,
        # Google nunca bloquea al propietario: si no es la cuenta de servicio,
        # se salta la protección.
        "exentos": [p for p in propietarios if p != sa],
    }


def resumen_pestana(valores: list[list[Any]]) -> dict[str, int]:
    """Recuento de una pestaña de la app: filas vivas, del histórico (bajo el
    separador) y cuántas llevan id."""
    def datos(filas: Iterable[list[Any]]) -> list[list[Any]]:
        return [list(f) for f in filas
                if any(_texto(c) for c in f) and not is_separator(list(f))]

    sep = next((i for i, f in enumerate(valores) if is_separator(list(f))), None)
    cuerpo = datos(valores[1:])
    return {
        "filas": len(valores),
        "vivas": len(datos(valores[1:sep] if sep is not None else valores[1:])),
        "historico": len(datos(valores[sep + 1:])) if sep is not None else 0,
        "ids": sum(1 for f in cuerpo if len(f) > ID_INDEX and _texto(f[ID_INDEX])),
    }


def recuentos_bd(session: Session) -> dict[str, int]:
    """Lo que vive en la base de datos, casado por id (no depende de la hoja)."""
    from app.erp.models.seguimiento_legacy import SeguimientoLegacy  # noqa: PLC0415
    from app.erp.models.seguimiento_mirror import (  # noqa: PLC0415
        SeguimientoManual,
        SeguimientoOverride,
        SeguimientoSnapshot,
    )

    def contar(modelo: Any, *cond: Any) -> int:
        return int(session.scalar(select(func.count()).select_from(modelo).where(*cond)) or 0)

    return {
        "overrides": contar(SeguimientoOverride),
        "historico_bd": contar(SeguimientoLegacy, SeguimientoLegacy.deleted_at.is_(None)),
        "manuales_bd": contar(SeguimientoManual, SeguimientoManual.deleted_at.is_(None)),
        "foto": contar(SeguimientoSnapshot),
    }


# --- prueba de propiedad --------------------------------------------------------


def _motivo_creacion(exc: DriveSyncError) -> str:
    texto = str(exc)
    bajo = texto.casefold()
    if "storage" in bajo or "quota" in bajo:
        return ("la cuenta de servicio no tiene cuota de Drive, así que Google no "
                "la deja ser propietaria de archivos (las creadas desde el "
                f"15/04/2025 no pueden). Detalle: {texto[:200]}")
    if " 403" in texto or "PERMISSION_DENIED" in texto:
        return ("Google no deja a la cuenta de servicio crear hojas propias (403). "
                f"Detalle: {texto[:200]}")
    return texto[:300]


def probar_propiedad(crear_hoja: CrearHoja, drive: ArchivosDrive) -> dict[str, Any]:
    """¿Puede la cuenta de servicio ser PROPIETARIA de una hoja? Crea una hoja
    vacía como ella y la borra en el acto. Es la pregunta de la que depende
    `--crear`."""
    try:
        file_id = crear_hoja(TITULO_PRUEBA, {})
    except DriveSyncError as exc:
        return {"puede": False, "motivo": _motivo_creacion(exc)}
    try:
        drive.delete(file_id)
        borrada = True
    except DriveSyncError as exc:
        logger.warning("drive: no se pudo borrar la hoja de prueba %s: %s", file_id, exc)
        borrada = False
    return {"puede": True, "borrada": borrada, "id": file_id}


# --- plan -----------------------------------------------------------------------


def plan_migracion(
    session: Session, *, origen: HojaMigrable, drive: ArchivosDrive, sa_email: str,
    destino_id: str | None = None, abrir: Callable[[str], HojaMigrable] | None = None,
) -> dict[str, Any]:
    """Qué se migraría, sin escribir nada. `errores` bloquea; `avisos`, no."""
    pedidos_tab, incidencias_tab = managed_tab_titles(session)
    errores: list[str] = []
    avisos: list[str] = []
    estado = estado_hoja(drive, origen.spreadsheet_id, sa_email)
    titulos = origen.tab_titles()
    pestanas = [t for t in (pedidos_tab, incidencias_tab) if t in titulos]
    if pedidos_tab not in titulos:
        errores.append(f"la hoja actual no tiene la pestaña «{pedidos_tab}»")
    valores = {t: origen.tab_values(t, raw=True) for t in pestanas}
    plan: dict[str, Any] = {
        "origen": estado,
        "pestanas": pestanas,
        "resumen": {t: resumen_pestana(v) for t, v in valores.items()},
        "otras_pestanas": [t for t in titulos if t not in pestanas],
        "bd": recuentos_bd(session),
        "ya_migrada": estado["sa_es_propietaria"],
        "errores": errores,
        "avisos": avisos,
        "_valores": valores,
    }
    if estado["sa_es_propietaria"]:
        errores.append("la hoja actual ya es propiedad de la cuenta de servicio: no hay "
                       "nada que migrar (usa --verificar)")
    if estado["por_enlace"]:
        avisos.append("la hoja actual se comparte también por enlace/dominio "
                      f"({', '.join(estado['por_enlace'])}); en la nueva NO se replica")
    if destino_id:
        plan["destino"] = _validar_destino(
            drive, destino_id, sa_email, estado, pestanas, abrir, errores, avisos,
        )
    return plan


def _validar_destino(
    drive: ArchivosDrive, destino_id: str, sa_email: str, origen: dict[str, Any],
    pestanas: list[str], abrir: Callable[[str], HojaMigrable] | None,
    errores: list[str], avisos: list[str],
) -> dict[str, Any]:
    if destino_id == origen["id"]:
        errores.append("la hoja destino es la misma que la actual")
        return {"id": destino_id}
    estado = estado_hoja(drive, destino_id, sa_email)
    equipo = {p["email"] for p in origen["personas"]} | set(origen["propietarios"])
    duenos_del_equipo = [p for p in estado["exentos"] if p in equipo]
    if duenos_del_equipo:
        errores.append(
            f"la hoja destino es de {', '.join(duenos_del_equipo)}, que ya usa la hoja: "
            "como propietario, la protección no le bloquearía. Créala con una cuenta "
            "que no use nadie a diario (o usa --crear)"
        )
    if not estado["sa_puede_editar"]:
        errores.append("la cuenta de servicio no puede editar la hoja destino: compártela "
                       f"con {sa_email} como EDITOR")
    if estado["exentos"] and not duenos_del_equipo:
        avisos.append(f"la propietaria de la hoja destino ({', '.join(estado['exentos'])}) "
                      "podrá saltarse la protección: guárdala como cuenta de emergencia")
    if estado["editores_pueden_compartir"] and not estado["sa_es_propietaria"]:
        avisos.append("en la hoja destino los editores pueden cambiar el acceso (y "
                      "quitárselo a BoHub): desde la cuenta propietaria, en Compartir → "
                      "⚙, desmarca «Los editores pueden cambiar los permisos»")
    if abrir is not None and estado["sa_puede_editar"]:
        hoja = abrir(destino_id)
        for t in pestanas:
            if t in hoja.tab_titles() and _con_datos(hoja.tab_values(t)):
                errores.append(f"la hoja destino ya tiene datos en «{t}»: no se sobrescribe")
    return estado


def _con_datos(valores: list[list[Any]]) -> bool:
    return any(_texto(c) for f in valores for c in f)


# --- migración ------------------------------------------------------------------


def _push_por_defecto(session: Session, hoja: Any) -> dict[str, Any]:
    from app.erp.api.seguimiento import (  # noqa: PLC0415
        drive_completados_rows,
        drive_live_rows,
    )
    from app.erp.drive_managed import push_managed_tabs  # noqa: PLC0415

    return push_managed_tabs(
        session, hoja, drive_live_rows(session), completados=drive_completados_rows(session),
    )


def migrar(
    session: Session, *, origen: HojaMigrable, drive: ArchivosDrive, sa_email: str,
    abrir: Callable[[str], HojaMigrable], crear_hoja: CrearHoja | None = None,
    destino_id: str | None = None, editores_extra: Iterable[str] = (),
    push: Push | None = None,
) -> dict[str, Any]:
    """Pasos 1-7 (ver el docstring del módulo). NO confirma la sesión: si el
    llamador confirma, BoHub pasa a escribir en la hoja nueva. Si algo falla,
    lanza `DriveSyncError` sin haber tocado la configuración, y borra la hoja
    nueva si la había creado."""
    if (crear_hoja is None) == (destino_id is None):
        raise DriveSyncError("elige una: crear la hoja nueva (--crear) o usar una "
                             "hoja destino ya creada (--destino ID)")
    plan = plan_migracion(session, origen=origen, drive=drive, sa_email=sa_email,
                          destino_id=destino_id, abrir=abrir)
    if plan["errores"]:
        raise DriveSyncError("; ".join(plan["errores"]))
    pestanas: list[str] = plan["pestanas"]
    valores: dict[str, list[list[Any]]] = plan.pop("_valores")
    propiedades = origen.spreadsheet_properties()
    ajustes = {k: propiedades[k] for k in ("locale", "timeZone") if propiedades.get(k)}

    creada = False
    if crear_hoja is not None:
        try:
            destino_id = crear_hoja(TITULO_NUEVA, ajustes)
        except DriveSyncError as exc:
            raise DriveSyncError(
                f"no se pudo crear la hoja nueva: {_motivo_creacion(exc)}. Nada ha "
                "cambiado. Usa una hoja destino creada por una cuenta dedicada (--destino)"
            ) from exc
        creada = True
    assert destino_id is not None
    try:
        destino = abrir(destino_id)
        if not creada and ajustes:
            destino.set_spreadsheet_properties(ajustes)
        _copiar(origen, destino, pestanas)
        for t in pestanas:
            if destino.tab_values(t, raw=True) != valores[t]:
                raise DriveSyncError(
                    f"la copia de «{t}» no es idéntica a la original (¿alguien la "
                    "estaba editando?): vuelve a lanzar la migración"
                )
        resumen = (push or _push_por_defecto)(session, destino)
        error_proteccion = (resumen.get("espejo") or {}).get("proteccion_error")
        if error_proteccion:
            raise DriveSyncError(f"Google rechazó la protección: {error_proteccion}")
        verificacion = verificar_hoja(destino, drive, destino_id, sa_email, pestanas[0])
        # Sin filas de BoHub no hay nada que proteger (y no es un fallo).
        sin_rangos = bool(resumen.get("rows")) and not verificacion["rangos_protegidos"]
        if sin_rangos or verificacion["problemas"]:
            raise DriveSyncError("la protección de la hoja nueva no es la esperada: "
                                 + "; ".join(verificacion["problemas"] or ["sin rangos"]))
        for t in pestanas:
            if origen.tab_values(t, raw=True) != valores[t]:
                raise DriveSyncError(
                    f"alguien ha editado «{t}» en la hoja actual durante la migración: "
                    "vuelve a lanzarla (que nadie toque la hoja ese minuto)"
                )
        compartidos = _compartir_equipo(drive, destino_id, plan["origen"], editores_extra,
                                        sa_email)
        if verificacion["sa_es_propietaria"]:
            # Que ningún editor pueda cambiar el acceso (ni quitárselo a BoHub).
            drive.update(destino_id, {"writersCanShare": False})
        # La verificación que cuenta, con el equipo ya dentro: a quién bloquea.
        verificacion = verificar_hoja(destino, drive, destino_id, sa_email, pestanas[0])
        resumen_nueva = {t: resumen_pestana(destino.tab_values(t, raw=True)) for t in pestanas}
    except Exception:
        # Cualquier fallo (también de red): la hoja a medias no se queda huérfana.
        if creada:
            try:
                drive.delete(destino_id)
            except Exception as exc:  # noqa: BLE001 — se avisa y sigue el error original
                logger.warning("drive: no se pudo borrar la hoja a medias %s: %s",
                               destino_id, exc)
        raise
    _reapuntar(session, origen.spreadsheet_id, destino_id,
               propietaria=", ".join(verificacion["propietarios"]))
    return {
        **plan,
        "destino_id": destino_id,
        "creada": creada,
        "copia_identica": True,
        "resumen_nueva": resumen_nueva,
        "pasada": {k: resumen.get(k) for k in ("rows", "manuales", "historico_preservado")},
        "espejo": resumen.get("espejo") or {},
        "compartidos": compartidos,
        "verificacion": verificacion,
        "bd_despues": recuentos_bd(session),
    }


def _copiar(origen: HojaMigrable, destino: HojaMigrable, pestanas: list[str]) -> None:
    """Copia cada pestaña con `copyTo` y le devuelve su título y su posición. Las
    pestañas vacías que sobren (la «Hoja 1» de una hoja nueva) se quitan."""
    for i, t in enumerate(pestanas):
        copia = origen.copy_tab_to(t, destino.spreadsheet_id)
        destino.refresh_tabs()
        if t in destino.tab_titles():
            destino.delete_tab(t)          # vacía: lo comprobó el plan
        destino.rename_tab(copia, t, index=i)
    for t in list(destino.tab_titles()):
        if t not in pestanas and not _con_datos(destino.tab_values(t)):
            destino.delete_tab(t)


def _compartir_equipo(
    drive: ArchivosDrive, destino_id: str, origen: dict[str, Any],
    extra: Iterable[str], sa_email: str,
) -> list[dict[str, str]]:
    """Las mismas personas que tenían acceso a la vieja. Quien la editaba (el
    propietario incluido) entra como EDITOR, nunca como propietario; quien solo
    la leía o comentaba, igual que antes."""
    ya = {_mail(p.get("emailAddress")) for p in drive.permissions(destino_id)}
    ya.add(_mail(sa_email))
    hechos: list[dict[str, str]] = []
    pendientes = [
        (p["email"], "writer" if p["rol"] in _ROLES_EDICION else p["rol"],
         "group" if p["tipo"] == "group" else "user")
        for p in origen["personas"]
    ] + [(_mail(e), "writer", "user") for e in extra if _mail(e)]
    for email, rol, tipo in pendientes:
        if email in ya:
            continue
        drive.share(destino_id, email, rol, kind=tipo)
        ya.add(email)
        hechos.append({"email": email, "rol": rol})
    return hechos


def _reapuntar(session: Session, origen_id: str, destino_id: str, *, propietaria: str) -> None:
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings  # noqa: PLC0415

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
    if cfg is None:
        raise DriveSyncError("no hay configuración ERP que re-apuntar")
    cfg.drive_spreadsheet_id = destino_id
    try:
        series = json.loads(cfg.factusol_series_json or "{}")
    except (TypeError, ValueError):
        series = {}
    if not isinstance(series, dict):
        series = {}
    series[ARCHIVO_SETTING] = origen_id
    series[MIGRACION_SETTING] = {
        "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
        "origen": origen_id, "destino": destino_id, "propietaria": propietaria,
    }
    cfg.factusol_series_json = json.dumps(series)
    session.flush()


# --- después de confirmar ---------------------------------------------------------


def archivar_origen(
    origen: HojaMigrable, drive: ArchivosDrive, sa_email: str,
) -> dict[str, Any]:
    """La hoja vieja queda de SOLO LECTURA para el equipo y se renombra
    «ARCHIVO · …». BoHub ya no la lee. Al propietario no se le puede quitar la
    edición (es suyo); queda avisado. Lo que falle se devuelve como aviso: la
    migración ya está hecha."""
    avisos: list[str] = []
    a_lectura: list[str] = []
    renombrada = False
    try:
        titulo = _texto(origen.spreadsheet_properties().get("title"))
        if not titulo.startswith(PREFIJO_ARCHIVO):
            origen.set_spreadsheet_properties({"title": f"{PREFIJO_ARCHIVO}{titulo}"})
            renombrada = True
    except DriveSyncError as exc:
        avisos.append(f"no se pudo renombrar la hoja vieja: {str(exc)[:150]}")
    try:
        estado = estado_hoja(drive, origen.spreadsheet_id, sa_email)
    except DriveSyncError as exc:
        avisos.append(f"no se pudo leer el acceso de la hoja vieja: {str(exc)[:150]}")
        return {"renombrada": renombrada, "a_lectura": a_lectura, "avisos": avisos}
    for p in estado["personas"]:
        if p["rol"] not in _ROLES_EDICION or p["rol"] == "owner":
            continue
        try:
            drive.set_role(origen.spreadsheet_id, str(p["id"]), "reader")
            a_lectura.append(p["email"])
        except DriveSyncError as exc:
            avisos.append(f"no se pudo pasar a lectura a {p['email']}: {str(exc)[:150]}")
    for dueno in estado["exentos"]:
        avisos.append(f"{dueno} sigue siendo propietario de la hoja vieja y puede "
                      "editarla, pero BoHub ya no la lee: es solo archivo")
    return {"renombrada": renombrada, "a_lectura": a_lectura, "avisos": avisos}


# --- verificación -----------------------------------------------------------------


def verificar_hoja(
    hoja: HojaMigrable, drive: ArchivosDrive, file_id: str, sa_email: str,
    pedidos_tab: str,
) -> dict[str, Any]:
    """¿Bloquean las columnas protegidas a TODAS las personas? Cada rango del
    espejo debe tener como ÚNICO editor a la cuenta de servicio (que sí puede
    escribirlo) y ser un bloqueo real, no solo un aviso. Quien sea propietario y
    no sea la cuenta de servicio se lo salta: sale en `exentos`."""
    from app.erp.seguimiento_mirror import PROTECT_DESC_PREFIX  # noqa: PLC0415

    sa = _mail(sa_email)
    estado = estado_hoja(drive, file_id, sa_email)
    propias = [p for p in hoja.protections(pedidos_tab)
               if _texto(p.get("description")).startswith(PROTECT_DESC_PREFIX)]
    problemas: list[str] = []
    for p in propias:
        editores = p.get("editors") or {}
        otros = sorted({_mail(u) for u in editores.get("users") or []} - {sa})
        desc = _texto(p.get("description"))
        if p.get("warningOnly"):
            problemas.append(f"«{desc}» solo avisa, no bloquea")
        if otros:
            problemas.append(f"«{desc}» la pueden editar también: {', '.join(otros)}")
        if editores.get("groups") or editores.get("domainUsersCanEdit"):
            problemas.append(f"«{desc}» la puede editar un grupo o todo el dominio")
        if p.get("requestingUserCanEdit") is False:
            problemas.append(f"«{desc}»: la cuenta de servicio no puede escribirla")
    exentos = estado["exentos"]
    bloqueados = sorted(p["email"] for p in estado["personas"]
                        if p["rol"] in _ROLES_EDICION and p["email"] not in exentos)
    return {
        "id": file_id,
        "propietarios": estado["propietarios"],
        "sa_es_propietaria": estado["sa_es_propietaria"],
        "rangos_protegidos": len(propias),
        "problemas": problemas,
        "exentos": exentos,
        "bloqueados": bloqueados,
        "editores_pueden_compartir": estado["editores_pueden_compartir"],
        "ok": bool(propias) and not problemas and not exentos,
    }


def compartir(drive: ArchivosDrive, file_id: str, email: str, rol: str = "writer") -> None:
    """Dar acceso a alguien más a la hoja (con la cuenta de servicio como
    propietaria, los editores no pueden compartir: se hace desde BoHub)."""
    if rol not in ("writer", "commenter", "reader"):
        raise DriveSyncError("rol no válido: writer, commenter o reader")
    drive.share(file_id, _mail(email), rol)
