import os
import functools
from datetime import date, datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func, create_engine, event, text
from sqlalchemy.orm import Session
from models import Base, Arrendatario, Propiedad, Pago, Gasto, ItemGasto, Usuario, RolUsuario, EstadoPropiedad, TipoPropiedad, FormaPago

# Cargar .env si existe
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

DB_PATH       = Path(__file__).parent / "gestion_arriendos.db"
CONTRATOS_DIR = Path(__file__).parent / "contratos"
CONTRATOS_DIR.mkdir(exist_ok=True)
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")   # lecturas sin bloquear escrituras
    cur.execute("PRAGMA busy_timeout=5000")  # espera hasta 5s antes de error
    cur.execute("PRAGMA synchronous=NORMAL") # balance entre velocidad y seguridad
    cur.close()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "inmobiliaria_cm_s3cr3t_2024!")

# ── Decoradores de acceso ──────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("id_usuario"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("id_usuario"):
            return redirect(url_for("login"))
        if session.get("rol") != "admin":
            flash("Acceso restringido a administradores.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

def es_admin():
    return session.get("rol") == "admin"

# ── Catálogo de permisos disponibles ──────────────────────────────────────────
PERMISOS_CATALOGO = {
    "ver_propiedades":   "Ver propiedades",
    "ver_arrendatarios": "Ver arrendatarios",
    "ver_pagos":         "Ver pagos",
    "registrar_pagos":   "Registrar pagos",
    "ver_gastos":        "Ver gastos comunes",
    "registrar_gastos":  "Registrar gastos comunes",
    "ver_consultas":     "Ver consultas y reportes",
    "ver_prorrateo":     "Ver prorrateo",
}

# Permisos que se dan por defecto al crear un usuario nuevo
PERMISOS_DEFAULT = ",".join(PERMISOS_CATALOGO.keys())

def tiene_permiso(permiso: str) -> bool:
    """Admin tiene todos los permisos; usuario sólo los asignados."""
    if session.get("rol") == "admin":
        return True
    permisos = session.get("permisos", "")
    return permiso in permisos.split(",")

def permiso_required(permiso: str):
    """Decorador: exige un permiso concreto (admin lo pasa siempre)."""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("id_usuario"):
                return redirect(url_for("login"))
            if not tiene_permiso(permiso):
                flash("No tienes permiso para acceder a esta sección.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator

# Exponer helpers en todos los templates
@app.context_processor
def inject_globals():
    return {
        "es_admin":       es_admin(),
        "rol_usuario":    session.get("rol", ""),
        "tiene_permiso":  tiene_permiso,
    }

MESES = {1:"Enero",2:"Febrero",3:"Marzo",4:"Abril",5:"Mayo",6:"Junio",
          7:"Julio",8:"Agosto",9:"Septiembre",10:"Octubre",11:"Noviembre",12:"Diciembre"}

# ── Filtros de formato chileno (punto miles, coma decimal) ─────────────────────
def _fmt_pesos(v):
    """$1.234.567  —  devuelve '—' si es None."""
    if v is None:
        return "—"
    return "$" + f"{int(round(v)):,}".replace(",", ".")

def _fmt_uf(v, dec=2):
    """12,34  /  1.234,56  —  devuelve '—' si es None."""
    if v is None:
        return "—"
    s = f"{float(v):,.{dec}f}"          # e.g. "1,234.56"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")  # "1.234,56"

def _fmt_fecha(v):
    """dd-mm-yyyy desde date, datetime o string 'yyyy-mm-dd'. Devuelve '—' si es None."""
    if v is None:
        return "—"
    if hasattr(v, "strftime"):
        return v.strftime("%d-%m-%Y")
    s = str(v)[:10]          # tomar sólo la parte de fecha
    parts = s.split("-")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return s

def _fmt_fecha_input(v):
    """yyyy-mm-dd → dd/mm/aaaa para campos <input>. Devuelve '' si es None."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    s = str(v)[:10]
    parts = s.split("-")
    if len(parts) == 3:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return s

app.jinja_env.filters["cl_pesos"]       = _fmt_pesos
app.jinja_env.filters["cl_uf"]          = _fmt_uf
app.jinja_env.filters["cl_fecha"]       = _fmt_fecha
app.jinja_env.filters["cl_fecha_input"] = _fmt_fecha_input

# ── Helpers ────────────────────────────────────────────────────────────────────
def _str(v): return v.strip() if v and v.strip() else None
def _float(v):
    try: return float(v) if v else None
    except: return None
def _int(v):
    try: return int(v) if v else None
    except: return None
def _date(v):
    if not v: return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try: return datetime.strptime(v.strip(), fmt).date()
        except: pass
    return None

def _enum_val(col):
    return col.value if col else None

# ── Autenticación ─────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("id_usuario"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pwd      = request.form.get("password", "")
        with Session(engine) as s:
            u = s.query(Usuario).filter(
                Usuario.username == username,
                Usuario.activo   == 1
            ).first()
            if u and check_password_hash(u.password_hash, pwd):
                session["id_usuario"] = u.id_usuario
                session["username"]   = u.username
                session["nombre"]     = u.nombre or u.username
                session["rol"]        = u.rol.value
                session["permisos"]   = u.permisos or ""
                session.permanent     = False
                return redirect(url_for("dashboard"))
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("login"))

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    with Session(engine) as s:
        año_actual = date.today().year
        stats = {
            "propiedades":   s.query(Propiedad).count(),
            "arrendadas":    s.query(Propiedad).filter(Propiedad.estado == EstadoPropiedad.ARRENDADA).count(),
            "arrendatarios": s.query(Arrendatario).count(),
            "pagos_año":     s.query(Pago).filter(Pago.año == año_actual).count(),
        }
        pagos_mes = (
            s.query(Pago.mes,
                    func.sum(Pago.valor_arriendo_uf).label("total_uf"),
                    func.count(Pago.id_pago).label("cantidad"))
            .filter(Pago.año == año_actual, Pago.mes != None)
            .group_by(Pago.mes).order_by(Pago.mes).all()
        )
        ultimos = (
            s.query(Pago, Propiedad.direccion_propiedad)
            .outerjoin(Propiedad, Pago.id_propiedad == Propiedad.id_propiedad)
            .order_by(Pago.fecha_pago.desc()).limit(8).all()
        )
        ultimos_pagos = [
            {"fecha_pago": p.fecha_pago, "direccion": d, "valor_arriendo_uf": p.valor_arriendo_uf}
            for p, d in ultimos
        ]
    return render_template("dashboard.html", stats=stats, pagos_mes=pagos_mes,
                           ultimos_pagos=ultimos_pagos, año_actual=año_actual, meses=MESES)

# ── Propiedades ───────────────────────────────────────────────────────────────
@app.route("/propiedades")
@login_required
@permiso_required("ver_propiedades")
def propiedades():
    id_propiedad = request.args.get("id_propiedad", "")
    estado       = request.args.get("estado", "")
    tipo         = request.args.get("tipo", "")
    q            = request.args.get("q", "")
    with Session(engine) as s:
        q2 = (s.query(Propiedad, Arrendatario.nombre_arrendatario)
              .outerjoin(Arrendatario, Propiedad.id_arrendatario == Arrendatario.id_arrendatario))
        if id_propiedad: q2 = q2.filter(Propiedad.id_propiedad == int(id_propiedad))
        if estado:       q2 = q2.filter(Propiedad.estado == estado)
        if tipo:         q2 = q2.filter(Propiedad.tipo_propiedad == tipo)
        if q:
            q2 = q2.filter(Arrendatario.nombre_arrendatario.ilike(f"%{q}%"))
        rows  = q2.order_by(Propiedad.direccion_propiedad).all()
        tipos = [t[0] for t in s.query(Propiedad.tipo_propiedad).distinct().all() if t[0]]
        todas = (s.query(Propiedad.id_propiedad, Propiedad.direccion_propiedad)
                  .order_by(Propiedad.direccion_propiedad).all())
        result = []
        for p, nombre in rows:
            d = {c.key: getattr(p, c.key) for c in p.__mapper__.columns}
            d["nombre_arrendatario"] = nombre
            d["estado"]         = _enum_val(p.estado)
            d["tipo_propiedad"] = _enum_val(p.tipo_propiedad)
            result.append(d)
    return render_template("propiedades.html", propiedades=result, tipos=tipos,
                           todas=todas, id_propiedad=id_propiedad,
                           estado=estado, tipo=tipo, q=q)

@app.route("/propiedades/nueva", methods=["GET","POST"])
@admin_required
def propiedad_nueva():
    with Session(engine) as s:
        arrendatarios = s.query(Arrendatario).order_by(Arrendatario.nombre_arrendatario).all()
        if request.method == "POST":
            p = Propiedad(
                direccion_propiedad  = _str(request.form.get("direccion")),
                tipo_propiedad       = TipoPropiedad(request.form.get("tipo")) if request.form.get("tipo") else None,
                estado               = EstadoPropiedad(request.form.get("estado")) if request.form.get("estado") else EstadoPropiedad.DISPONIBLE,
                rol                  = _str(request.form.get("rol")),
                metros_propiedad     = _float(request.form.get("metros_propiedad")),
                metros_terreno       = _float(request.form.get("metros_terreno")),
                valor_arriendo_uf    = _float(request.form.get("valor_arriendo_uf")),
                fecha_contrato       = _date(request.form.get("fecha_contrato")),
                duracion_contrato    = _int(request.form.get("duracion_contrato")),
                id_arrendatario      = _int(request.form.get("id_arrendatario")),
                paga_gastos_comunes  = 1 if request.form.get("paga_gastos_comunes") else 0,
            )
            s.add(p); s.commit()
            flash("Propiedad creada correctamente.", "success")
            return redirect(url_for("propiedades"))
    return render_template("form_propiedad.html", propiedad=None, arrendatarios=arrendatarios)

@app.route("/propiedades/<int:id>/editar", methods=["GET","POST"])
@admin_required
def propiedad_editar(id):
    with Session(engine) as s:
        p = s.get(Propiedad, id)
        arrendatarios = s.query(Arrendatario).order_by(Arrendatario.nombre_arrendatario).all()
        if not p:
            flash("Propiedad no encontrada.", "danger"); return redirect(url_for("propiedades"))
        if request.method == "POST":
            p.direccion_propiedad = _str(request.form.get("direccion"))
            p.tipo_propiedad      = TipoPropiedad(request.form.get("tipo")) if request.form.get("tipo") else None
            p.estado               = EstadoPropiedad(request.form.get("estado")) if request.form.get("estado") else None
            p.rol                  = _str(request.form.get("rol"))
            p.metros_propiedad     = _float(request.form.get("metros_propiedad"))
            p.metros_terreno       = _float(request.form.get("metros_terreno"))
            p.valor_arriendo_uf    = _float(request.form.get("valor_arriendo_uf"))
            p.fecha_contrato       = _date(request.form.get("fecha_contrato"))
            p.duracion_contrato    = _int(request.form.get("duracion_contrato"))
            p.id_arrendatario      = _int(request.form.get("id_arrendatario"))
            p.paga_gastos_comunes  = 1 if request.form.get("paga_gastos_comunes") else 0
            s.commit()
            flash("Propiedad actualizada.", "success")
            return redirect(url_for("propiedades"))
        data = {c.key: getattr(p, c.key) for c in p.__mapper__.columns}
        data["tipo_propiedad"] = _enum_val(p.tipo_propiedad)
        data["estado"]         = _enum_val(p.estado)
    return render_template("form_propiedad.html", propiedad=data, arrendatarios=arrendatarios)

@app.route("/propiedades/<int:id>/eliminar")
@admin_required
def propiedad_eliminar(id):
    with Session(engine) as s:
        p = s.get(Propiedad, id)
        if p:
            # Preservar los pagos como ingresos históricos al eliminar la propiedad.
            s.query(Pago).filter(Pago.id_propiedad == id).update({Pago.id_propiedad: None})
            s.delete(p)
            s.commit()
            flash("Propiedad eliminada. Los pagos históricos se preservan sin propiedad asignada.", "warning")
    return redirect(url_for("propiedades"))

# ── Arrendatarios ─────────────────────────────────────────────────────────────
@app.route("/arrendatarios")
@login_required
@permiso_required("ver_arrendatarios")
def arrendatarios():
    q = request.args.get("q","")
    with Session(engine) as s:
        q2 = (s.query(Arrendatario, func.count(Propiedad.id_propiedad).label("num_propiedades"))
              .outerjoin(Propiedad, Arrendatario.id_arrendatario == Propiedad.id_arrendatario)
              .group_by(Arrendatario.id_arrendatario))
        if q:
            q2 = q2.filter(Arrendatario.nombre_arrendatario.ilike(f"%{q}%") |
                           Arrendatario.rut_arrendatario.ilike(f"%{q}%"))
        rows = q2.order_by(Arrendatario.nombre_arrendatario).all()
        result = [{**{c.key: getattr(a, c.key) for c in a.__mapper__.columns}, "num_propiedades": n}
                  for a, n in rows]
    return render_template("arrendatarios.html", arrendatarios=result, q=q)

@app.route("/arrendatarios/nuevo", methods=["GET","POST"])
@admin_required
def arrendatario_nuevo():
    if request.method == "POST":
        with Session(engine) as s:
            a = Arrendatario(
                nombre_arrendatario    = _str(request.form.get("nombre")),
                rut_arrendatario       = _str(request.form.get("rut")),
                contacto               = _str(request.form.get("contacto")),
                telefono               = _str(request.form.get("telefono")),
                mail                   = _str(request.form.get("mail")),
                actividad_arrendatario = _str(request.form.get("actividad")),
            )
            s.add(a); s.commit()
            flash("Arrendatario creado correctamente.", "success")
        return redirect(url_for("arrendatarios"))
    return render_template("form_arrendatario.html", arrendatario=None)

@app.route("/arrendatarios/<int:id>/editar", methods=["GET","POST"])
@admin_required
def arrendatario_editar(id):
    with Session(engine) as s:
        a = s.get(Arrendatario, id)
        if not a:
            flash("Arrendatario no encontrado.", "danger"); return redirect(url_for("arrendatarios"))
        if request.method == "POST":
            a.nombre_arrendatario    = _str(request.form.get("nombre"))
            a.rut_arrendatario       = _str(request.form.get("rut"))
            a.contacto               = _str(request.form.get("contacto"))
            a.telefono               = _str(request.form.get("telefono"))
            a.mail                   = _str(request.form.get("mail"))
            a.actividad_arrendatario = _str(request.form.get("actividad"))
            s.commit()
            flash("Arrendatario actualizado.", "success")
            return redirect(url_for("arrendatarios"))
        data = {c.key: getattr(a, c.key) for c in a.__mapper__.columns}
    return render_template("form_arrendatario.html", arrendatario=data)

@app.route("/arrendatarios/<int:id>/eliminar")
@admin_required
def arrendatario_eliminar(id):
    with Session(engine) as s:
        a = s.get(Arrendatario, id)
        if a: s.delete(a); s.commit()
        flash("Arrendatario eliminado.", "warning")
    return redirect(url_for("arrendatarios"))

# ── Pagos ─────────────────────────────────────────────────────────────────────
@app.route("/pagos")
@login_required
@permiso_required("ver_pagos")
def pagos():
    mes          = request.args.get("mes", "")
    año          = request.args.get("año", "")
    id_propiedad = request.args.get("id_propiedad", "")
    with Session(engine) as s:
        q2 = (s.query(Pago, Propiedad.direccion_propiedad, Arrendatario.nombre_arrendatario)
              .outerjoin(Propiedad,    Pago.id_propiedad == Propiedad.id_propiedad)
              .outerjoin(Arrendatario, Propiedad.id_arrendatario == Arrendatario.id_arrendatario))
        if mes:          q2 = q2.filter(func.strftime('%m', Pago.fecha_pago) == f"{int(mes):02d}")
        if año:          q2 = q2.filter(func.strftime('%Y', Pago.fecha_pago) == str(año))
        if id_propiedad: q2 = q2.filter(Pago.id_propiedad == int(id_propiedad))
        rows = q2.order_by(Pago.fecha_pago.desc()).all()
        años = sorted({int(r[0]) for r in s.query(func.strftime('%Y', Pago.fecha_pago))
                       .filter(Pago.fecha_pago != None).distinct().all()}, reverse=True)
        propiedades = (s.query(Propiedad)
                       .order_by(Propiedad.direccion_propiedad).all())
        props_data = [{c.key: getattr(p, c.key) for c in p.__mapper__.columns}
                      for p in propiedades]
        result = []
        for p, dir_, arr in rows:
            d = {c.key: getattr(p, c.key) for c in p.__mapper__.columns}
            d["direccion"]    = dir_
            d["arrendatario"] = arr
            d["forma_pago"]   = _enum_val(p.forma_pago)
            result.append(d)
        total_uf    = sum(r["valor_arriendo_uf"] or 0 for r in result)
        total_pesos = sum(r["valor_arriendo"]    or 0 for r in result)
    return render_template("pagos.html", pagos=result, meses=MESES, años=años,
                           mes=mes, año=año, id_propiedad=id_propiedad,
                           propiedades=props_data,
                           total_uf=total_uf, total_pesos=total_pesos)

@app.route("/pagos/nuevo", methods=["GET","POST"])
@login_required
@permiso_required("registrar_pagos")
def pago_nuevo():
    with Session(engine) as s:
        props = (s.query(Propiedad)
                 .filter(Propiedad.estado == EstadoPropiedad.ARRENDADA)
                 .order_by(Propiedad.direccion_propiedad).all())
        props_data = [{c.key: getattr(p, c.key) for c in p.__mapper__.columns} for p in props]
        # Arrendatarios con al menos una propiedad arrendada
        arrs = (s.query(Arrendatario)
                .join(Propiedad, Arrendatario.id_arrendatario == Propiedad.id_arrendatario)
                .filter(Propiedad.estado == EstadoPropiedad.ARRENDADA)
                .distinct().order_by(Arrendatario.nombre_arrendatario).all())
        arrs_data = [{c.key: getattr(a, c.key) for c in a.__mapper__.columns} for a in arrs]
        # Si viene con ?prop=X, pre-seleccionar el arrendatario correspondiente
        presel = _int(request.args.get("prop"))
        presel_arr = None
        if presel:
            prop_obj = s.get(Propiedad, presel)
            if prop_obj:
                presel_arr = prop_obj.id_arrendatario
        # Calcular total m² de propiedades con GC para ponderaciones
        total_m2_gc = s.query(func.sum(Propiedad.metros_propiedad))\
                       .filter(Propiedad.paga_gastos_comunes == 1,
                               Propiedad.metros_propiedad != None).scalar() or 0
        # GC total por mes-año disponibles
        gc_por_mes = {}
        for row in s.query(Gasto.mes, Gasto.año, func.sum(Gasto.monto))\
                     .filter(Gasto.monto != None)\
                     .group_by(Gasto.mes, Gasto.año).all():
            if row[0] and row[1]:
                gc_por_mes[f"{row[1]}-{row[0]:02d}"] = round(row[2])

        if request.method == "POST":
            fecha = _date(request.form.get("fecha_pago"))
            uf    = _float(request.form.get("valor_uf"))
            arr   = _float(request.form.get("valor_arriendo_uf"))
            pesos = _float(request.form.get("valor_arriendo")) or (round(uf*arr) if uf and arr else None)
            pg = Pago(
                id_propiedad      = _int(request.form.get("id_propiedad")),
                fecha_pago        = fecha,
                mes               = _int(request.form.get("mes")),
                año               = _int(request.form.get("año")),
                valor_uf          = uf,
                valor_arriendo_uf = arr,
                valor_arriendo    = pesos,
                gasto_comun       = _float(request.form.get("gasto_comun")),
                descuento         = _float(request.form.get("descuento")),
                publicidad        = _float(request.form.get("publicidad")),
                secretaria        = _float(request.form.get("secretaria")),
                esterilizacion    = _float(request.form.get("esterilizacion")),
                otro              = _float(request.form.get("otro")),
                observaciones     = _str(request.form.get("observaciones")),
                obs_descuento     = _str(request.form.get("obs_descuento")),
                factura           = _str(request.form.get("factura")),
                forma_pago        = FormaPago(request.form.get("forma_pago")) if request.form.get("forma_pago") else None,
            )
            s.add(pg); s.commit()
            flash("Pago registrado correctamente.", "success")
            return redirect(url_for("pagos"))
    import json
    hoy = date.today().strftime("%d/%m/%Y")
    return render_template("form_pago.html", pago=None, propiedades=props_data,
                           arrendatarios=arrs_data,
                           meses=MESES, hoy=hoy, mes_actual=date.today().month,
                           año_actual=date.today().year, presel=presel, presel_arr=presel_arr,
                           total_m2_gc=total_m2_gc,
                           gc_por_mes=json.dumps(gc_por_mes))

@app.route("/pagos/<int:id>/editar")
@login_required
@permiso_required("ver_pagos")
def pago_editar(id):
    """Los pagos registrados son inmutables — sólo se pueden ver o eliminar."""
    with Session(engine) as s:
        pg = s.get(Pago, id)
        if not pg:
            flash("Pago no encontrado.", "danger")
            return redirect(url_for("pagos"))
        props = s.query(Propiedad).order_by(Propiedad.direccion_propiedad).all()
        props_data = [{c.key: getattr(p, c.key) for c in p.__mapper__.columns} for p in props]
        arrs = s.query(Arrendatario).order_by(Arrendatario.nombre_arrendatario).all()
        arrs_data = [{c.key: getattr(a, c.key) for c in a.__mapper__.columns} for a in arrs]
        data = {c.key: getattr(pg, c.key) for c in pg.__mapper__.columns}
        data["forma_pago"] = _enum_val(pg.forma_pago)
        # Total a pagar calculado
        arr_p  = pg.valor_arriendo  or 0
        gc_p   = pg.gasto_comun    or 0
        desc   = pg.descuento      or 0
        pub    = pg.publicidad     or 0
        sec    = pg.secretaria     or 0
        est    = pg.esterilizacion or 0
        otr    = pg.otro           or 0
        data["total_pago"] = round(arr_p + gc_p + pub + sec + est + otr - desc)
        presel_arr = None
        if pg.id_propiedad:
            prop_obj = s.get(Propiedad, pg.id_propiedad)
            if prop_obj:
                presel_arr = prop_obj.id_arrendatario
    import json
    hoy = date.today().strftime("%d/%m/%Y")
    return render_template("form_pago.html", pago=data, propiedades=props_data,
                           arrendatarios=arrs_data,
                           meses=MESES, hoy=hoy, mes_actual=date.today().month,
                           año_actual=date.today().year, presel=None,
                           presel_arr=presel_arr, solo_lectura=True,
                           total_m2_gc=0, gc_por_mes=json.dumps({}))

@app.route("/pagos/<int:id>/eliminar")
@admin_required
def pago_eliminar(id):
    with Session(engine) as s:
        pg = s.get(Pago, id)
        if pg: s.delete(pg); s.commit()
        flash("Pago eliminado.", "warning")
    return redirect(url_for("pagos"))

# ── Contratos (PDF) ───────────────────────────────────────────────────────────
@app.route("/contratos/<path:filename>")
@login_required
def ver_contrato(filename):
    """Sirve el PDF del contrato para visualización en el navegador."""
    filepath = CONTRATOS_DIR / filename
    if not filepath.exists():
        abort(404)
    return send_from_directory(CONTRATOS_DIR, filename,
                               mimetype="application/pdf")

@app.route("/propiedades/<int:id>/subir-contrato", methods=["POST"])
@admin_required
def subir_contrato(id):
    """Recibe el PDF, lo guarda y actualiza la propiedad."""
    archivo = request.files.get("contrato")
    if not archivo or archivo.filename == "":
        flash("No se seleccionó ningún archivo.", "warning")
        return redirect(url_for("propiedad_editar", id=id))
    if not archivo.filename.lower().endswith(".pdf"):
        flash("Solo se aceptan archivos PDF.", "danger")
        return redirect(url_for("propiedad_editar", id=id))

    # Nombre seguro: contrato_<id>_<nombre_original>
    nombre = f"contrato_{id}_{secure_filename(archivo.filename)}"
    archivo.save(CONTRATOS_DIR / nombre)

    with Session(engine) as s:
        p = s.get(Propiedad, id)
        if p:
            p.copia_contrato = nombre
            s.commit()
    flash("Contrato subido correctamente.", "success")
    return redirect(url_for("propiedad_editar", id=id))

@app.route("/propiedades/<int:id>/eliminar-contrato", methods=["POST"])
@admin_required
def eliminar_contrato(id):
    """Desvincula y borra el PDF del contrato."""
    with Session(engine) as s:
        p = s.get(Propiedad, id)
        if p and p.copia_contrato:
            f = CONTRATOS_DIR / p.copia_contrato
            if f.exists():
                f.unlink()
            p.copia_contrato = None
            s.commit()
    flash("Contrato eliminado.", "warning")
    return redirect(url_for("propiedad_editar", id=id))

# ── Consultas (equivalentes a las queries de Access) ──────────────────────────

@app.route("/consultas/pagos-mes")
@login_required
@permiso_required("ver_consultas")
def consulta_pagos_mes():
    """ConsultaPagosMes / ConsultaPagosMes2 — pagos del mes+año seleccionado."""
    mes = _int(request.args.get("mes")) or date.today().month
    año = _int(request.args.get("año")) or date.today().year
    with Session(engine) as s:
        rows = (
            s.query(Pago, Propiedad.direccion_propiedad,
                    Arrendatario.nombre_arrendatario)
            .outerjoin(Propiedad,    Pago.id_propiedad == Propiedad.id_propiedad)
            .outerjoin(Arrendatario, Propiedad.id_arrendatario == Arrendatario.id_arrendatario)
            .filter(
                func.strftime('%m', Pago.fecha_pago) == f"{mes:02d}",
                func.strftime('%Y', Pago.fecha_pago) == str(año)
            )
            .order_by(Propiedad.direccion_propiedad)
            .all()
        )
        años = sorted({int(r[0]) for r in s.query(func.strftime('%Y', Pago.fecha_pago))
                       .filter(Pago.fecha_pago != None).distinct().all()}, reverse=True)
        result = []
        for p, dir_, arr in rows:
            d = {c.key: getattr(p, c.key) for c in p.__mapper__.columns}
            d["direccion"]    = dir_
            d["arrendatario"] = arr
            d["forma_pago"]   = _enum_val(p.forma_pago)
            result.append(d)
        total_uf    = sum(r["valor_arriendo_uf"] or 0 for r in result)
        total_pesos = sum(r["valor_arriendo"]    or 0 for r in result)
    return render_template("consulta_pagos_mes.html",
                           pagos=result, meses=MESES, años=años,
                           mes=mes, año=año,
                           total_uf=total_uf, total_pesos=total_pesos)


@app.route("/consultas/pagos-año")
@login_required
@permiso_required("ver_consultas")
def consulta_pagos_año():
    """ConsultaPagosAño — resumen anual de pagos agrupado por mes de fecha_pago."""
    from types import SimpleNamespace
    año = _int(request.args.get("año")) or date.today().year
    with Session(engine) as s:
        _mes_fp = func.strftime('%m', Pago.fecha_pago)
        filas_raw = (
            s.query(
                _mes_fp.label("mes"),
                func.count(Pago.id_pago).label("cantidad"),
                func.sum(Pago.valor_arriendo_uf).label("total_uf"),
                func.sum(Pago.valor_arriendo).label("total_pesos"),
            )
            .filter(func.strftime('%Y', Pago.fecha_pago) == str(año),
                    Pago.fecha_pago != None)
            .group_by(_mes_fp)
            .order_by(_mes_fp)
            .all()
        )
        filas = [SimpleNamespace(mes=int(f.mes), cantidad=f.cantidad,
                                 total_uf=f.total_uf, total_pesos=f.total_pesos)
                 for f in filas_raw]
        años = sorted({int(r[0]) for r in s.query(func.strftime('%Y', Pago.fecha_pago))
                       .filter(Pago.fecha_pago != None).distinct().all()}, reverse=True)
        gran_total_uf    = sum(f.total_uf    or 0 for f in filas)
        gran_total_pesos = sum(f.total_pesos or 0 for f in filas)
    return render_template("consulta_pagos_año.html",
                           filas=filas, meses=MESES, años=años, año=año,
                           gran_total_uf=gran_total_uf,
                           gran_total_pesos=gran_total_pesos)


@app.route("/consultas/propiedades-arrendatario")
@login_required
@permiso_required("ver_consultas")
def consulta_propiedades_arrendatario():
    """ConsultaPropiedadesPorArrendatarios — propiedades activas por arrendatario."""
    q = request.args.get("q", "")
    with Session(engine) as s:
        q2 = (
            s.query(Arrendatario, Propiedad)
            .join(Propiedad, Arrendatario.id_arrendatario == Propiedad.id_arrendatario)
            .filter(Propiedad.estado == EstadoPropiedad.ARRENDADA)
            .order_by(Arrendatario.nombre_arrendatario, Propiedad.direccion_propiedad)
        )
        if q:
            q2 = q2.filter(Arrendatario.nombre_arrendatario.ilike(f"%{q}%"))
        rows = q2.all()
        # Agrupar por arrendatario
        from collections import OrderedDict
        grupos = OrderedDict()
        for arr, prop in rows:
            key = arr.id_arrendatario
            if key not in grupos:
                grupos[key] = {
                    "nombre": arr.nombre_arrendatario,
                    "rut":    arr.rut_arrendatario,
                    "telefono": arr.telefono,
                    "mail":   arr.mail,
                    "propiedades": [],
                }
            pd = {c.key: getattr(prop, c.key) for c in prop.__mapper__.columns}
            pd["tipo_propiedad"] = _enum_val(prop.tipo_propiedad)
            grupos[key]["propiedades"].append(pd)
    return render_template("consulta_propiedades_arr.html",
                           grupos=grupos.values(), q=q)


@app.route("/consultas/sin-pago-mes")
@login_required
@permiso_required("ver_consultas")
def consulta_sin_pago_mes():
    """Propiedades arrendadas sin pago registrado para el mes/año seleccionado."""
    from models import propiedades_sin_pago_mes
    mes = _int(request.args.get("mes")) or date.today().month
    año = _int(request.args.get("año")) or date.today().year
    with Session(engine) as s:
        años = [r[0] for r in
                s.query(Pago.año).distinct().filter(Pago.año != None)
                .order_by(Pago.año.desc()).all()]
        if not años:
            años = [date.today().year]
        rows = propiedades_sin_pago_mes(s, mes, año)
        result = []
        for prop, arr in rows:
            result.append({
                "direccion":    prop.direccion_propiedad,
                "tipo":         _enum_val(prop.tipo_propiedad),
                "valor_uf":     prop.valor_arriendo_uf,
                "arrendatario": arr.nombre_arrendatario,
                "rut":          arr.rut_arrendatario,
                "telefono":     arr.telefono,
                "mail":         arr.mail,
            })
    return render_template("consulta_sin_pago_mes.html",
                           propiedades=result, meses=MESES, años=años,
                           mes=mes, año=año)


# ── Gastos Comunes ────────────────────────────────────────────────────────────
def _get_items(s):
    return s.query(ItemGasto).filter(ItemGasto.activo == 1).order_by(ItemGasto.nombre).all()

@app.route("/gastos")
@login_required
@permiso_required("ver_gastos")
def gastos():
    mes = request.args.get("mes", "")
    año = request.args.get("año", str(date.today().year))
    with Session(engine) as s:
        q = (s.query(Gasto, ItemGasto.nombre)
             .join(ItemGasto, Gasto.id_item == ItemGasto.id_item))
        if mes: q = q.filter(Gasto.mes == int(mes))
        if año: q = q.filter(Gasto.año == int(año))
        rows = q.order_by(Gasto.mes, ItemGasto.nombre).all()
        años = [r[0] for r in s.query(Gasto.año).distinct()
                .filter(Gasto.año != None).order_by(Gasto.año.desc()).all()]
        if not años: años = [date.today().year]
        result = []
        for g, nombre in rows:
            d = {c.key: getattr(g, c.key) for c in g.__mapper__.columns}
            d["nombre_item"] = nombre
            result.append(d)
        total = sum(r["monto"] or 0 for r in result)
        pivote = {}
        meses_con_datos = sorted(set(r["mes"] for r in result if r["mes"]))
        for r in result:
            n = r["nombre_item"]
            if n not in pivote: pivote[n] = {}
            mes_gasto = r["mes"]
            if mes_gasto not in pivote[n]:
                pivote[n][mes_gasto] = {"monto": r["monto"] or 0}
            else:
                pivote[n][mes_gasto]["monto"] += r["monto"] or 0
    return render_template("gastos.html", gastos=result, pivote=pivote,
                           meses_con_datos=meses_con_datos,
                           meses=MESES, años=años, mes=mes, año=año, total=total)

@app.route("/gastos/nuevo", methods=["GET","POST"])
@login_required
@permiso_required("registrar_gastos")
def gasto_nuevo():
    with Session(engine) as s:
        items = _get_items(s)
        if request.method == "POST":
            g = Gasto(
                id_item     = _int(request.form.get("id_item")),
                mes         = _int(request.form.get("mes")),
                año         = _int(request.form.get("año")),
                monto       = _float(request.form.get("monto")),
                descripcion = _str(request.form.get("descripcion")),
                fecha_pago  = _date(request.form.get("fecha_pago")),
                factura     = _str(request.form.get("factura")),
            )
            s.add(g); s.commit()
            flash("Gasto registrado correctamente.", "success")
            return redirect(url_for("gastos"))
        items_data = [{"id_item": i.id_item, "nombre": i.nombre} for i in items]
    hoy = date.today()
    return render_template("form_gasto.html", gasto=None, items=items_data,
                           meses=MESES, mes_actual=hoy.month,
                           año_actual=hoy.year, hoy=hoy.isoformat())

@app.route("/gastos/<int:id>/editar", methods=["GET","POST"])
@login_required
@permiso_required("ver_gastos")
def gasto_editar(id):
    with Session(engine) as s:
        g = s.get(Gasto, id)
        if not g:
            flash("Gasto no encontrado.", "danger")
            return redirect(url_for("gastos"))
        items = _get_items(s)
        if request.method == "POST":
            g.id_item     = _int(request.form.get("id_item"))
            g.mes         = _int(request.form.get("mes"))
            g.año         = _int(request.form.get("año"))
            g.monto       = _float(request.form.get("monto"))
            g.descripcion = _str(request.form.get("descripcion"))
            g.fecha_pago  = _date(request.form.get("fecha_pago"))
            g.factura     = _str(request.form.get("factura"))
            s.commit()
            flash("Gasto actualizado.", "success")
            return redirect(url_for("gastos"))
        data = {c.key: getattr(g, c.key) for c in g.__mapper__.columns}
        items_data = [{"id_item": i.id_item, "nombre": i.nombre} for i in items]
    hoy = date.today()
    return render_template("form_gasto.html", gasto=data, items=items_data,
                           meses=MESES, mes_actual=hoy.month,
                           año_actual=hoy.year, hoy=hoy.isoformat())

@app.route("/gastos/<int:id>/eliminar")
@login_required
def gasto_eliminar(id):
    with Session(engine) as s:
        g = s.get(Gasto, id)
        if g: s.delete(g); s.commit()
    flash("Gasto eliminado.", "warning")
    return redirect(url_for("gastos"))

# ── Prorrateo de gastos comunes ───────────────────────────────────────────────
@app.route("/gastos/prorrateo")
@login_required
@permiso_required("ver_prorrateo")
def prorrateo_gastos():
    mes = request.args.get("mes", "")
    año = request.args.get("año", str(date.today().year))
    with Session(engine) as s:
        # Propiedades que pagan GC con sus m²
        props = (s.query(Propiedad, Arrendatario.nombre_arrendatario)
                 .outerjoin(Arrendatario, Propiedad.id_arrendatario == Arrendatario.id_arrendatario)
                 .filter(Propiedad.paga_gastos_comunes == 1)
                 .order_by(Propiedad.direccion_propiedad).all())
        total_m2 = sum((p.metros_propiedad or 0) for p, _ in props)

        filas = []
        for p, nombre_arr in props:
            m2 = p.metros_propiedad or 0
            pct = (m2 / total_m2 * 100) if total_m2 else 0
            filas.append({
                "id_propiedad":   p.id_propiedad,
                "direccion":      p.direccion_propiedad or "—",
                "arrendatario":   nombre_arr or "—",
                "m2":             m2,
                # store full precision pct (template formats to 2 decimals)
                "pct":            pct,
            })

        # Total gastos del periodo
        q_gc = s.query(func.sum(Gasto.monto)).filter(Gasto.monto != None)
        if mes: q_gc = q_gc.filter(Gasto.mes == int(mes))
        if año: q_gc = q_gc.filter(Gasto.año == int(año))
        total_gc = q_gc.scalar() or 0

        # Añadir monto prorrateado usando proporción real sobre m2 (evitar usar pct redondeado)
        for f in filas:
            f["monto_gc"] = round(total_gc * (f["m2"] / total_m2)) if total_gc and total_m2 else 0

        # Años disponibles
        años = [r[0] for r in s.query(Gasto.año).distinct()
                .filter(Gasto.año != None).order_by(Gasto.año.desc()).all()]
        if not años: años = [date.today().year]

        # Desglose GC por ítem del periodo
        q_items = (s.query(ItemGasto.nombre, func.sum(Gasto.monto).label("total"))
                   .join(Gasto, Gasto.id_item == ItemGasto.id_item))
        if mes: q_items = q_items.filter(Gasto.mes == int(mes))
        if año: q_items = q_items.filter(Gasto.año == int(año))
        items_gc = q_items.group_by(ItemGasto.nombre).order_by(ItemGasto.nombre).all()

    return render_template("prorrateo_gastos.html",
                           filas=filas, total_m2=total_m2, total_gc=total_gc,
                           items_gc=items_gc, meses=MESES, años=años,
                           mes=mes, año=año)

# ── Ítems de gastos (mantenedor) ──────────────────────────────────────────────
@app.route("/gastos/items")
@login_required
@permiso_required("ver_gastos")
def items_gasto():
    with Session(engine) as s:
        items = s.query(ItemGasto).order_by(ItemGasto.nombre).all()
        data = [{c.key: getattr(i, c.key) for c in i.__mapper__.columns} for i in items]
    return render_template("items_gasto.html", items=data)

@app.route("/gastos/items/nuevo", methods=["GET","POST"])
@login_required
@permiso_required("registrar_gastos")
def item_gasto_nuevo():
    if request.method == "POST":
        nombre = _str(request.form.get("nombre"))
        if nombre:
            with Session(engine) as s:
                s.add(ItemGasto(nombre=nombre)); s.commit()
            flash(f"Ítem «{nombre}» agregado.", "success")
        return redirect(url_for("items_gasto"))
    return render_template("items_gasto.html", items=[], nuevo=True)

@app.route("/gastos/items/<int:id>/toggle")
@admin_required
def item_gasto_toggle(id):
    with Session(engine) as s:
        i = s.get(ItemGasto, id)
        if i:
            i.activo = 0 if i.activo else 1
            s.commit()
            estado = "activado" if i.activo else "desactivado"
            flash(f"Ítem «{i.nombre}» {estado}.", "info")
    return redirect(url_for("items_gasto"))

@app.route("/gastos/items/<int:id>/eliminar")
@admin_required
def item_gasto_eliminar(id):
    with Session(engine) as s:
        i = s.get(ItemGasto, id)
        if i:
            if s.query(Gasto).filter(Gasto.id_item == id).count():
                flash("No se puede eliminar: tiene gastos asociados. Desactívalo en su lugar.", "danger")
                return redirect(url_for("items_gasto"))
            s.delete(i); s.commit()
            flash("Ítem eliminado.", "warning")
    return redirect(url_for("items_gasto"))


# ── Gestión de usuarios ───────────────────────────────────────────────────────
@app.route("/usuarios")
@admin_required
def usuarios():
    with Session(engine) as s:
        lista = s.query(Usuario).order_by(Usuario.username).all()
        data  = [{c.key: getattr(u, c.key) for c in u.__mapper__.columns} | {"rol_val": u.rol.value}
                 for u in lista]
    return render_template("usuarios.html", usuarios=data)

@app.route("/usuarios/nuevo", methods=["GET","POST"])
@admin_required
def usuario_nuevo():
    if request.method == "POST":
        username = _str(request.form.get("username"))
        pwd      = request.form.get("password","").strip()
        nombre   = _str(request.form.get("nombre"))
        rol_val  = request.form.get("rol","usuario")
        if not username or not pwd:
            flash("Usuario y contraseña son obligatorios.", "danger")
        else:
            with Session(engine) as s:
                if s.query(Usuario).filter(Usuario.username == username).first():
                    flash("Ese nombre de usuario ya existe.", "danger")
                else:
                    s.add(Usuario(
                        username=username, nombre=nombre,
                        password_hash=generate_password_hash(pwd),
                        rol=RolUsuario(rol_val), activo=1,
                        permisos=PERMISOS_DEFAULT if rol_val=="usuario" else "",
                    ))
                    s.commit()
                    flash(f"Usuario «{username}» creado correctamente.", "success")
                    return redirect(url_for("usuarios"))
    return render_template("form_usuario.html", usuario=None)

@app.route("/usuarios/<int:id>/editar", methods=["GET","POST"])
@admin_required
def usuario_editar(id):
    with Session(engine) as s:
        u = s.get(Usuario, id)
        if not u:
            flash("Usuario no encontrado.", "danger")
            return redirect(url_for("usuarios"))
        if request.method == "POST":
            u.nombre  = _str(request.form.get("nombre"))
            u.rol     = RolUsuario(request.form.get("rol","usuario"))
            u.activo  = 1 if request.form.get("activo") else 0
            nueva_pwd = request.form.get("nueva_password","").strip()
            if nueva_pwd:
                u.password_hash = generate_password_hash(nueva_pwd)
                flash("Contraseña actualizada.", "info")
            s.commit()
            flash(f"Usuario «{u.username}» actualizado.", "success")
            return redirect(url_for("usuarios"))
        data = {c.key: getattr(u, c.key) for c in u.__mapper__.columns}
        data["rol_val"] = u.rol.value
    return render_template("form_usuario.html", usuario=data)

@app.route("/usuarios/<int:id>/permisos", methods=["GET","POST"])
@admin_required
def usuario_permisos(id):
    with Session(engine) as s:
        u = s.get(Usuario, id)
        if not u:
            flash("Usuario no encontrado.", "danger")
            return redirect(url_for("usuarios"))
        if u.rol.value == "admin":
            flash("Los administradores tienen todos los permisos por defecto.", "info")
            return redirect(url_for("usuarios"))
        if request.method == "POST":
            seleccionados = request.form.getlist("permisos")
            u.permisos = ",".join(seleccionados)
            s.commit()
            flash(f"Permisos de «{u.username}» actualizados.", "success")
            return redirect(url_for("usuarios"))
        data = {c.key: getattr(u, c.key) for c in u.__mapper__.columns}
        data["permisos_lista"] = (u.permisos or "").split(",")
    return render_template("permisos_usuario.html",
                           usuario=data,
                           catalogo=PERMISOS_CATALOGO)

@app.route("/usuarios/<int:id>/rol", methods=["POST"])
@admin_required
def usuario_cambiar_rol(id):
    """Cambia el rol de un usuario directamente desde la lista."""
    with Session(engine) as s:
        u = s.get(Usuario, id)
        if not u:
            flash("Usuario no encontrado.", "danger")
            return redirect(url_for("usuarios"))
        # No permitir que el admin se quite su propio rol
        if u.id_usuario == session["id_usuario"]:
            flash("No puedes cambiar tu propio rol.", "warning")
            return redirect(url_for("usuarios"))
        nuevo_rol = request.form.get("rol")
        u.rol = RolUsuario(nuevo_rol)
        s.commit()
        flash(f"Rol de «{u.username}» actualizado a {nuevo_rol.upper()}.", "success")
    return redirect(url_for("usuarios"))

@app.route("/mi-cuenta", methods=["GET","POST"])
@login_required
def mi_cuenta():
    """Cualquier usuario puede cambiar su propia contraseña."""
    if request.method == "POST":
        actual    = request.form.get("password_actual","")
        nueva     = request.form.get("password_nueva","").strip()
        confirma  = request.form.get("password_confirma","").strip()
        with Session(engine) as s:
            u = s.get(Usuario, session["id_usuario"])
            if not check_password_hash(u.password_hash, actual):
                flash("La contraseña actual es incorrecta.", "danger")
            elif nueva != confirma:
                flash("La nueva contraseña y la confirmación no coinciden.", "danger")
            elif len(nueva) < 4:
                flash("La contraseña debe tener al menos 4 caracteres.", "danger")
            else:
                u.password_hash = generate_password_hash(nueva)
                s.commit()
                flash("Contraseña cambiada correctamente.", "success")
                return redirect(url_for("dashboard"))
    return render_template("mi_cuenta.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
