import sqlite3, os, hashlib
from flask import g

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.environ.get('DATABASE_URL', os.path.join(BASE_DIR, 'database', 'ventix.db'))


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()


def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript('''
        -- ── Tabla maestra de tiendas (multi-tenant) ──────────────
        CREATE TABLE IF NOT EXISTS tiendas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT    NOT NULL,
            ciudad      TEXT    DEFAULT '',
            direccion   TEXT    DEFAULT '',
            telefono    TEXT    DEFAULT '',
            plan        TEXT    DEFAULT 'basico',
            activa      INTEGER DEFAULT 1,
            fecha_alta  TEXT    DEFAULT CURRENT_TIMESTAMP,
            mensaje_ticket TEXT DEFAULT '¡Gracias por tu compra!'
        );

        -- ── Usuarios (admin general + cajeros por tienda) ─────────
        CREATE TABLE IF NOT EXISTS usuarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id   INTEGER REFERENCES tiendas(id),
            nombre      TEXT    NOT NULL,
            username    TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            rol         TEXT    NOT NULL DEFAULT 'cajero',
            activo      INTEGER DEFAULT 1,
            fecha_alta  TEXT    DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Categorías por tienda ─────────────────────────────────
        CREATE TABLE IF NOT EXISTS categorias (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id INTEGER NOT NULL REFERENCES tiendas(id),
            nombre    TEXT    NOT NULL
        );

        -- ── Productos por tienda ──────────────────────────────────
        CREATE TABLE IF NOT EXISTS productos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id      INTEGER NOT NULL REFERENCES tiendas(id),
            nombre         TEXT    NOT NULL,
            codigo_barras  TEXT,
            precio         REAL    NOT NULL DEFAULT 0,
            precio_costo   REAL    NOT NULL DEFAULT 0,
            stock          INTEGER NOT NULL DEFAULT 0,
            stock_minimo   INTEGER NOT NULL DEFAULT 5,
            categoria_id   INTEGER REFERENCES categorias(id),
            activo         INTEGER DEFAULT 1
        );

        -- ── Ventas por tienda ─────────────────────────────────────
        CREATE TABLE IF NOT EXISTS ventas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id   INTEGER NOT NULL REFERENCES tiendas(id),
            usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
            fecha       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            total       REAL    NOT NULL DEFAULT 0,
            metodo_pago TEXT    NOT NULL DEFAULT 'Efectivo',
            caja        TEXT    DEFAULT 'Caja 1'
        );

        -- ── Detalle de ventas ─────────────────────────────────────
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id        INTEGER NOT NULL REFERENCES ventas(id),
            producto_id     INTEGER NOT NULL REFERENCES productos(id),
            cantidad        INTEGER NOT NULL,
            precio_unitario REAL    NOT NULL
        );

        -- ── Índices de rendimiento ────────────────────────────────
        CREATE INDEX IF NOT EXISTS idx_productos_tienda    ON productos(tienda_id);
        CREATE INDEX IF NOT EXISTS idx_ventas_tienda_fecha ON ventas(tienda_id, fecha);
        CREATE INDEX IF NOT EXISTS idx_detalle_venta       ON detalle_ventas(venta_id);
    ''')

    # ── Super admin (dueño de Ventix) ─────────────────────────────
    existe = db.execute(
        "SELECT id FROM usuarios WHERE username='superadmin'"
    ).fetchone()
    if not existe:
        db.execute('''
            INSERT INTO usuarios (tienda_id, nombre, username, password, rol)
            VALUES (NULL, 'Super Admin', 'superadmin', ?, 'superadmin')
        ''', (hash_pw('ventix2024'),))

    db.commit()
    db.close()
    print('✅ Ventix DB inicializada')
