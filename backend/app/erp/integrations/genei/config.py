"""Configuración (no secreta) del envío con Genei y el comparador de agencias.

Vive en `Carrier.config_json` (JSON):
  - `preferred_couriers`: couriers preferidos por país de DESTINO, en orden
    (`{"ES": ["Correos", "GLS"], "FR": ["Chronopost"]}`). El comparador propone
    por defecto el preferido **factible más barato** del país.
  - `default_package`: medidas/peso de bulto por defecto (editable al crear),
    por si el pedido no los trae.
El ORIGEN (remitente por defecto de Genei) es `Carrier.default_address_id`, no
va aquí. Las CREDENCIALES van cifradas en `Carrier.api_credentials_encrypted`.

El comparador normaliza cada tarifa de `GET /agencies/prices` a `AgencyPrice`
(tolerante al naming real de Genei) y elige la agencia por defecto: entrega a
domicilio (door-to-door) y, entre las que casan un courier preferido del país,
la más barata; si ninguna preferida es factible, la más barata a domicilio.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.erp.models.carriers import Carrier

logger = logging.getLogger(__name__)

#: Mínimo del intervalo del sondeo de tracking (no martillear a Genei).
TRACKING_POLL_MIN_MINUTES = 10


@dataclass
class DefaultPackage:
    """Bulto por defecto (kg / cm). Se usa si el pedido no trae medidas."""

    weight: float = 1.0
    height: float = 20.0
    width: float = 20.0
    length: float = 20.0

    def as_package(self) -> dict[str, float]:
        """Formato `packagesArray` de Genei (origen ≠ almacén: sin box/refs)."""
        return {
            "weight": self.weight, "height": self.height,
            "width": self.width, "length": self.length,
        }

    @classmethod
    def from_dict(cls, data: Any) -> DefaultPackage:
        if not isinstance(data, dict):
            return cls()
        def num(key: str, default: float) -> float:
            try:
                val = float(data.get(key, default))
                return val if val > 0 else default
            except (TypeError, ValueError):
                return default
        return cls(
            weight=num("weight", 1.0), height=num("height", 20.0),
            width=num("width", 20.0), length=num("length", 20.0),
        )


@dataclass
class OriginAddress:
    """Origen (remitente) para la consulta de tarifas: el almacén SAT. El
    `default_address_id` de Genei (id del remitente registrado) va aparte, en la
    fila del carrier; esto es solo el país/CP/población para pedir precios."""

    iso_country: str = ""
    postal_code: str = ""
    town: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> OriginAddress:
        if not isinstance(data, dict):
            return cls()
        return cls(
            iso_country=str(data.get("iso_country") or data.get("country") or "").strip().upper(),
            postal_code=str(data.get("postal_code") or "").strip(),
            town=str(data.get("town") or data.get("city") or "").strip(),
        )


@dataclass
class GeneiConfig:
    """Config del adaptador Genei (no secreta)."""

    preferred_couriers: dict[str, list[str]] = field(default_factory=dict)
    default_package: DefaultPackage = field(default_factory=DefaultPackage)
    origin: OriginAddress = field(default_factory=OriginAddress)
    #: El origen es un almacén/remitente registrado en Genei (usa su address_id).
    is_warehouse: bool = True
    #: Base pública del backend para el webhook de estados (PR-2). El
    #: `notificationUrl` que se envía al crear = `<base>/api/webhooks/genei?token=…`
    #: (el token/secreto va cifrado en las credenciales). Vacío → no se envía
    #: notificationUrl y el webhook queda apagado (el resto sigue funcionando).
    webhook_base_url: str = ""
    #: Sondeo del tracking DETALLADO (eventos del transportista vía
    #: `/shipments/{code}/tracking`) en el `worker-sync`: el webhook de Genei
    #: solo avisa de su estado grueso. Encendido por defecto; solo lee de Genei.
    tracking_poll_enabled: bool = True
    #: Cada cuántos minutos se revisa cada envío vivo (mínimo 10).
    tracking_poll_minutes: int = 30
    #: Aviso de envío al CLIENTE lo manda BoHub (en su idioma, desde el
    #: remitente de la marca) en cuanto hay nº de seguimiento. Encendido por
    #: defecto; en Genei se desmarca «Destinatario → Al crear un envío».
    customer_email_enabled: bool = True

    def webhook_url(self, secret: str | None) -> str | None:
        """`notificationUrl` para Genei, o None si falta la base o el secreto."""
        base = (self.webhook_base_url or "").strip().rstrip("/")
        if not base or not secret:
            return None
        return f"{base}/api/webhooks/genei?token={secret}"

    def preferred_for(self, country_iso: str | None) -> list[str]:
        """Couriers preferidos para ese país de destino (ISO2, may/min da igual)."""
        if not country_iso:
            return []
        code = country_iso.strip().upper()
        return list(self.preferred_couriers.get(code, []))

    def to_json(self) -> str:
        return json.dumps({
            "preferred_couriers": {
                k.upper(): [str(x) for x in v]
                for k, v in self.preferred_couriers.items() if v
            },
            "default_package": asdict(self.default_package),
            "origin": asdict(self.origin),
            "is_warehouse": self.is_warehouse,
            "webhook_base_url": self.webhook_base_url,
            "tracking_poll_enabled": self.tracking_poll_enabled,
            "tracking_poll_minutes": self.tracking_poll_minutes,
            "customer_email_enabled": self.customer_email_enabled,
        })

    @classmethod
    def from_json(cls, raw: str | None) -> GeneiConfig:
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("carrier.config_json de Genei ilegible; uso defaults")
            return cls()
        if not isinstance(data, dict):
            return cls()
        prefs_raw = data.get("preferred_couriers") or {}
        prefs: dict[str, list[str]] = {}
        if isinstance(prefs_raw, dict):
            for country, couriers in prefs_raw.items():
                if isinstance(couriers, list):
                    prefs[str(country).upper()] = [str(c) for c in couriers if str(c).strip()]
        is_warehouse = data.get("is_warehouse")
        poll_enabled = data.get("tracking_poll_enabled")
        try:
            poll_minutes = int(data.get("tracking_poll_minutes") or 30)
        except (TypeError, ValueError):
            poll_minutes = 30
        return cls(
            preferred_couriers=prefs,
            default_package=DefaultPackage.from_dict(data.get("default_package")),
            origin=OriginAddress.from_dict(data.get("origin")),
            is_warehouse=bool(is_warehouse) if is_warehouse is not None else True,
            webhook_base_url=str(data.get("webhook_base_url") or "").strip(),
            tracking_poll_enabled=bool(poll_enabled) if poll_enabled is not None else True,
            tracking_poll_minutes=max(poll_minutes, TRACKING_POLL_MIN_MINUTES),
            customer_email_enabled=(bool(data.get("customer_email_enabled"))
                                    if data.get("customer_email_enabled") is not None else True),
        )

    @classmethod
    def of(cls, carrier: Carrier) -> GeneiConfig:
        return cls.from_json(carrier.config_json)


# --- normalización + comparador de agencias ---------------------------------

_ID_KEYS = ("id_agencia", "agencyId", "agency_id", "id", "idAgencia")
_NAME_KEYS = ("nombre_completo_agencia", "nombre_agencia", "name", "nombre",
              "agency", "agencia", "courier", "label")
_PRICE_KEYS = ("importe", "price", "precio", "total", "amount", "cost", "coste")
_SERVICE_KEYS = ("nombre_integracion_cliente", "service", "servicio", "tipo", "type",
                 "modalidad", "deliveryType")


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


#: Marcas de que una tarifa NO es entrega a domicilio (punto/oficina). Se usa
#: para preferir door-to-door salvo que el operario pida oficina a propósito.
_OFFICE_HINTS = ("oficina", "office", "punto", "point", "conveniencia", "access", "pickup", "shop")


@dataclass(frozen=True)
class AgencyPrice:
    """Tarifa normalizada de una agencia factible."""

    agency_id: str
    name: str
    price: float | None
    is_home_delivery: bool
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> AgencyPrice | None:
        agency_id = _first(data, _ID_KEYS)
        if agency_id is None:
            return None
        name = str(_first(data, _NAME_KEYS) or "").strip()
        price = _to_float(_first(data, _PRICE_KEYS))
        service = str(_first(data, _SERVICE_KEYS) or "").lower()
        # Genei marca la entrega a domicilio con `domicilio_domicilio` (1) o el
        # tipo de integración «Dom-Dom»; si no viene, se cae a la heurística del
        # nombre (oficina/punto de recogida).
        dom = data.get("domicilio_domicilio")
        if dom is not None:
            is_home = str(dom).strip() in ("1", "true", "True")
        else:
            blob = f"{name} {service}".lower()
            is_home = not any(hint in blob for hint in _OFFICE_HINTS)
        return cls(str(agency_id), name, price, is_home, data)

    def matches_preferred(self, preferred: list[str]) -> int | None:
        """Índice (prioridad) del primer courier preferido que casa el nombre,
        o None. Casa por subcadena, sin distinguir may/min."""
        low = self.name.lower()
        for i, courier in enumerate(preferred):
            if courier.strip() and courier.strip().lower() in low:
                return i
        return None


def normalize_prices(rows: list[dict[str, Any]]) -> list[AgencyPrice]:
    out = [AgencyPrice.from_raw(r) for r in rows if isinstance(r, dict)]
    return [a for a in out if a is not None]


@dataclass
class AgencyChoice:
    """Resultado del comparador: la propuesta por defecto + todas las opciones."""

    default: AgencyPrice | None
    home_options: list[AgencyPrice]
    all_options: list[AgencyPrice]


def choose_agencies(
    rows: list[dict[str, Any]], preferred: list[str], *, home_only: bool = True,
) -> AgencyChoice:
    """Comparador: normaliza, ordena por precio y elige la propuesta por defecto.

    Por defecto (`home_only=True`) trabaja sobre las agencias a DOMICILIO. La
    propuesta por defecto es el **preferido factible más barato** del país; si
    ninguna preferida es factible, la más barata a domicilio. Se devuelven
    también todas las opciones para el comparador (el operario puede cambiar a
    otra agencia o pedir oficina/punto)."""
    agencies = normalize_prices(rows)
    home = [a for a in agencies if a.is_home_delivery]
    pool = home if home_only else agencies

    def price_key(a: AgencyPrice) -> float:
        return a.price if a.price is not None else float("inf")

    ordered = sorted(pool, key=price_key)
    # Entre las preferidas factibles, la más barata (respetando además el orden
    # de preferencia como desempate secundario).
    preferred_hits = [
        (a.matches_preferred(preferred), a) for a in ordered
    ]
    preferred_hits = [(rank, a) for rank, a in preferred_hits if rank is not None]
    default: AgencyPrice | None
    if preferred_hits:
        default = min(preferred_hits, key=lambda t: (price_key(t[1]), t[0]))[1]
    else:
        default = ordered[0] if ordered else None
    return AgencyChoice(default=default, home_options=home, all_options=agencies)
