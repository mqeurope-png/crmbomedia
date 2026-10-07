"""Apariencia de un formulario: ancho, alineación y estilo.

Se guarda en `web_forms.appearance_json`. Cada opción vacía (`None`) deja
el aspecto de siempre, así que un formulario que no la toca genera
exactamente el mismo HTML que antes. El CSS se compone SOLO con valores
validados (enumerados, enteros en rango y colores `#rrggbb`): ningún texto
del usuario llega al CSS. El texto del botón va al HTML, escapado.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Por debajo de este ancho de pantalla el formulario ocupa el 100 %.
PUNTO_DE_CORTE_MOVIL_PX = 600

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: Tipografías ofrecidas (pilas seguras, sin cargar fuentes de fuera).
FUENTES: dict[str, str] = {
    "inherit": "inherit",
    "system": "system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
    "sans": "Helvetica,Arial,sans-serif",
    "serif": "Georgia,'Times New Roman',serif",
    "humanista": "'Segoe UI',Candara,'Trebuchet MS',sans-serif",
}

Via = Literal["iframe", "widget", "puro"]


class Apariencia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width_pct: int | None = Field(None, ge=25, le=100)
    max_width_px: int | None = Field(None, ge=240, le=2400)
    align: Literal["left", "center", "right"] | None = None
    theme: Literal["light", "dark", "inherit"] | None = None
    primary_color: str | None = None
    text_color: str | None = None
    background_color: str | None = None
    radius_px: int | None = Field(None, ge=0, le=32)
    font: Literal["inherit", "system", "sans", "serif", "humanista"] | None = None
    font_size_px: int | None = Field(None, ge=12, le=22)
    submit_text: str | None = Field(None, max_length=60)

    @field_validator("primary_color", "text_color", "background_color")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _HEX.match(v):
            raise ValueError("color inválido: usa el formato #rrggbb")
        return v.lower()

    @field_validator("submit_text")
    @classmethod
    def _texto(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    def es_por_defecto(self) -> bool:
        return all(v is None for v in self.model_dump().values())

    def texto_boton(self) -> str:
        return self.submit_text or "Enviar"


def cargar(raw: str | None) -> Apariencia:
    """La apariencia guardada; si falta o no se puede leer, la de siempre."""
    if not raw:
        return Apariencia()
    try:
        return Apariencia.model_validate(json.loads(raw))
    except (TypeError, ValueError):
        return Apariencia()


def volcar(ap: Apariencia | None) -> str | None:
    if ap is None or ap.es_por_defecto():
        return None
    return json.dumps(ap.model_dump(exclude_none=True), sort_keys=True)


# --- CSS --------------------------------------------------------------------

_TEMAS = {
    "light": {"texto": "#0f172a", "fondo": "#ffffff", "campo": "#ffffff", "borde": "#cbd5e1",
              "ayuda": "#64748b"},
    "dark": {"texto": "#f1f5f9", "fondo": "#0f172a", "campo": "#1e293b", "borde": "#334155",
             "ayuda": "#94a3b8"},
}

#: Alineación por defecto de cada vía (la de siempre): el iframe centra, el
#: widget y el fragmento van a la izquierda.
_ALINEACION_POR_DEFECTO = {"iframe": "center", "widget": "left", "puro": "left"}

_MARGENES = {"left": ("0", "auto"), "center": ("auto", "auto"), "right": ("auto", "0")}


def _selectores(via: Via, form_id: str) -> dict[str, str]:
    if via == "iframe":
        f = "#bh-form.bh-form"
        campos = f"{f} .bh-field input,{f} .bh-field textarea,{f} .bh-field select"
        return {"form": f, "campos": campos, "etiquetas": f"{f} .bh-field label",
                "boton": f"{f} .bh-btn", "ayuda": f"{f} .bh-help", "pagina": "body"}
    if via == "widget":
        m = f'[data-bohub-form="{form_id}"]'
        f = f"{m} form.bh-form"
        campos = f"{f} .bh-field input,{f} .bh-field textarea,{f} .bh-field select"
        return {"form": f, "campos": campos, "etiquetas": f"{f} .bh-field label",
                "boton": f"{f} .bh-btn", "ayuda": f"{f} .bh-help", "pagina": ""}
    f = f"#bh-form-{form_id}"
    return {"form": f, "campos": f"{f} .bh-input", "etiquetas": f"{f} .bh-label",
            "boton": f"{f} .bh-button", "ayuda": f"{f} .bh-help", "pagina": ""}


def css(ap: Apariencia, *, via: Via, form_id: str) -> str:
    """CSS adicional para la apariencia elegida, o `""` si es la de siempre.
    `form_id` solo se usa en selectores y debe ser el id real del formulario
    (lo pone el servidor, nunca el usuario)."""
    if ap.es_por_defecto():
        return ""
    s = _selectores(via, form_id)
    reglas: list[str] = []

    # Ancho y alineación.
    if ap.width_pct is not None or ap.max_width_px is not None or ap.align is not None:
        ancho = ap.width_pct or 100
        if ap.max_width_px is not None:
            maximo = f"{ap.max_width_px}px"
        else:
            maximo = "none" if ap.width_pct is not None else "520px"
        izq, der = _MARGENES[ap.align or _ALINEACION_POR_DEFECTO[via]]
        reglas.append(f"{s['form']}{{width:{ancho}%;max-width:{maximo};"
                      f"margin-left:{izq};margin-right:{der}}}")
        reglas.append(f"@media (max-width:{PUNTO_DE_CORTE_MOVIL_PX}px){{{s['form']}"
                      f"{{width:100%;max-width:100%}}}}")

    # Tema base.
    if ap.theme == "inherit":
        reglas.append(f"{s['form']},{s['campos']},{s['etiquetas']},{s['boton']}"
                      "{font-family:inherit}")
        reglas.append(f"{s['form']},{s['etiquetas']},{s['ayuda']}{{color:inherit}}")
        reglas.append(f"{s['campos']}{{background:transparent;color:inherit}}")
        if s["pagina"]:
            reglas.append(f"{s['pagina']}{{background:transparent}}")
    elif ap.theme in _TEMAS:
        t = _TEMAS[ap.theme]
        if via == "puro":
            # El fragmento no trae estilos: el tema le pone la base de BoHub.
            reglas.append(f"{s['form']}{{display:flex;flex-direction:column;gap:14px}}")
            reglas.append(f"{s['form']} .bh-field{{display:flex;flex-direction:column;gap:4px}}")
            reglas.append(f"{s['etiquetas']}{{font-size:14px;font-weight:600}}")
            reglas.append(f"{s['campos']}{{padding:10px 12px;border:1px solid {t['borde']};"
                          "border-radius:8px;font-size:14px;font-family:inherit}")
            reglas.append(f"{s['boton']}{{padding:12px 16px;background:#2563eb;color:#fff;"
                          "border:0;border-radius:8px;font-size:15px;font-weight:600;"
                          "cursor:pointer}")
        reglas.append(f"{s['form']}{{color:{t['texto']}}}")
        reglas.append(f"{s['campos']}{{background:{t['campo']};color:{t['texto']};"
                      f"border-color:{t['borde']}}}")
        reglas.append(f"{s['ayuda']}{{color:{t['ayuda']}}}")
        if s["pagina"]:
            reglas.append(f"{s['pagina']}{{background:{t['fondo']};color:{t['texto']}}}")
        elif ap.theme == "dark":
            reglas.append(f"{s['form']}{{background:{t['fondo']};padding:16px;"
                          "border-radius:8px}")

    # Colores.
    if ap.primary_color:
        c = ap.primary_color
        reglas.append(f"{s['boton']}{{background:{c};color:#fff}}")
        reglas.append(f"{s['campos']}:focus{{outline:2px solid {c};outline-offset:1px;"
                      f"border-color:{c}}}")
    if ap.text_color:
        reglas.append(f"{s['form']},{s['etiquetas']}{{color:{ap.text_color}}}")
        reglas.append(f"{s['campos']}{{color:{ap.text_color}}}")
    if ap.background_color:
        reglas.append(f"{s['form']}{{background:{ap.background_color};padding:16px;"
                      "border-radius:8px}")
        if s["pagina"]:
            reglas.append(f"{s['pagina']}{{background:{ap.background_color}}}")

    # Formas y letra.
    if ap.radius_px is not None:
        reglas.append(f"{s['campos']},{s['boton']}{{border-radius:{ap.radius_px}px}}")
    if ap.font:
        pila = FUENTES[ap.font]
        reglas.append(f"{s['form']},{s['campos']},{s['boton']}{{font-family:{pila}}}")
        if s["pagina"] and ap.font != "inherit":
            reglas.append(f"{s['pagina']}{{font-family:{pila}}}")
    if ap.font_size_px is not None:
        n = ap.font_size_px
        reglas.append(f"{s['etiquetas']},{s['campos']}{{font-size:{n}px}}")
        reglas.append(f"{s['boton']}{{font-size:{n + 1}px}}")
        reglas.append(f"{s['ayuda']}{{font-size:{max(n - 2, 10)}px}}")
    return "".join(reglas)
