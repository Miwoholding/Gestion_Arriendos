from datetime import date
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, Float, Date, ForeignKey,
    Enum, Text, create_engine, exists
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session


class RolUsuario(PyEnum):
    ADMIN   = "admin"
    USUARIO = "usuario"


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario    = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(50), unique=True, nullable=False)
    nombre        = Column(String(100))
    password_hash = Column(String(200), nullable=False)
    rol           = Column(Enum(RolUsuario), default=RolUsuario.USUARIO, nullable=False)
    activo        = Column(Integer, default=1)   # 1=activo, 0=inactivo
    permisos      = Column(Text, default="")     # CSV: "ver_pagos,registrar_pagos,..."


class TipoPropiedad(PyEnum):
    CASA = "CASA"
    DEPARTAMENTO = "DEPARTAMENTO"
    OFICINA = "OFICINA"
    TERRENO = "TERRENO"
    LOCAL_COMERCIAL = "LOCAL COMERCIAL"
    BODEGA = "BODEGA"


class EstadoPropiedad(PyEnum):
    ARRENDADA = "ARRENDADA"
    DISPONIBLE = "DISPONIBLE"
    VENDIDA = "VENDIDA"


class FormaPago(PyEnum):
    TRANSFERENCIA = "Transferencia"
    DEPOSITO = "Depósito"
    EFECTIVO = "Efectivo"


class Arrendatario(Base):
    __tablename__ = "arrendatarios"

    id_arrendatario = Column(Integer, primary_key=True, autoincrement=True)
    nombre_arrendatario = Column(String(100))
    rut_arrendatario = Column(String(20), unique=True)
    contacto = Column(String(100))
    telefono = Column(String(20))
    mail = Column(String(100))
    actividad_arrendatario = Column(String(100))

    propiedades = relationship("Propiedad", back_populates="arrendatario")


class Propiedad(Base):
    __tablename__ = "propiedades"

    id_propiedad = Column(Integer, primary_key=True, autoincrement=True)
    tipo_propiedad = Column(Enum(TipoPropiedad))
    direccion_propiedad = Column(String(200))
    rol = Column(String(30))             # ROL SII ej: 3260-047
    metros_terreno = Column(Float)
    metros_propiedad = Column(Float)
    estado = Column(Enum(EstadoPropiedad), default=EstadoPropiedad.DISPONIBLE)
    valor_arriendo_uf = Column(Float)
    fecha_contrato = Column(Date)
    duracion_contrato = Column(Integer)  # en meses
    copia_contrato = Column(Text)        # ruta o nombre del archivo
    paga_gastos_comunes = Column(Integer, default=0)  # 1=sí, 0=no
    id_arrendatario = Column(Integer, ForeignKey("arrendatarios.id_arrendatario"))

    arrendatario = relationship("Arrendatario", back_populates="propiedades")
    pagos = relationship("Pago", back_populates="propiedad", passive_deletes=True)


class Pago(Base):
    __tablename__ = "pagos"

    id_pago = Column(Integer, primary_key=True, autoincrement=True)
    id_propiedad = Column(Integer, ForeignKey("propiedades.id_propiedad", ondelete="SET NULL"), nullable=True)
    fecha_pago = Column(Date)
    mes = Column(Integer)   # 1-12
    año = Column(Integer)
    valor_uf = Column(Float)          # valor UF del día del pago
    valor_arriendo_uf = Column(Float) # arriendo pactado en UF
    valor_arriendo = Column(Float)    # arriendo en pesos (uf × valor_uf)
    gasto_comun   = Column(Float)       # prorrateo GC asignado a esta propiedad
    descuento     = Column(Float)       # descuento en pesos
    publicidad    = Column(Float)       # cargo por publicidad
    secretaria    = Column(Float)       # cargo por secretaria
    esterilizacion = Column(Float)      # cargo por esterilización
    otro          = Column(Float)       # otro cargo
    observaciones = Column(String(300)) # descripción del cargo "otro"
    # total_pago = valor_arriendo + gasto_comun + publicidad + secretaria + esterilizacion + otro - descuento
    factura = Column(String(50))
    forma_pago = Column(Enum(FormaPago))

    propiedad = relationship("Propiedad", back_populates="pagos")


class ItemGasto(Base):
    """Catálogo de ítems/proveedores de gastos comunes (mantenible por el usuario)."""
    __tablename__ = "items_gasto"

    id_item = Column(Integer, primary_key=True, autoincrement=True)
    nombre  = Column(String(100), nullable=False, unique=True)
    activo  = Column(Integer, default=1)   # 1=activo, 0=inactivo

    gastos  = relationship("Gasto", back_populates="item")


class Gasto(Base):
    __tablename__ = "gastos"

    id_gasto    = Column(Integer, primary_key=True, autoincrement=True)
    id_item     = Column(Integer, ForeignKey("items_gasto.id_item"), nullable=False)
    mes         = Column(Integer)           # 1-12
    año         = Column(Integer)
    monto       = Column(Float)             # en pesos
    descripcion = Column(String(200))
    fecha_pago  = Column(Date)
    factura     = Column(String(50))

    item = relationship("ItemGasto", back_populates="gastos")


# ---------------------------------------------------------------------------
# Helpers de consulta (equivalentes a las queries de Access)
# ---------------------------------------------------------------------------

def consulta_pagos_mes(session: Session, mes: int, año: int):
    """Equivalente a ConsultaPagosMes."""
    return (
        session.query(Pago)
        .filter(Pago.mes == mes, Pago.año == año)
        .order_by(Pago.fecha_pago)
        .all()
    )


def consulta_pagos_año(session: Session, año: int):
    """Equivalente a ConsultaPagosAño."""
    return (
        session.query(Pago)
        .filter(Pago.año == año)
        .order_by(Pago.mes)
        .all()
    )


def propiedades_sin_pago_mes(session: Session, mes: int, año: int):
    """Propiedades arrendadas que no tienen pago registrado para el mes/año dado."""
    pago_existe = (
        exists()
        .where(Pago.id_propiedad == Propiedad.id_propiedad)
        .where(Pago.mes == mes)
        .where(Pago.año == año)
    )
    return (
        session.query(Propiedad, Arrendatario)
        .join(Arrendatario, Propiedad.id_arrendatario == Arrendatario.id_arrendatario)
        .filter(Propiedad.estado == EstadoPropiedad.ARRENDADA)
        .filter(~pago_existe)
        .order_by(Propiedad.direccion_propiedad)
        .all()
    )


def consulta_propiedades_por_arrendatario(session: Session, id_arrendatario: int):
    """Equivalente a ConsultaPropiedadesPorArrendatarios."""
    return (
        session.query(Propiedad)
        .filter(Propiedad.id_arrendatario == id_arrendatario)
        .order_by(Propiedad.direccion_propiedad)
        .all()
    )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def create_db(url: str = "sqlite:///gestion_arriendos.db"):
    engine = create_engine(url, echo=False)
    Base.metadata.create_all(engine)
    return engine
