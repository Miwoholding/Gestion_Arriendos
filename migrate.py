"""
Migra los datos de GestiónArriendos.accdb → gestion_arriendos.db (SQLite).
"""
import sys
import os
from pathlib import Path
from datetime import date

# Asegurar que el parser esté en el path
sys.path.insert(0, str(Path(__file__).parent))

from accdb_reader import AccdbReader, read_catalog, parse_table_def, read_table_rows, extract_all
from models import (
    create_db, Arrendatario, Propiedad, Pago,
    TipoPropiedad, EstadoPropiedad, FormaPago,
)
from sqlalchemy.orm import Session


DB_ACCDB = Path(__file__).parent.parent / "GestiónArriendos.accdb"
DB_SQLITE = Path(__file__).parent / "gestion_arriendos.db"


# ── Mapeadores de valores de enumeración ──────────────────────────────────────

_TIPO = {
    "CASA":            TipoPropiedad.CASA,
    "DEPARTAMENTO":    TipoPropiedad.DEPARTAMENTO,
    "OFICINA":         TipoPropiedad.OFICINA,
    "TERRENO":         TipoPropiedad.TERRENO,
    "LOCAL COMERCIAL": TipoPropiedad.LOCAL_COMERCIAL,
    "BODEGA":          TipoPropiedad.BODEGA,
}

_ESTADO = {
    "ARRENDADA":   EstadoPropiedad.ARRENDADA,
    "DISPONIBLE":  EstadoPropiedad.DISPONIBLE,
    "VENDIDA":     EstadoPropiedad.VENDIDA,
}

_FORMA = {
    "Transferencia": FormaPago.TRANSFERENCIA,
    "Depósito":      FormaPago.DEPOSITO,
    "Efectivo":      FormaPago.EFECTIVO,
}


def _str(val) -> str | None:
    return str(val).strip() if val else None


def _date(val) -> date | None:
    return val if isinstance(val, date) else None


def _float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _int(val) -> int | None:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ── Migración ─────────────────────────────────────────────────────────────────

def migrate():
    print(f"Leyendo: {DB_ACCDB}")
    reader = AccdbReader(str(DB_ACCDB))
    catalog = read_catalog(reader)

    arrendatarios_raw = read_table_rows(reader, parse_table_def(reader, catalog["Arrendatarios"]))
    propiedades_raw   = read_table_rows(reader, parse_table_def(reader, catalog["Propiedades"]))
    pagos_raw         = read_table_rows(reader, parse_table_def(reader, catalog["Pagos"]))

    print(f"  Arrendatarios: {len(arrendatarios_raw)} filas")
    print(f"  Propiedades:   {len(propiedades_raw)} filas")
    print(f"  Pagos:         {len(pagos_raw)} filas")

    # Limpiar DB existente y crear de nuevo
    if DB_SQLITE.exists():
        DB_SQLITE.unlink()

    engine = create_db(f"sqlite:///{DB_SQLITE}")

    with Session(engine) as session:
        # ── Arrendatarios ─────────────────────────────────────────────────────
        id_map = {}  # old Id → new SQLAlchemy object
        for r in arrendatarios_raw:
            a = Arrendatario(
                id_arrendatario        = _int(r.get("Id_Arrendatario")),
                nombre_arrendatario    = _str(r.get("Nombre_Arrendatario")),
                rut_arrendatario       = _str(r.get("RUT_Arrendatario")),
                contacto               = _str(r.get("Contacto")),
                telefono               = _str(r.get("Telefono")),
                mail                   = _str(r.get("Mail")),
                actividad_arrendatario = _str(r.get("Actividad_Arrendatario")),
            )
            session.add(a)
            id_map[r.get("Id_Arrendatario")] = a

        session.flush()

        # ── Propiedades ───────────────────────────────────────────────────────
        prop_map = {}
        for r in propiedades_raw:
            tipo_str   = _str(r.get("Tipo_Propiedad"))
            estado_str = _str(r.get("Estado"))
            p = Propiedad(
                id_propiedad       = _int(r.get("Id_Propiedad")),
                tipo_propiedad     = _TIPO.get(tipo_str),
                direccion_propiedad= _str(r.get("Direccion_Propiedad")),
                metros_terreno     = _float(r.get("Metros_Terreno")),
                metros_propiedad   = _float(r.get("Metros_Propiedad")),
                estado             = _ESTADO.get(estado_str),
                valor_arriendo_uf  = _float(r.get("Valor_Arriendo_UF")),
                fecha_contrato     = _date(r.get("Fecha_Contrato")),
                duracion_contrato  = _int(r.get("Duracion_Contrato")),
                id_arrendatario    = _int(r.get("Id_Arrendatario")),
            )
            session.add(p)
            prop_map[r.get("Id_Propiedad")] = p

        session.flush()

        # ── Pagos ─────────────────────────────────────────────────────────────
        for r in pagos_raw:
            forma_str = _str(r.get("FormaPago"))
            uf        = _float(r.get("ValorArriendoUF"))
            valor_uf  = _float(r.get("ValorUF"))
            # Calcular valor en pesos si no está almacenado directamente
            valor_arriendo = round(uf * valor_uf, 0) if uf and valor_uf else None

            fecha = _date(r.get("Fecha_Pago"))
            pg = Pago(
                id_pago           = _int(r.get("Id_Pago")),
                id_propiedad      = _int(r.get("Propiedad")),
                fecha_pago        = fecha,
                mes               = fecha.month if fecha else None,
                año               = fecha.year  if fecha else None,
                valor_uf          = valor_uf,
                valor_arriendo_uf = uf,
                valor_arriendo    = valor_arriendo,
                factura           = _str(r.get("Factura")),
                forma_pago        = _FORMA.get(forma_str),
            )
            session.add(pg)

        session.commit()
        print(f"\nMigración completada → {DB_SQLITE}")

    # ── Verificación rápida ───────────────────────────────────────────────────
    with Session(engine) as session:
        n_arr  = session.query(Arrendatario).count()
        n_prop = session.query(Propiedad).count()
        n_pag  = session.query(Pago).count()
        print(f"\nVerificación SQLite:")
        print(f"  Arrendatarios: {n_arr}")
        print(f"  Propiedades:   {n_prop}")
        print(f"  Pagos:         {n_pag}")

        print("\nMuestra Arrendatarios:")
        for a in session.query(Arrendatario).limit(3):
            print(f"  [{a.id_arrendatario}] {a.nombre_arrendatario} — {a.rut_arrendatario}")

        print("\nMuestra Pagos (últimos):")
        for p in session.query(Pago).order_by(Pago.fecha_pago.desc()).limit(3):
            print(f"  [{p.id_pago}] Prop {p.id_propiedad}  {p.fecha_pago}  {p.valor_arriendo_uf} UF  ${p.valor_arriendo:,.0f}")


if __name__ == "__main__":
    migrate()
