"""Descubre las rutas REALES de datos de la API DELSOL (C-1-fix1).

Contexto: `/login/Autenticar` funciona (200 + JWT), pero las rutas de datos
que asumió el Sprint 0 (`/registros/cargaTabla`, …) devuelven 404. La doc
oficial (apidoc.sdelsol.com) está bloqueada por la política de egress del
entorno de desarrollo/CI, así que este script hace el descubrimiento desde
donde SÍ hay red: el VPS de producción.

**Oráculo**: en ASP.NET Web API el routing se resuelve ANTES que la
autorización, así que:

  - ruta inexistente        → 404  (+ body "No HTTP resource was found…")
  - ruta EXISTENTE sin token→ 401  ← esto es un ACIERTO
  - ruta EXISTENTE con token→ 200 / 400 (payload inválido) ← acierto seguro

Uso (desde el VPS, dentro del contenedor api):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
        python -m scripts.factusol_discover_paths

    # probar candidatos extra (además de la matriz por defecto):
    ... python -m scripts.factusol_discover_paths /Datos/Cargar /Api/v1/CargaTabla

Al terminar imprime las envs que hay que añadir a `.env.production`.
No escribe nada en FACTUSOL: solo manda POSTs con body vacío `{}`.
"""
from __future__ import annotations

import sys

import httpx

from app.core.config import get_settings
from app.integrations.factusol.client import LOGIN_PATH

#: Controllers candidatos (el patrón confirmado es /{controller}/{action},
#: como en /login/Autenticar). Incluye los ya descartados por Bart para que el
#: informe sea completo y reproducible.
CONTROLLERS = [
    "registros", "registro", "datos", "dato", "tabla", "tablas",
    "consulta", "consultas", "factusol", "empresa", "empresas",
    "api", "gestion", "general", "comun", "servicio", "servicios",
    "delsol", "cloud", "bd", "basedatos", "data", "database",
    "table", "tables", "record", "records", "query", "queries",
    "documentos", "documento", "fichero", "ficheros", "maestros",
]

#: Acciones candidatas para "leer una tabla".
ACTIONS_READ = [
    "CargaTabla", "CargarTabla", "Cargar", "Carga",
    "LeerTabla", "Leer", "ObtenerTabla", "Obtener",
    "ConsultarTabla", "Consultar", "Listar", "ListarTabla",
    "LoadTable", "Load", "ReadTable", "Read", "GetTable", "Get", "Select",
]

#: Sufijos de acción para las otras 3 operaciones (se prueban en el controller
#: que resulte ganador para la lectura).
ACTIONS_BY_OP = {
    "write": ["EscribirRegistro", "Escribir", "InsertarRegistro", "Insertar",
              "CrearRegistro", "Crear", "NuevoRegistro", "Nuevo",
              "WriteRecord", "Write", "Insert", "Create", "Add"],
    "update": ["ActualizarRegistro", "Actualizar", "ModificarRegistro",
               "Modificar", "EditarRegistro", "Editar",
               "UpdateRecord", "Update", "Modify", "Edit"],
    "delete": ["BorrarRegistros", "BorrarRegistro", "Borrar",
               "EliminarRegistros", "Eliminar", "SuprimirRegistros",
               "DeleteRecords", "DeleteRecord", "Delete", "Remove"],
}

TIMEOUT = 12.0


