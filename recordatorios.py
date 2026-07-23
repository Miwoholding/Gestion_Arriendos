"""
Envía un mail diario de recordatorio de pago a los arrendatarios cuya
propiedad ya alcanzó el día de vencimiento del mes y todavía no tiene
registrado el pago correspondiente. Deja de enviarse en cuanto se
registra el pago del mes para esa propiedad.

Pensado para ejecutarse una vez al día vía launchd/cron
(ver com.inmobiliariacm.recordatorios.plist).
"""
import os
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

# ── Cargar variables de .env (mismo patrón que app.py) ──────────────────────
_env_file = BASE / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from models import Base, RecordatorioEnviado, propiedades_pendientes_recordatorio

MESES = ["", "enero","febrero","marzo","abril","mayo","junio","julio",
          "agosto","septiembre","octubre","noviembre","diciembre"]

DB_PATH = BASE / "gestion_arriendos.db"
LOG_FILE = BASE / "logs" / "recordatorios.log"

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Inmobiliaria CM")
MAIL_BCC = os.environ.get("MAIL_BCC")  # opcional: copia interna de control


def _log(msg: str):
    ts = date.today().isoformat()
    linea = f"[{ts}] {msg}"
    print(linea)
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def _fmt_pesos(v):
    if v is None:
        return "-"
    return f"${v:,.0f}".replace(",", ".")


CMF_API_KEY = os.environ.get("CMF_API_KEY")


def _parse_num_cl(s: str) -> float:
    """'40.844,79' -> 40844.79"""
    return float(s.replace(".", "").replace(",", "."))


def _get_uf_hoy():
    """Valor de la UF de hoy. Primero la API oficial de la CMF (requiere
    CMF_API_KEY); si falla o no hay key, cae a mindicador.cl."""
    import urllib.request, json, certifi
    ctx = ssl.create_default_context(cafile=certifi.where())

    if CMF_API_KEY:
        try:
            req = urllib.request.Request(
                f"https://api.cmfchile.cl/api-sbifv3/recursos_api/uf?apikey={CMF_API_KEY}&formato=json",
                headers={"User-Agent": "gestion-arriendos/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                data = json.loads(r.read())
            return _parse_num_cl(data["UFs"][0]["Valor"])
        except Exception:
            pass

    try:
        req = urllib.request.Request(
            "https://mindicador.cl/api/uf",
            headers={"User-Agent": "gestion-arriendos/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            data = json.loads(r.read())
        return float(data["serie"][0]["valor"])
    except Exception:
        return None


def construir_mensaje(nombre_arrendatario, direccion, mes, año, valor_uf, dia_venc, valor_pesos, uf_dia=None):
    mes_nombre = MESES[mes]
    asunto = f"Recordatorio de pago de arriendo – {direccion} – {mes_nombre} {año}"

    monto_uf_str = f"{valor_uf:.2f} UF" if valor_uf else "UF pactada en el contrato"
    uf_dia_linea = f"Valor UF del día: {_fmt_pesos(uf_dia)}\n" if uf_dia else ""
    monto_pesos_linea = f"Equivalente aproximado hoy: {_fmt_pesos(valor_pesos)}\n" if valor_pesos else ""

    cuerpo = f"""Estimado(a) {nombre_arrendatario},

Le recordamos que el pago del arriendo de la propiedad ubicada en:

  {direccion}

correspondiente a {mes_nombre} {año}, vence el día {dia_venc} de cada mes y aún no
registramos su pago.

Monto pactado: {monto_uf_str}
{uf_dia_linea}{monto_pesos_linea}
Si ya realizó el pago, por favor descuide este mensaje e infórmenos para
regularizar el registro. De lo contrario, le agradecemos regularizarlo a la
brevedad.

Saludos cordiales,
{MAIL_FROM_NAME}
"""
    return asunto, cuerpo


def enviar_mail(destinatario, asunto, cuerpo):
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM}>"
    msg["To"] = destinatario
    if MAIL_BCC:
        msg["Bcc"] = MAIL_BCC
    msg.set_content(cuerpo)

    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def main():
    if not SMTP_USER or not SMTP_PASS:
        _log("ERROR: faltan SMTP_USER / SMTP_PASS en .env. No se enviaron recordatorios.")
        return 1

    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[RecordatorioEnviado.__table__])
    hoy = date.today()
    uf_hoy = _get_uf_hoy()
    enviados, fallidos = 0, 0

    with Session(engine) as s:
        pendientes = propiedades_pendientes_recordatorio(s, hoy)
        if not pendientes:
            _log("Sin propiedades pendientes de recordatorio hoy.")
            return 0

        for prop, arr in pendientes:
            valor_pesos = (prop.valor_arriendo_uf * uf_hoy) if (prop.valor_arriendo_uf and uf_hoy) else None
            asunto, cuerpo = construir_mensaje(
                nombre_arrendatario=arr.nombre_arrendatario or "arrendatario",
                direccion=prop.direccion_propiedad,
                mes=hoy.month, año=hoy.year,
                valor_uf=prop.valor_arriendo_uf,
                dia_venc=prop.dia_vencimiento,
                valor_pesos=valor_pesos,
                uf_dia=uf_hoy,
            )
            registro = RecordatorioEnviado(
                mes=hoy.month, año=hoy.year,
                id_propiedad=prop.id_propiedad,
                direccion=prop.direccion_propiedad,
                id_arrendatario=arr.id_arrendatario,
                mail=arr.mail,
                valor_uf=uf_hoy,
                valor_pesos=valor_pesos,
            )
            try:
                enviar_mail(arr.mail, asunto, cuerpo)
                enviados += 1
                registro.exito = 1
                _log(f"Enviado a {arr.mail} — {prop.direccion_propiedad}")
            except Exception as e:
                fallidos += 1
                registro.exito = 0
                registro.error = str(e)[:300]
                _log(f"ERROR enviando a {arr.mail} — {prop.direccion_propiedad}: {e}")
            s.add(registro)
        s.commit()

    _log(f"Resumen: {enviados} enviados, {fallidos} fallidos.")
    return 0


def test(destinatario: str):
    """Envía un correo de ejemplo a `destinatario` sin tocar la base de datos,
    para verificar credenciales SMTP y el formato del mensaje."""
    if not SMTP_USER or not SMTP_PASS:
        _log("ERROR: faltan SMTP_USER / SMTP_PASS en .env.")
        return 1
    hoy = date.today()
    uf_hoy = _get_uf_hoy()
    valor_uf = 12.5
    asunto, cuerpo = construir_mensaje(
        nombre_arrendatario="Arrendatario de prueba",
        direccion="Dirección de ejemplo 123",
        mes=hoy.month, año=hoy.year,
        valor_uf=valor_uf,
        dia_venc=5,
        valor_pesos=(valor_uf * uf_hoy) if uf_hoy else None,
        uf_dia=uf_hoy,
    )
    asunto = "[PRUEBA] " + asunto
    try:
        enviar_mail(destinatario, asunto, cuerpo)
        _log(f"Prueba enviada a {destinatario}")
        return 0
    except Exception as e:
        _log(f"ERROR en envío de prueba a {destinatario}: {e}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        sys.exit(test(sys.argv[2]))
    sys.exit(main())
