"""Capa de embed PÚBLICA de formularios web (sin auth, CORS abierto).

  - GET /forms/{form_id}         → página HTML server-rendered (iframe),
                                   estilos propios BoHub, aislada.
  - GET /forms/embed/{form_id}.js → widget JS vanilla (<15KB, sin deps)
                                   que se inyecta en cualquier web host.

Ambos consumen la API pública (`/public/forms/{id}/config.json` +
`/submit`). El CORS `*` para el prefijo `/forms/` lo aplica el middleware
de `app.main`.
"""
# ruff: noqa: E501 — este módulo contiene HTML/JS embebido (widget +
# iframe) cuyas líneas de template exceden 100 cols a propósito.
from __future__ import annotations

import html
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import not_found
from app.db.session import get_session
from app.models.web_forms import WebForm
from app.services.web_forms import apariencia as aparien
from app.services.web_forms.enlaces import texto_con_enlaces

router = APIRouter(tags=["web-forms-embed"])

#: Código propio de «el formulario existe pero está desactivado», distinto
#: del 404 «Form not found» (no existe).
FORM_INACTIVE_CODE = "form_inactive"
FORM_INACTIVE_MESSAGE = (
    "Formulario desactivado: no se muestra en la web hasta activarlo en BoHub."
)


class FormularioDesactivado(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=403, detail={
            "code": FORM_INACTIVE_CODE, "message": FORM_INACTIVE_MESSAGE})


def _get_active_form(session: Session, form_id: str) -> WebForm:
    """El formulario publicado. 404 si no existe; 403 `form_inactive` si
    existe pero está desactivado (antes daba el mismo 404 y no había forma
    de saber por qué no se pintaba)."""
    form = session.get(WebForm, form_id)
    if form is None:
        raise not_found("Form")
    if not form.is_active:
        raise FormularioDesactivado()
    return form


def _api_base() -> str:
    settings = get_settings()
    return (settings.web_forms_embed_base_url or settings.frontend_base_url).rstrip("/")


def _field_config(form: WebForm) -> list[dict]:
    out = []
    for f in sorted(form.fields, key=lambda x: x.position):
        options = []
        if f.options_json:
            try:
                parsed = json.loads(f.options_json)
                options = parsed if isinstance(parsed, list) else []
            except (TypeError, ValueError):
                options = []
        out.append({
            "key": f.field_key, "label": f.label, "type": f.field_type,
            "placeholder": f.placeholder or "", "help_text": f.help_text or "",
            "required": f.is_required, "hidden": f.is_hidden,
            "default_value": f.default_value or "", "options": options,
            "label_html": texto_con_enlaces(f.label),
            "help_html": texto_con_enlaces(f.help_text or ""),
        })
    return out


@router.get("/forms/{form_id}", response_class=HTMLResponse)
def render_iframe(
    form_id: str, session: Session = Depends(get_session)
) -> HTMLResponse:
    """Página HTML autocontenida del form para iframe. Estilo propio
    BoHub (aislado de la web host)."""
    try:
        form = _get_active_form(session, form_id)
    except FormularioDesactivado:
        return HTMLResponse(status_code=403, content=(
            '<!doctype html><meta charset="utf-8"><p style="font-family:system-ui,sans-serif;'
            f'color:#64748b;margin:16px">{html.escape(FORM_INACTIVE_MESSAGE)}</p>'))
    settings = get_settings()
    site_key = settings.recaptcha_site_key if form.recaptcha_enabled else None
    return HTMLResponse(content=render_iframe_html(form, api_base=_api_base(), site_key=site_key))


