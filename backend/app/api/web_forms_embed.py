"""Capa de embed PÚBLICA de formularios web (sin auth, CORS abierto).

  - GET /forms/{form_id}         → página HTML server-rendered (iframe),
                                   estilos propios BoHub, aislada.
  - GET /forms/embed/{form_id}.js → widget JS vanilla (<15KB, sin deps)
                                   que se inyecta en cualquier web host.

Ambos consumen la API pública (`/public/forms/{id}/config.json` +
`/submit`). El CORS `*` para el prefijo `/forms/` lo aplica el middleware
de `app.main`.
"""
from __future__ import annotations

import html
import json

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import not_found
from app.db.session import get_session
from app.models.web_forms import WebForm

router = APIRouter(tags=["web-forms-embed"])


def _get_active_form(session: Session, form_id: str) -> WebForm:
    form = session.get(WebForm, form_id)
    if form is None or not form.is_active:
        raise not_found("Form")
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
        })
    return out


@router.get("/forms/{form_id}", response_class=HTMLResponse)
def render_iframe(
    form_id: str, session: Session = Depends(get_session)
) -> HTMLResponse:
    """Página HTML autocontenida del form para iframe. Estilo propio
    BoHub (aislado de la web host)."""
    form = _get_active_form(session, form_id)
    settings = get_settings()
    site_key = settings.recaptcha_site_key if form.recaptcha_enabled else None
    fields = _field_config(form)
    api_base = _api_base()

    rows = "".join(_render_field_html(f) for f in fields if not f["hidden"])
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
</style>
</head>
<body>
<form class="bh-form" id="bh-form">
{rows}
<input class="bh-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
<button class="bh-btn" type="submit">Enviar</button>
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
</body>
</html>"""
    return HTMLResponse(content=page)


@router.get("/forms/embed/{form_id}.js")
def render_widget_js(
    form_id: str, session: Session = Depends(get_session)
) -> Response:
    """Widget JS vanilla que se auto-inyecta en la web host. Renderiza el
    form desde config.json, hereda estilos del host (reset mínimo),
    recopila UTM/referrer/landing y envía. Soporta varias instancias."""
    form = _get_active_form(session, form_id)
    api_base = _api_base()
    js = _WIDGET_CORE_JS + "\n" + _WIDGET_BOOT_JS.replace(
        "__FORM_ID__", json.dumps(form.id)
    ).replace("__API_BASE__", json.dumps(api_base))
    return Response(
        content=js,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _render_field_html(f: dict) -> str:
    key = html.escape(f["key"])
    label = html.escape(f["label"])
    ph = html.escape(f["placeholder"])
    req = ' required' if f["required"] else ""
    star = ' <span class="bh-req">*</span>' if f["required"] else ""
    help_html = f'<span class="bh-help">{html.escape(f["help_text"])}</span>' if f["help_text"] else ""
    if f["type"] == "textarea":
        control = f'<textarea name="{key}" placeholder="{ph}"{req} rows="4"></textarea>'
    elif f["type"] == "select":
        opts = "".join(
            f'<option value="{html.escape(str(o.get("value","")))}">'
            f'{html.escape(str(o.get("label","")))}</option>'
            for o in f["options"]
        )
        control = f'<select name="{key}"{req}><option value="">—</option>{opts}</select>'
    elif f["type"] == "checkbox":
        return (
            f'<div class="bh-field"><label>'
            f'<input type="checkbox" name="{key}"> {label}{star}</label>{help_html}</div>'
        )
    else:
        itype = html.escape(f["type"]) if f["type"] in {"email", "tel"} else "text"
        control = f'<input type="{itype}" name="{key}" placeholder="{ph}"{req}>'
    return (
        f'<div class="bh-field"><label>{label}{star}</label>'
        f'{control}{help_html}</div>'
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
    fd.forEach(function(v,k){body[k]=v;});
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
  style.textContent='[data-bohub-form] *{box-sizing:border-box}[data-bohub-form] .bh-form{display:flex;flex-direction:column;gap:12px;max-width:520px}[data-bohub-form] .bh-field{display:flex;flex-direction:column;gap:4px}[data-bohub-form] .bh-field label{font-size:14px;font-weight:600}[data-bohub-form] .bh-field input,[data-bohub-form] .bh-field textarea,[data-bohub-form] .bh-field select{padding:10px 12px;border:1px solid #cbd5e1;border-radius:8px;font-size:14px;font-family:inherit}[data-bohub-form] .bh-help{font-size:12px;color:#64748b}[data-bohub-form] .bh-req{color:#dc2626}[data-bohub-form] .bh-btn{padding:12px 16px;background:#2563eb;color:#fff;border:0;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}[data-bohub-form] .bh-msg{padding:14px;border-radius:8px;font-size:14px}[data-bohub-form] .bh-ok{background:#dcfce7;color:#166534}[data-bohub-form] .bh-err{background:#fee2e2;color:#991b1b}[data-bohub-form] .bh-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}';
  document.head.appendChild(style);
  function esc(s){var d=document.createElement("div");d.textContent=s==null?"":s;return d.innerHTML;}
  function field(f){
    if(f.hidden)return"";
    var star=f.required?' <span class="bh-req">*</span>':"";
    var help=f.help_text?'<span class="bh-help">'+esc(f.help_text)+'</span>':"";
    var ctrl;
    if(f.type==="textarea")ctrl='<textarea name="'+esc(f.key)+'" placeholder="'+esc(f.placeholder)+'" rows="4"'+(f.required?" required":"")+'></textarea>';
    else if(f.type==="select"){var o=(f.options||[]).map(function(x){return'<option value="'+esc(x.value)+'">'+esc(x.label)+'</option>';}).join("");ctrl='<select name="'+esc(f.key)+'"'+(f.required?" required":"")+'><option value="">—</option>'+o+'</select>';}
    else if(f.type==="checkbox")return'<div class="bh-field"><label><input type="checkbox" name="'+esc(f.key)+'"> '+esc(f.label)+star+'</label>'+help+'</div>';
    else{var it=(f.type==="email"||f.type==="tel")?f.type:"text";ctrl='<input type="'+it+'" name="'+esc(f.key)+'" placeholder="'+esc(f.placeholder)+'"'+(f.required?" required":"")+'>';}
    return'<div class="bh-field"><label>'+esc(f.label)+star+'</label>'+ctrl+help+'</div>';
  }
  fetch(API_BASE+"/public/forms/"+FORM_ID+"/config.json").then(function(r){return r.json();}).then(function(cfg){
    var rows=(cfg.fields||[]).map(field).join("");
    mount.innerHTML='<form class="bh-form">'+rows+'<input class="bh-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true"><button class="bh-btn" type="submit">Enviar</button><div class="bh-msg" style="display:none"></div></form>';
    var formEl=mount.querySelector("form"),msgEl=mount.querySelector(".bh-msg");
    function boot(){window.__bhInit({formId:FORM_ID,apiBase:API_BASE,siteKey:cfg.recaptcha_site_key,formEl:formEl,msgEl:msgEl});}
    if(cfg.recaptcha_site_key&&!window.grecaptcha){var s=document.createElement("script");s.src="https://www.google.com/recaptcha/api.js?render="+cfg.recaptcha_site_key;s.onload=boot;document.head.appendChild(s);}else{boot();}
  }).catch(function(){mount.innerHTML='<p style="color:#991b1b">No se pudo cargar el formulario.</p>';});
})();
"""
