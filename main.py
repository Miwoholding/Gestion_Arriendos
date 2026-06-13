"""
Punto de entrada de ejemplo para Gestión Arriendos.
Crea la base de datos SQLite y muestra cómo usar los modelos.
"""
from datetime import date
from sqlalchemy.orm import Session
from models import (
    create_db, Arrendatario, Propiedad, Pago,
    TipoPropiedad, EstadoPropiedad, FormaPago,
    consulta_pagos_mes, consulta_pagos_año,
    consulta_propiedades_por_arrendatario,
)


def main():
    engine = create_db()

    with Session(engine) as session:
        # --- Datos de ejemplo ---
        arrendatario = Arrendatario(
            nombre_arrendatario="Juan Pérez",
            rut_arrendatario="12.345.678-9",
            telefono="+56 9 1234 5678",
            mail="juan@example.com",
            actividad_arrendatario="Médico",
        )
        session.add(arrendatario)
        session.flush()

        propiedad = Propiedad(
            tipo_propiedad=TipoPropiedad.OFICINA,
            direccion_propiedad="Av. Libertad 1234, Of. 302, Quillota",
            metros_propiedad=45.0,
            estado=EstadoPropiedad.ARRENDADA,
            valor_arriendo_uf=12.5,
            fecha_contrato=date(2024, 3, 1),
            duracion_contrato=12,
            id_arrendatario=arrendatario.id_arrendatario,
        )
        session.add(propiedad)
        session.flush()

        pago = Pago(
            id_propiedad=propiedad.id_propiedad,
            fecha_pago=date(2025, 5, 5),
            mes=5,
            año=2025,
            valor_uf=37_800.0,
            valor_arriendo_uf=12.5,
            valor_arriendo=12.5 * 37_800,
            forma_pago=FormaPago.TRANSFERENCIA,
        )
        session.add(pago)
        session.commit()

        # --- Consultas ---
        pagos_mayo = consulta_pagos_mes(session, mes=5, año=2025)
        print(f"Pagos mayo 2025: {len(pagos_mayo)}")

        props = consulta_propiedades_por_arrendatario(
            session, arrendatario.id_arrendatario
        )
        print(f"Propiedades de {arrendatario.nombre_arrendatario}: {len(props)}")


if __name__ == "__main__":
    main()