#: El iframe avisa a la página que lo contiene de su altura, para que crezca
#: con el contenido (p. ej. al mostrar errores) en vez de recortarlo. La
#: página lo escucha con el código de inserción del iframe.
def iframe_resize_js(form_id: str) -> str:
    """Script del iframe (no cambia el aspecto):

    - avisa a la página que lo contiene de su altura, para que crezca con el
      contenido;
    - los enlaces relativos (`/politica-de-privacidad/`) apuntan a la web que
      lo inserta, no a BoHub (el iframe se sirve desde el dominio de BoHub).
    """
    return (
        "<script>\n(function(){if(window.parent===window)return;var id="
        + json.dumps(form_id)
        + ';\nvar o="";try{o=(location.ancestorOrigins&&location.ancestorOrigins[0])||'
        '(document.referrer?new URL(document.referrer).origin:"");}catch(e){}\n'
        'if(/^https?:\/\/[^\/]+$/.test(o)){Array.prototype.forEach.call('
        'document.querySelectorAll(\'a[href^="/"]\'),function(a){var h=a.getAttribute("href");'
        'if(h.charAt(1)!=="/"&&h.charAt(1)!=="\\\\")a.setAttribute("href",o+h);});}\n'
        'function h(){window.parent.postMessage({type:"bohub-form-height",formId:id,'
        'height:document.documentElement.scrollHeight},"*");}\n'
        'window.addEventListener("load",h);if(window.ResizeObserver){new ResizeObserver(h)'
        ".observe(document.body);}else{setInterval(h,500);}})();\n</script>\n"
    )


def render_iframe_html(form: WebForm, *, api_base: str, site_key: str | None) -> str:
    """HTML de la vía iframe (también la vista previa del editor)."""
    ap = aparien.cargar(form.appearance_json)
    extra_css = aparien.css(ap, via="iframe", form_id=form.id)
    extra_css = f"{extra_css}\n" if extra_css else ""
    fields = _field_config(form)

    rows = "".join(_render_field_html(f) for f in fields)
    recaptcha_script = (
        f'<script src="https://www.google.com/recaptcha/api.js?render='
        f'{html.escape(site_key)}"></script>' if site_key else ""
    )
    page = f"""<!doctype html>
<html lang="{html.escape(form.language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(form.name)}</title>
{recaptcha_script}
<style>
*{{box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px;color:#0f172a;background:#fff}}
.bh-form{{max-width:520px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
.bh-field{{display:flex;flex-direction:column;gap:4px}}
.bh-field label{{font-size:14px;font-weight:600}}
.bh-field input,.bh-field textarea,.bh-field select{{padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;font-family:inherit}}
.bh-help{{font-size:12px;color:#64748b}}
.bh-req{{color:#dc2626}}
.bh-btn{{padding:12px 16px;background:#2563eb;color:#fff;border:0;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}}
.bh-btn:disabled{{opacity:.6;cursor:not-allowed}}
.bh-msg{{padding:14px;border-radius:8px;font-size:14px}}
.bh-ok{{background:#dcfce7;color:#166534}}
.bh-err{{background:#fee2e2;color:#991b1b}}
.bh-hp{{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}}
.bh-stars{{display:inline-flex;flex-direction:row-reverse;gap:.15em;justify-content:flex-end}}
.bh-stars input{{position:absolute;opacity:0;pointer-events:none}}
.bh-stars label{{cursor:pointer;color:#d0d0d0;font-size:1.7rem;line-height:1}}
.bh-stars label:hover,.bh-stars label:hover ~ label,.bh-stars input:checked ~ label{{color:#f5b301}}
{extra_css}</style>
</head>
<body>
<form class="bh-form" id="bh-form">
{rows}
<input class="bh-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
<button class="bh-btn" type="submit">{html.escape(ap.texto_boton())}</button>
<div class="bh-msg" id="bh-msg" style="display:none"></div>
</form>
<script>
{_WIDGET_CORE_JS}
window.__bhInit({{
  formId: {json.dumps(form.id)},
  apiBase: {json.dumps(api_base)},
  siteKey: {json.dumps(site_key)},
  formEl: document.getElementById("bh-form"),
  msgEl: document.getElementById("bh-msg")
}});
</script>
{iframe_resize_js(form.id)}</body>
</html>"""
    return page


