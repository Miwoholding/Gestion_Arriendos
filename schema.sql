-- ============================================================
-- Gestión Arriendos – Esquema SQL
-- Migrado desde GestiónArriendos.accdb (Access)
-- Compatible con SQLite / PostgreSQL / MySQL
-- ============================================================

CREATE TABLE arrendatarios (
    id_arrendatario     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_arrendatario VARCHAR(100) NOT NULL,
    rut_arrendatario    VARCHAR(12)  NOT NULL UNIQUE,
    contacto            VARCHAR(100),
    telefono            VARCHAR(20),
    mail                VARCHAR(100),
    actividad_arrendatario VARCHAR(100)
);

CREATE TABLE propiedades (
    id_propiedad        INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_propiedad      VARCHAR(20) CHECK (tipo_propiedad IN
                            ('CASA','DEPARTAMENTO','OFICINA','TERRENO','LOCAL COMERCIAL','BODEGA')),
    direccion_propiedad VARCHAR(200) NOT NULL,
    metros_terreno      REAL,
    metros_propiedad    REAL,
    estado              VARCHAR(15) DEFAULT 'DISPONIBLE' CHECK (estado IN
                            ('ARRENDADA','DISPONIBLE','VENDIDA')),
    valor_arriendo_uf   REAL,
    fecha_contrato      DATE,
    duracion_contrato   INTEGER,          -- meses
    copia_contrato      TEXT,             -- ruta / nombre del archivo
    id_arrendatario     INTEGER REFERENCES arrendatarios(id_arrendatario)
);

CREATE TABLE pagos (
    id_pago             INTEGER PRIMARY KEY AUTOINCREMENT,
    id_propiedad        INTEGER NOT NULL REFERENCES propiedades(id_propiedad),
    fecha_pago          DATE,
    mes                 INTEGER CHECK (mes BETWEEN 1 AND 12),
    año                 INTEGER,
    valor_uf            REAL,             -- valor UF del día del pago
    valor_arriendo_uf   REAL,             -- arriendo pactado en UF
    valor_arriendo      REAL,             -- monto en pesos
    factura             VARCHAR(50),
    forma_pago          VARCHAR(20) CHECK (forma_pago IN
                            ('Transferencia','Depósito','Efectivo'))
);

-- ---------------------------------------------------------------------------
-- Vistas (equivalentes a las queries de Access)
-- ---------------------------------------------------------------------------

CREATE VIEW v_pagos_por_mes AS
SELECT
    p.id_pago,
    pr.direccion_propiedad,
    a.nombre_arrendatario,
    p.mes,
    p.año,
    p.fecha_pago,
    p.valor_arriendo_uf,
    p.valor_arriendo,
    p.forma_pago,
    p.factura
FROM pagos p
JOIN propiedades pr ON pr.id_propiedad = p.id_propiedad
LEFT JOIN arrendatarios a ON a.id_arrendatario = pr.id_arrendatario;

CREATE VIEW v_propiedades_por_arrendatario AS
SELECT
    a.nombre_arrendatario,
    a.rut_arrendatario,
    a.telefono,
    a.mail,
    pr.direccion_propiedad,
    pr.tipo_propiedad,
    pr.estado,
    pr.valor_arriendo_uf,
    pr.fecha_contrato,
    pr.duracion_contrato
FROM arrendatarios a
JOIN propiedades pr ON pr.id_arrendatario = a.id_arrendatario
ORDER BY a.nombre_arrendatario, pr.direccion_propiedad;

CREATE VIEW v_resumen_pagos_año AS
SELECT
    año,
    mes,
    COUNT(*)                   AS cantidad_pagos,
    SUM(valor_arriendo_uf)     AS total_uf,
    SUM(valor_arriendo)        AS total_pesos
FROM pagos
GROUP BY año, mes
ORDER BY año, mes;