def _probe(client: httpx.Client, base_url: str, path: str,
           token: str | None) -> tuple[int, str]:
    """POST {} sobre la ruta. Devuelve (status, body recortado)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = client.post(f"{base_url}{path}", json={}, headers=headers)
        return r.status_code, r.text[:160].replace("\n", " ")
    except httpx.HTTPError as exc:  # red/timeout: no concluyente
        return 0, f"<error de red: {exc}>"


def _is_hit(status: int) -> bool:
    """404 = la ruta no existe. Cualquier otra respuesta HTTP = existe."""
    return status not in (0, 404)


def main() -> int:
    settings = get_settings()
    base_url = settings.factusol_base_url.rstrip("/")
    extra = [a if a.startswith("/") else f"/{a}" for a in sys.argv[1:]]

    print(f"Base URL: {base_url}")
    print("Oráculo: 404 = ruta inexistente · 401/400/200 = RUTA VÁLIDA\n")

    # Precheck: si no hay red hacia la API, el barrido (cientos de peticiones)
    # solo acumularía timeouts. La ruta de login está confirmada, así que
    # debe responder algo distinto de un error de red.
    with httpx.Client(timeout=TIMEOUT) as http:
        status, body = _probe(http, base_url, LOGIN_PATH, None)
    if status == 0:
        print(f"❌ Sin conectividad con {base_url} ({body})")
        print("   Ejecuta este script desde el VPS, que sí alcanza la API.")
        return 2
    print(f"Conectividad OK (login responde {status}).\n")

    # Token opcional: con él, una ruta válida responde 200/400 en vez de 401
    # (señal más fuerte). Sin credenciales el descubrimiento funciona igual.
    token: str | None = None
    try:
        from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415

        token = FactusolClient.from_settings(settings).authenticate()
        print("Login OK — se probará CON token (200/400 = acierto seguro).\n")
    except Exception as exc:  # noqa: BLE001 — el probe funciona sin token
        print(f"Login no disponible ({exc}); se probará SIN token (401 = acierto).\n")

    hits: dict[str, list[str]] = {"read": [], "write": [], "update": [], "delete": []}

    with httpx.Client(timeout=TIMEOUT) as http:
        # 1) Candidatos explícitos pasados por CLI.
        for path in extra:
            status, body = _probe(http, base_url, path, token)
            mark = "✅" if _is_hit(status) else "  "
            print(f"{mark} {status:>3}  {path}   {body[:70]}")
            if _is_hit(status):
                hits["read"].append(path)

        # 2) Matriz controller × acción de lectura.
        print(f"\n--- Barrido de lectura: {len(CONTROLLERS)}×{len(ACTIONS_READ)} "
              f"= {len(CONTROLLERS) * len(ACTIONS_READ)} rutas ---")
        for controller in CONTROLLERS:
            for action in ACTIONS_READ:
                path = f"/{controller}/{action}"
                status, body = _probe(http, base_url, path, token)
                if _is_hit(status):
                    print(f"✅ {status:>3}  {path}   {body[:70]}")
                    hits["read"].append(path)

        if not hits["read"]:
            print("\n❌ Ningún acierto de lectura en la matriz.")
            print("   Pasa candidatos manualmente sacados de apidoc.sdelsol.com:")
            print("   python -m scripts.factusol_discover_paths /Xxx/Yyy /Zzz/Www")
            return 1

        # 3) Para el controller ganador, buscar las otras 3 operaciones.
        winner = hits["read"][0]
        controller = winner.strip("/").split("/")[0]
        print(f"\n--- Controller ganador: /{controller} — buscando write/update/delete ---")
        for op, actions in ACTIONS_BY_OP.items():
            for action in actions:
                path = f"/{controller}/{action}"
                status, body = _probe(http, base_url, path, token)
                if _is_hit(status):
                    print(f"✅ {status:>3}  [{op}] {path}   {body[:60]}")
                    hits[op].append(path)

    print("\n" + "=" * 72)
    print("RESULTADO — añade a .env.production (y reinicia api + worker-sync):\n")
    env_by_op = {
        "read": "FACTUSOL_PATH_LOAD_TABLE",
        "write": "FACTUSOL_PATH_WRITE_RECORD",
        "update": "FACTUSOL_PATH_UPDATE_RECORD",
        "delete": "FACTUSOL_PATH_DELETE_RECORDS",
    }
    for op, env in env_by_op.items():
        found = hits[op]
        if found:
            print(f"{env}={found[0]}"
                  + (f"    # otros candidatos: {', '.join(found[1:])}" if len(found) > 1 else ""))
        else:
            print(f"# {env}=???   ← no encontrado, revisar apidoc.sdelsol.com")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