@router.get("/forms/embed/{form_id}.js")
def render_widget_js(
    form_id: str, session: Session = Depends(get_session)
) -> Response:
    """Widget JS vanilla que se auto-inyecta en la web host. Renderiza el
    form desde config.json, hereda estilos del host (reset mínimo),
    recopila UTM/referrer/landing y envía. Soporta varias instancias."""
    try:
        form = _get_active_form(session, form_id)
    except FormularioDesactivado:
        # Un <script> con error no se ejecuta y la página no pinta nada sin
        # dar pistas: se sirve un JS que lo explica en la consola.
        aviso = json.dumps(f"BoHub ({form_id}): {FORM_INACTIVE_MESSAGE}")
        return Response(
            content=f'console.warn({aviso});document.querySelectorAll('
                    f'"[data-bohub-form]").forEach(function(m){{if(m.getAttribute('
                    f'"data-bohub-form")==={json.dumps(form_id)})m.setAttribute('
                    f'"data-bohub-error",{json.dumps(FORM_INACTIVE_CODE)});}});',
            media_type="application/javascript", status_code=200,
            headers={"Cache-Control": "public, max-age=60"},
        )
    api_base = _api_base()
    js = _WIDGET_CORE_JS + "\n" + _WIDGET_BOOT_JS.replace(
        "__FORM_ID__", json.dumps(form.id)
    ).replace("__API_BASE__", json.dumps(api_base))
    return Response(
        content=js,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _stars_radio_markup(key: str) -> str:
    """Radios 5→1 + labels ★ del widget de estrellas. El orden inverso en el
    DOM + `flex-direction:row-reverse` + los selectores `~` del CSS pintan
    hasta la estrella hovered/seleccionada (patrón 0-JS)."""
    return "".join(
        f'<input type="radio" name="{key}" value="{v}" id="{key}_{v}">'
        f'<label for="{key}_{v}" aria-label="{v}">★</label>'
        for v in (5, 4, 3, 2, 1)
    )


def _render_field_html(f: dict) -> str:
    key = html.escape(f["key"])
    label = f["label_html"]
    ph = html.escape(f["placeholder"])
    default = html.escape(f["default_value"])
    req = ' required' if f["required"] else ""
    star = ' <span class="bh-req">*</span>' if f["required"] else ""
    help_html = f'<span class="bh-help">{f["help_html"]}</span>' if f["help_text"] else ""
    # Campos ocultos (UTM): input hidden con su default_value para que se
    # envíe en el submit — antes se descartaban y su default se perdía.
    if f["hidden"] or f["type"] == "hidden":
        return f'<input type="hidden" name="{key}" value="{default}">'
    val = f' value="{default}"' if default else ""
    if f["type"] == "textarea":
        control = f'<textarea name="{key}" placeholder="{ph}"{req} rows="4">{default}</textarea>'
    elif f["type"] == "select":
        opts = "".join(
            f'<option value="{html.escape(str(o.get("value","")))}"'
            f'{" selected" if str(o.get("value","")) == f["default_value"] else ""}>'
            f'{html.escape(str(o.get("label","")))}</option>'
            for o in f["options"]
        )
        control = f'<select name="{key}"{req}><option value="">—</option>{opts}</select>'
    elif f["type"] == "checkbox":
        return (
            f'<div class="bh-field"><label>'
            f'<input type="checkbox" name="{key}"> {label}{star}</label>{help_html}</div>'
        )
    elif f["type"] == "tags":
        boxes = "".join(
            f'<label class="bh-check"><input type="checkbox" name="{key}[]" '
            f'value="{html.escape(str(o.get("tag_id","")))}"> '
            f'{html.escape(str(o.get("label","")))}</label>'
            for o in f["options"]
        )
        return (
            f'<div class="bh-field"><label>{label}{star}</label>'
            f'<div class="bh-tags">{boxes}</div>{help_html}</div>'
        )
    elif f["type"] == "stars":
        return (
            f'<div class="bh-field"><label>{label}{star}</label>'
            f'<div class="bh-stars">{_stars_radio_markup(key)}</div>{help_html}</div>'
        )
    else:
        itype = html.escape(f["type"]) if f["type"] in {"email", "tel"} else "text"
        control = f'<input type="{itype}" name="{key}" placeholder="{ph}"{req}{val}>'
    return (
        f'<div class="bh-field"><label>{label}{star}</label>'
        f'{control}{help_html}</div>'
    )


# --- HTML puro copiable (v3 Bug 3) ------------------------------------------
# Fragmento sin <html>/<head>/<body>, clases semánticas SIN estilar, para
# pegar en cualquier web y maquetar con el CSS propio del sitio. Incluye el
# honeypot, el snippet reCAPTCHA v3 inline y una sugerencia de estilos base
# comentada. Sirve tanto al endpoint público /html como al embed-code admin.

_PURE_STYLE_SUGGESTION = """<!-- Sugerencia de estilos base (descomenta y ajusta a tu web):
<style>
.bh-form{max-width:520px;display:flex;flex-direction:column;gap:14px}
.bh-field{display:flex;flex-direction:column;gap:4px}
.bh-label{font-size:14px;font-weight:600}
.bh-input{padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px}
.bh-button{padding:12px 16px;background:#2563eb;color:#fff;border:0;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}
.bh-message{padding:14px;border-radius:8px;font-size:14px;background:#dcfce7;color:#166534}
.bh-stars{display:inline-flex;flex-direction:row-reverse;gap:.15em;justify-content:flex-end}
.bh-stars input{position:absolute;opacity:0;pointer-events:none}
.bh-stars label{cursor:pointer;color:#d0d0d0;font-size:1.7rem;line-height:1}
.bh-stars label:hover,.bh-stars label:hover ~ label,.bh-stars input:checked ~ label{color:#f5b301}
</style>
-->"""


def _render_pure_field(f: dict) -> str:
    """Un campo del form como HTML crudo con clases semánticas (bh-label,
    bh-input) SIN estilar. Espeja `_render_field_html` pero neutro."""
    key = html.escape(f["key"])
    label = f["label_html"]
    ph = html.escape(f["placeholder"])
    default = html.escape(f["default_value"])
    req = " required" if f["required"] else ""
    star = ' <span class="bh-req">*</span>' if f["required"] else ""
    help_html = f'<span class="bh-help">{f["help_html"]}</span>' if f["help_text"] else ""
    if f["hidden"] or f["type"] == "hidden":
        return f'<input type="hidden" name="{key}" value="{default}">'
    if f["type"] == "textarea":
        control = f'<textarea class="bh-input" name="{key}" placeholder="{ph}"{req} rows="4">{default}</textarea>'
    elif f["type"] == "select":
        opts = "".join(
            f'<option value="{html.escape(str(o.get("value","")))}"'
            f'{" selected" if str(o.get("value","")) == f["default_value"] else ""}>'
            f'{html.escape(str(o.get("label","")))}</option>'
            for o in f["options"]
        )
        control = f'<select class="bh-input" name="{key}"{req}><option value="">—</option>{opts}</select>'
    elif f["type"] == "checkbox":
        return (
            f'<div class="bh-field"><label class="bh-label">'
            f'<input class="bh-check" type="checkbox" name="{key}"> {label}{star}</label>{help_html}</div>'
        )
    elif f["type"] == "tags":
        boxes = "".join(
            f'<label class="bh-check-label"><input class="bh-check" type="checkbox" name="{key}[]" '
            f'value="{html.escape(str(o.get("tag_id","")))}"> '
            f'{html.escape(str(o.get("label","")))}</label>'
            for o in f["options"]
        )
        return (
            f'<div class="bh-field"><span class="bh-label">{label}{star}</span>'
            f'<div class="bh-tags">{boxes}</div>{help_html}</div>'
        )
    elif f["type"] == "stars":
        return (
            f'<div class="bh-field"><span class="bh-label">{label}{star}</span>'
            f'<div class="bh-stars">{_stars_radio_markup(key)}</div>{help_html}</div>'
        )
    else:
        itype = html.escape(f["type"]) if f["type"] in {"email", "tel"} else "text"
        val = f' value="{default}"' if default else ""
        control = f'<input class="bh-input" type="{itype}" name="{key}" placeholder="{ph}"{req}{val}>'
    return (
        f'<div class="bh-field"><label class="bh-label">{label}{star}</label>'
        f'{control}{help_html}</div>'
    )


def build_pure_html_fragment(form: WebForm, *, api_base: str, site_key: str | None) -> str:
    """Fragmento HTML puro copiable del formulario. `<form>` con
    action/method reales al endpoint de submit, honeypot siempre, y un
    snippet inline (~25 líneas) que ejecuta reCAPTCHA v3 y envía como JSON
    vía fetch (sin navegación, sin dependencias externas)."""
    fields = _field_config(form)
    ap = aparien.cargar(form.appearance_json)
    estilo = aparien.css(ap, via="puro", form_id=form.id)
    action = f"{api_base}/public/forms/{form.id}/submit"
    form_dom_id = f"bh-form-{form.id}"
    rows = "\n".join(_render_pure_field(f) for f in fields)
    honeypot = (
        '<input class="bh-hp" type="text" name="website" tabindex="-1" '
        'autocomplete="off" aria-hidden="true" style="position:absolute;left:-9999px">'
    )
    recaptcha_head = (
        f'<script src="https://www.google.com/recaptcha/api.js?render={html.escape(site_key)}" async defer></script>\n'
        if site_key else ""
    )
    submit_js = f"""<script>
(function(){{
  var form=document.getElementById({json.dumps(form_dom_id)});
  if(!form)return;
  var SITE_KEY={json.dumps(site_key)},ACTION={json.dumps(action)};
  function send(token){{
    var data={{}};
    new FormData(form).forEach(function(v,k){{if(k.slice(-2)==="[]"){{var b=k.slice(0,-2);(data[b]=data[b]||[]).push(v);}}else{{data[k]=v;}}}});
    if(token)data.recaptcha_token=token;
    var p=new URLSearchParams(location.search);
    ["utm_source","utm_medium","utm_campaign"].forEach(function(k){{if(p.get(k))data[k]=p.get(k);}});
    data.referrer=document.referrer||"";data.landing_page=location.href;
    var msg=form.querySelector(".bh-message");
    fetch(ACTION,{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(data)}})
      .then(function(r){{return r.json();}})
      .then(function(j){{
        if(j.success&&j.action==="redirect"&&j.redirect_url){{location.href=j.redirect_url;return;}}
        if(msg){{msg.style.display="block";msg.textContent=j.success?(j.success_message||"¡Gracias! Hemos recibido tu solicitud."):"No se pudo enviar. Revisa los datos.";}}
        if(j.success)form.reset();
      }})
      .catch(function(){{if(msg){{msg.style.display="block";msg.textContent="Error de conexión. Inténtalo de nuevo.";}}}});
  }}
  form.addEventListener("submit",function(e){{
    e.preventDefault();
    if(SITE_KEY&&window.grecaptcha){{grecaptcha.ready(function(){{grecaptcha.execute(SITE_KEY,{{action:"submit"}}).then(send);}});}}
    else{{send(null);}}
  }});
}})();
</script>"""
    return (
        f'{recaptcha_head}'
        f'{f"<style>{estilo}</style>" + chr(10) if estilo else ""}'
        f'<form class="bh-form" id="{form_dom_id}" action="{html.escape(action)}" method="POST">\n'
        f'{rows}\n'
        f'{honeypot}\n'
        f'<input type="hidden" name="recaptcha_token" value="">\n'
        f'<button class="bh-button" type="submit">{html.escape(ap.texto_boton())}</button>\n'
        f'<div class="bh-message" role="status" style="display:none"></div>\n'
        f'</form>\n'
        f'{submit_js}\n'
        f'{_PURE_STYLE_SUGGESTION}'
    )


# --- widget JS (vanilla, sin dependencias) ----------------------------------
# `__bhInit(cfg)` monta el comportamiento sobre un <form> ya presente
# (iframe) o renderizado por el boot (widget). Compacto para <15KB gzip.

_WIDGET_CORE_JS = r"""
window.__bhInit=function(cfg){
  var form=cfg.formEl,msg=cfg.msgEl;
  function show(cls,text){msg.style.display="block";msg.className="bh-msg "+cls;msg.textContent=text;}
  function meta(){
    var p=new URLSearchParams(window.location.search),d={};
    ["utm_source","utm_medium","utm_campaign"].forEach(function(k){if(p.get(k))d[k]=p.get(k);});
    d.referrer=document.referrer||"";d.landing_page=window.location.href;return d;
  }
  function token(cb){
    if(cfg.siteKey&&window.grecaptcha){
      grecaptcha.ready(function(){grecaptcha.execute(cfg.siteKey,{action:"submit"}).then(function(t){cb(t);});});
    }else{cb(null);}
  }
  form.addEventListener("submit",function(e){
    e.preventDefault();
    var btn=form.querySelector("button[type=submit]");if(btn)btn.disabled=true;
    var fd=new FormData(form),body=meta();
    fd.forEach(function(v,k){if(k.slice(-2)==="[]"){var b=k.slice(0,-2);(body[b]=body[b]||[]).push(v);}else{body[k]=v;}});
    token(function(t){
      if(t)body.recaptcha_token=t;
      fetch(cfg.apiBase+"/public/forms/"+cfg.formId+"/submit",{
        method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)
      }).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j};});})
      .then(function(res){
        if(btn)btn.disabled=false;
        if(res.ok&&res.j.success){
          if(res.j.action==="redirect"&&res.j.redirect_url){window.location.href=res.j.redirect_url;return;}
          form.reset();show("bh-ok",res.j.success_message||"¡Gracias! Hemos recibido tu solicitud.");
        }else{
          show("bh-err","No se pudo enviar. Revisa los datos e inténtalo de nuevo.");
        }
      }).catch(function(){if(btn)btn.disabled=false;show("bh-err","Error de conexión. Inténtalo de nuevo.");});
    });
  });
};
"""

_WIDGET_BOOT_JS = r"""
(function(){
  var FORM_ID=__FORM_ID__,API_BASE=__API_BASE__;
  var mount=document.querySelector('[data-bohub-form="'+FORM_ID+'"]');
  if(!mount){mount=document.createElement("div");mount.setAttribute("data-bohub-form",FORM_ID);
    if(document.currentScript&&document.currentScript.parentNode)document.currentScript.parentNode.insertBefore(mount,document.currentScript);}
  if(mount.getAttribute("data-bh-mounted"))return;mount.setAttribute("data-bh-mounted","1");
  var style=document.createElement("style");
  style.textContent='[data-bohub-form] *{box-sizing:border-box}[data-bohub-form] .bh-form{display:flex;flex-direction:column;gap:12px;max-width:520px}[data-bohub-form] .bh-field{display:flex;flex-direction:column;gap:4px}[data-bohub-form] .bh-field label{font-size:14px;font-weight:600}[data-bohub-form] .bh-field input,[data-bohub-form] .bh-field textarea,[data-bohub-form] .bh-field select{padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;font-family:inherit}[data-bohub-form] .bh-help{font-size:12px;color:#64748b}[data-bohub-form] .bh-req{color:#dc2626}[data-bohub-form] .bh-btn{padding:12px 16px;background:#2563eb;color:#fff;border:0;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}[data-bohub-form] .bh-msg{padding:14px;border-radius:8px;font-size:14px}[data-bohub-form] .bh-ok{background:#dcfce7;color:#166534}[data-bohub-form] .bh-err{background:#fee2e2;color:#991b1b}[data-bohub-form] .bh-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}[data-bohub-form] .bh-tags{display:flex;flex-direction:column;gap:4px}[data-bohub-form] .bh-check{font-weight:400;display:flex;align-items:center;gap:6px}[data-bohub-form] .bh-stars{display:inline-flex;flex-direction:row-reverse;gap:.15em;justify-content:flex-end}[data-bohub-form] .bh-stars input{position:absolute;opacity:0;pointer-events:none}[data-bohub-form] .bh-stars label{cursor:pointer;color:#d0d0d0;font-size:1.7rem;line-height:1}[data-bohub-form] .bh-stars label:hover,[data-bohub-form] .bh-stars label:hover ~ label,[data-bohub-form] .bh-stars input:checked ~ label{color:#f5b301}';
  document.head.appendChild(style);
  function esc(s){var d=document.createElement("div");d.textContent=s==null?"":s;return d.innerHTML.replace(/"/g,"&quot;").replace(/'/g,"&#39;");}
  function field(f){
    var dv=f.default_value||"";
    if(f.hidden||f.type==="hidden")return'<input type="hidden" name="'+esc(f.key)+'" value="'+esc(dv)+'">';
    var star=f.required?' <span class="bh-req">*</span>':"";
    var lab=f.label_html!=null?f.label_html:esc(f.label);
    var help=f.help_text?'<span class="bh-help">'+(f.help_html!=null?f.help_html:esc(f.help_text))+'</span>':"";
    var ctrl;
    if(f.type==="textarea")ctrl='<textarea name="'+esc(f.key)+'" placeholder="'+esc(f.placeholder)+'" rows="4"'+(f.required?" required":"")+'>'+esc(dv)+'</textarea>';
    else if(f.type==="select"){var o=(f.options||[]).map(function(x){return'<option value="'+esc(x.value)+'"'+(String(x.value)===dv?" selected":"")+'>'+esc(x.label)+'</option>';}).join("");ctrl='<select name="'+esc(f.key)+'"'+(f.required?" required":"")+'><option value="">—</option>'+o+'</select>';}
    else if(f.type==="checkbox")return'<div class="bh-field"><label><input type="checkbox" name="'+esc(f.key)+'"> '+lab+star+'</label>'+help+'</div>';
    else if(f.type==="tags"){var tb=(f.options||[]).map(function(o){return'<label class="bh-check"><input type="checkbox" name="'+esc(f.key)+'[]" value="'+esc(o.tag_id)+'"> '+esc(o.label)+'</label>';}).join("");return'<div class="bh-field"><label>'+lab+star+'</label><div class="bh-tags">'+tb+'</div>'+help+'</div>';}
    else if(f.type==="stars"){var sr="";[5,4,3,2,1].forEach(function(v){sr+='<input type="radio" name="'+esc(f.key)+'" value="'+v+'" id="'+esc(f.key)+'_'+v+'"><label for="'+esc(f.key)+'_'+v+'" aria-label="'+v+'">★</label>';});return'<div class="bh-field"><label>'+lab+star+'</label><div class="bh-stars">'+sr+'</div>'+help+'</div>';}
    else{var it=(f.type==="email"||f.type==="tel")?f.type:"text";ctrl='<input type="'+it+'" name="'+esc(f.key)+'" placeholder="'+esc(f.placeholder)+'"'+(f.required?" required":"")+(dv?' value="'+esc(dv)+'"':"")+'>';}
    return'<div class="bh-field"><label>'+lab+star+'</label>'+ctrl+help+'</div>';
  }
  fetch(API_BASE+"/public/forms/"+FORM_ID+"/config.json").then(function(r){return r.json().then(function(j){if(!r.ok){var e=new Error("bohub");e.d=(j&&j.detail)||{};throw e;}return j;});}).then(function(cfg){
    if(cfg.style_css){var st=document.createElement("style");st.textContent=cfg.style_css;document.head.appendChild(st);}
    var rows=(cfg.fields||[]).map(field).join("");
    mount.innerHTML='<form class="bh-form">'+rows+'<input class="bh-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true"><button class="bh-btn" type="submit">'+esc(cfg.submit_text||"Enviar")+'</button><div class="bh-msg" style="display:none"></div></form>';
    var formEl=mount.querySelector("form"),msgEl=mount.querySelector(".bh-msg");
    function boot(){window.__bhInit({formId:FORM_ID,apiBase:API_BASE,siteKey:cfg.recaptcha_site_key,formEl:formEl,msgEl:msgEl});}
    if(cfg.recaptcha_site_key&&!window.grecaptcha){var s=document.createElement("script");s.src="https://www.google.com/recaptcha/api.js?render="+cfg.recaptcha_site_key;s.onload=boot;document.head.appendChild(s);}else{boot();}
  }).catch(function(e){if(e&&e.d&&e.d.code==="form_inactive"){mount.setAttribute("data-bohub-error","form_inactive");if(window.console)console.warn("BoHub ("+FORM_ID+"): "+e.d.message);return;}mount.innerHTML='<p style="color:#991b1b">No se pudo cargar el formulario.</p>';});
})();
"""
