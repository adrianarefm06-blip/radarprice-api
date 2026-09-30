"""Páginas públicas en HTML (sin JS ni recursos externos): política de privacidad.

URL estable para las fichas de Google Play / App Store: {API}/privacidad
El texto coincide con la pantalla "Acerca de" de la app.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

_UPDATED = "30 de septiembre de 2026"

PRIVACY_HTML = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RadarPrice · Política de privacidad</title>
<style>
body{{font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:720px;margin:0 auto;
padding:24px 16px;color:#1b1d21;background:#fff}}
h1{{font-size:1.6rem}} h2{{font-size:1.15rem;margin-top:1.6em}} small{{color:#5f6570}}
@media (prefers-color-scheme:dark){{body{{color:#e6e8eb;background:#121417}} small{{color:#9aa1ab}}}}
</style></head><body>
<h1>Política de privacidad de RadarPrice</h1>
<small>Última actualización: {_UPDATED}</small>

<h2>Qué es RadarPrice</h2>
<p>RadarPrice es una app que compara precios de zapatillas por talla entre tiendas online y avisa cuando
un precio baja del objetivo elegido. No vende productos ni gestiona pedidos ni pagos.</p>

<h2>Datos que tratamos</h2>
<ul>
<li><strong>No hay cuentas</strong> ni pedimos nombre, correo, teléfono ni ubicación.</li>
<li><strong>Favoritos</strong>: se guardan solo en tu dispositivo; no se envían a nuestro servidor.</li>
<li><strong>Alertas de precio</strong>: se guardan en nuestro servidor (zapatilla, talla y precio objetivo)
asociadas a un identificador aleatorio generado en tu dispositivo, sin relación con tu identidad.
Se conservan hasta que borras la alerta. Al desinstalar la app el identificador se pierde y las alertas
dejan de estar asociadas a ningún dispositivo.</li>
<li><strong>Registros técnicos</strong>: el servidor puede registrar temporalmente datos técnicos de las
peticiones (como la dirección IP) para seguridad y resolución de errores.</li>
</ul>

<h2>Lo que no hacemos</h2>
<p>No usamos analítica, publicidad ni perfiles, y no vendemos ni cedemos datos a terceros.</p>

<h2>Servicios de terceros</h2>
<p>El servidor se aloja en Render (Unión Europea, Fráncfort). Las fotos de producto se cargan desde las
webs de las tiendas y marcas, que reciben la petición de la imagen. Al abrir una tienda desde la app sales de
RadarPrice y se aplica la política de privacidad de esa tienda.</p>

<h2>Tus derechos</h2>
<p>Puedes borrar tus alertas en cualquier momento desde la app, lo que las elimina del servidor.
Para cualquier consulta sobre privacidad, contacta con el responsable de la app a través de su ficha en la
tienda de aplicaciones.</p>

<h2>Precios</h2>
<p>Los precios son orientativos; confírmalos siempre en la tienda. Los marcados como «Est.» son estimados
(de demostración).</p>
</body></html>
"""


@router.get("/privacidad", response_class=HTMLResponse, include_in_schema=False)
async def privacy() -> HTMLResponse:
    return HTMLResponse(PRIVACY_HTML, headers={"Cache-Control": "public, max-age=3600"})
