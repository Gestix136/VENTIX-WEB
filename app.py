from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash, g)
from functools import wraps
import os, datetime, json
from database.db import get_db, close_db, hash_pw, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ventix_dev_key_cambiar_en_produccion')
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

app.teardown_appcontext(close_db)

# ─────────────────────────────────────────────────────────────────
# Auth decorators
# ─────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*a, **k)
    return d


def superadmin_required(f):
    @wraps(f)
    def d(*a, **k):
        if session.get('rol') != 'superadmin':
            flash('Acceso restringido.', 'error')
            return redirect(url_for('pos'))
        return f(*a, **k)
    return d


def get_tienda():
    """Retorna los datos de la tienda del usuario en sesión."""
    tid = session.get('tienda_id')
    if not tid:
        return None
    return get_db().execute('SELECT * FROM tiendas WHERE id=?', (tid,)).fetchone()


# ─────────────────────────────────────────────────────────────────
# LOGIN / LOGOUT
# ─────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        if session.get('rol') == 'superadmin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('pos'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        db = get_db()
        user = db.execute(
            'SELECT * FROM usuarios WHERE username=? AND password=? AND activo=1',
            (username, hash_pw(password))
        ).fetchone()

        if user:
            # Verificar tienda activa (superadmin no tiene tienda)
            if user['rol'] != 'superadmin':
                tienda = db.execute(
                    'SELECT * FROM tiendas WHERE id=? AND activa=1',
                    (user['tienda_id'],)
                ).fetchone()
                if not tienda:
                    error = 'Tu tienda está suspendida. Contacta a soporte.'
                else:
                    session['user_id']  = user['id']
                    session['username'] = user['username']
                    session['nombre']   = user['nombre']
                    session['rol']      = user['rol']
                    session['tienda_id'] = user['tienda_id']
                    session['tienda_nombre'] = tienda['nombre']
                    return redirect(url_for('pos'))
            else:
                session['user_id']  = user['id']
                session['username'] = user['username']
                session['nombre']   = user['nombre']
                session['rol']      = 'superadmin'
                session['tienda_id'] = None
                return redirect(url_for('admin_dashboard'))
        else:
            error = 'Usuario o contraseña incorrectos.'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─────────────────────────────────────────────────────────────────
# SUPER ADMIN — Dashboard
# ─────────────────────────────────────────────────────────────────
@app.route('/admin')
@login_required
@superadmin_required
def admin_dashboard():
    db = get_db()
    tiendas = db.execute('''
        SELECT t.*, 
               COUNT(DISTINCT u.id) as num_usuarios,
               COUNT(DISTINCT v.id) as num_ventas,
               COALESCE(SUM(v.total), 0) as total_ventas
        FROM tiendas t
        LEFT JOIN usuarios u ON u.tienda_id = t.id
        LEFT JOIN ventas v ON v.tienda_id = t.id
        GROUP BY t.id
        ORDER BY t.fecha_alta DESC
    ''').fetchall()

    stats = {
        'total_tiendas':  db.execute('SELECT COUNT(*) FROM tiendas').fetchone()[0],
        'tiendas_activas': db.execute('SELECT COUNT(*) FROM tiendas WHERE activa=1').fetchone()[0],
        'total_ventas':   db.execute('SELECT COALESCE(SUM(total),0) FROM ventas').fetchone()[0],
        'total_usuarios': db.execute("SELECT COUNT(*) FROM usuarios WHERE rol != 'superadmin'").fetchone()[0],
    }
    return render_template('admin/dashboard.html', tiendas=tiendas, stats=stats)


@app.route('/admin/tienda/nueva', methods=['GET', 'POST'])
@login_required
@superadmin_required
def admin_nueva_tienda():
    if request.method == 'POST':
        nombre   = request.form.get('nombre', '').strip()
        ciudad   = request.form.get('ciudad', '').strip()
        direccion = request.form.get('direccion', '').strip()
        telefono = request.form.get('telefono', '').strip()
        plan     = request.form.get('plan', 'basico')
        # Credenciales del admin de la tienda
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        nombre_admin = request.form.get('nombre_admin', '').strip()

        if not nombre or not username or not password:
            flash('Nombre de tienda, usuario y contraseña son obligatorios.', 'error')
            return render_template('admin/nueva_tienda.html')

        db = get_db()
        try:
            # Crear tienda
            cur = db.execute('''
                INSERT INTO tiendas (nombre, ciudad, direccion, telefono, plan)
                VALUES (?, ?, ?, ?, ?)
            ''', (nombre, ciudad, direccion, telefono, plan))
            tienda_id = cur.lastrowid

            # Crear admin de la tienda
            db.execute('''
                INSERT INTO usuarios (tienda_id, nombre, username, password, rol)
                VALUES (?, ?, ?, ?, 'admin')
            ''', (tienda_id, nombre_admin or nombre, username, hash_pw(password)))

            # Crear categorías básicas
            for cat in ['Bebidas', 'Lácteos', 'Carnes', 'Abarrotes', 'Dulces', 'Limpieza', 'Otros']:
                db.execute('INSERT INTO categorias (tienda_id, nombre) VALUES (?, ?)',
                           (tienda_id, cat))

            db.commit()
            flash(f'Tienda "{nombre}" creada exitosamente. Usuario: {username}', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')

    return render_template('admin/nueva_tienda.html')


@app.route('/admin/tienda/<int:tid>/toggle')
@login_required
@superadmin_required
def admin_toggle_tienda(tid):
    db = get_db()
    tienda = db.execute('SELECT * FROM tiendas WHERE id=?', (tid,)).fetchone()
    if tienda:
        nuevo = 0 if tienda['activa'] else 1
        db.execute('UPDATE tiendas SET activa=? WHERE id=?', (nuevo, tid))
        db.commit()
        estado = 'activada' if nuevo else 'suspendida'
        flash(f'Tienda {estado} correctamente.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/tienda/<int:tid>')
@login_required
@superadmin_required
def admin_ver_tienda(tid):
    db = get_db()
    tienda = db.execute('SELECT * FROM tiendas WHERE id=?', (tid,)).fetchone()
    if not tienda:
        flash('Tienda no encontrada.', 'error')
        return redirect(url_for('admin_dashboard'))

    usuarios = db.execute(
        "SELECT * FROM usuarios WHERE tienda_id=? AND rol != 'superadmin'", (tid,)
    ).fetchall()
    ventas_hoy = db.execute('''
        SELECT COUNT(*) as n, COALESCE(SUM(total),0) as total
        FROM ventas WHERE tienda_id=? AND DATE(fecha)=DATE('now')
    ''', (tid,)).fetchone()
    ventas_mes = db.execute('''
        SELECT COUNT(*) as n, COALESCE(SUM(total),0) as total
        FROM ventas WHERE tienda_id=? AND strftime('%Y-%m', fecha)=strftime('%Y-%m','now')
    ''', (tid,)).fetchone()
    total_productos = db.execute(
        'SELECT COUNT(*) FROM productos WHERE tienda_id=? AND activo=1', (tid,)
    ).fetchone()[0]

    return render_template('admin/ver_tienda.html',
                           tienda=tienda, usuarios=usuarios,
                           ventas_hoy=ventas_hoy, ventas_mes=ventas_mes,
                           total_productos=total_productos)


@app.route('/admin/tienda/<int:tid>/usuario/nuevo', methods=['POST'])
@login_required
@superadmin_required
def admin_nuevo_usuario(tid):
    nombre   = request.form.get('nombre', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    rol      = request.form.get('rol', 'cajero')
    db = get_db()
    try:
        db.execute('''
            INSERT INTO usuarios (tienda_id, nombre, username, password, rol)
            VALUES (?, ?, ?, ?, ?)
        ''', (tid, nombre, username, hash_pw(password), rol))
        db.commit()
        flash(f'Usuario "{username}" creado correctamente.', 'success')
    except Exception as e:
        flash(f'Error: ya existe ese usuario.' if 'UNIQUE' in str(e) else str(e), 'error')
    return redirect(url_for('admin_ver_tienda', tid=tid))


# ─────────────────────────────────────────────────────────────────
# POS — Pantalla principal de ventas
# ─────────────────────────────────────────────────────────────────
@app.route('/pos')
@login_required
def pos():
    if session.get('rol') == 'superadmin':
        return redirect(url_for('admin_dashboard'))
    tienda = get_tienda()
    return render_template('pos/ventas.html', tienda=tienda)


@app.route('/api/productos/buscar')
@login_required
def api_buscar_producto():
    q = request.args.get('q', '').strip()
    tid = session.get('tienda_id')
    db = get_db()
    if not q or not tid:
        return jsonify([])
    rows = db.execute('''
        SELECT p.id, p.nombre, p.codigo_barras, p.precio, p.stock,
               c.nombre as categoria
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
        WHERE p.tienda_id=? AND p.activo=1 AND p.stock > 0
          AND (p.nombre LIKE ? OR p.codigo_barras = ?)
        ORDER BY p.nombre LIMIT 10
    ''', (tid, f'%{q}%', q)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ventas/registrar', methods=['POST'])
@login_required
def api_registrar_venta():
    data = request.get_json()
    tid  = session.get('tienda_id')
    uid  = session.get('user_id')
    items = data.get('items', [])
    metodo = data.get('metodo_pago', 'Efectivo')
    caja   = data.get('caja', 'Caja 1')

    if not items:
        return jsonify({'ok': False, 'msg': 'Carrito vacío.'})

    db = get_db()
    try:
        total = sum(i['cantidad'] * i['precio_unitario'] for i in items)
        fecha = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cur = db.execute('''
            INSERT INTO ventas (tienda_id, usuario_id, fecha, total, metodo_pago, caja)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (tid, uid, fecha, total, metodo, caja))
        venta_id = cur.lastrowid

        for item in items:
            db.execute('''
                INSERT INTO detalle_ventas (venta_id, producto_id, cantidad, precio_unitario)
                VALUES (?, ?, ?, ?)
            ''', (venta_id, item['producto_id'], item['cantidad'], item['precio_unitario']))
            db.execute('''
                UPDATE productos SET stock = stock - ? WHERE id=? AND tienda_id=?
            ''', (item['cantidad'], item['producto_id'], tid))

        db.commit()
        return jsonify({'ok': True, 'venta_id': venta_id, 'total': total})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# ─────────────────────────────────────────────────────────────────
# INVENTARIO
# ─────────────────────────────────────────────────────────────────
@app.route('/inventario')
@login_required
def inventario():
    tid = session.get('tienda_id')
    db  = get_db()
    q   = request.args.get('q', '').strip()

    if q:
        productos = db.execute('''
            SELECT p.*, c.nombre as categoria_nombre
            FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id
            WHERE p.tienda_id=? AND p.activo=1
              AND (p.nombre LIKE ? OR p.codigo_barras LIKE ?)
            ORDER BY p.nombre
        ''', (tid, f'%{q}%', f'%{q}%')).fetchall()
    else:
        productos = db.execute('''
            SELECT p.*, c.nombre as categoria_nombre
            FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id
            WHERE p.tienda_id=? AND p.activo=1
            ORDER BY p.nombre
        ''', (tid,)).fetchall()

    # Convertir a dicts para que tojson funcione en el template
    productos = [dict(p) for p in productos]

    categorias  = db.execute(
        'SELECT * FROM categorias WHERE tienda_id=? ORDER BY nombre', (tid,)
    ).fetchall()
    stock_bajo  = db.execute(
        'SELECT COUNT(*) FROM productos WHERE tienda_id=? AND activo=1 AND stock <= stock_minimo',
        (tid,)
    ).fetchone()[0]
    tienda = get_tienda()
    return render_template('pos/inventario.html', productos=productos,
                           categorias=categorias, stock_bajo=stock_bajo,
                           q=q, tienda=tienda)


@app.route('/inventario/agregar', methods=['POST'])
@login_required
def inventario_agregar():
    tid = session.get('tienda_id')
    db  = get_db()
    try:
        db.execute('''
            INSERT INTO productos
              (tienda_id, nombre, codigo_barras, precio, precio_costo,
               stock, stock_minimo, categoria_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            tid,
            request.form.get('nombre', '').strip(),
            request.form.get('codigo_barras', '').strip() or None,
            float(request.form.get('precio', 0)),
            float(request.form.get('precio_costo', 0)),
            int(request.form.get('stock', 0)),
            int(request.form.get('stock_minimo', 5)),
            request.form.get('categoria_id') or None,
        ))
        db.commit()
        flash('Producto agregado correctamente.', 'success')
    except Exception as e:
        flash(f'Error: ya existe ese código de barras.' if 'UNIQUE' in str(e) else str(e), 'error')
    return redirect(url_for('inventario'))


@app.route('/inventario/editar/<int:pid>', methods=['POST'])
@login_required
def inventario_editar(pid):
    tid = session.get('tienda_id')
    db  = get_db()
    try:
        db.execute('''
            UPDATE productos SET
              nombre=?, codigo_barras=?, precio=?, precio_costo=?,
              stock=?, stock_minimo=?, categoria_id=?
            WHERE id=? AND tienda_id=?
        ''', (
            request.form.get('nombre', '').strip(),
            request.form.get('codigo_barras', '').strip() or None,
            float(request.form.get('precio', 0)),
            float(request.form.get('precio_costo', 0)),
            int(request.form.get('stock', 0)),
            int(request.form.get('stock_minimo', 5)),
            request.form.get('categoria_id') or None,
            pid, tid
        ))
        db.commit()
        flash('Producto actualizado.', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('inventario'))


@app.route('/inventario/eliminar/<int:pid>')
@login_required
def inventario_eliminar(pid):
    tid = session.get('tienda_id')
    db  = get_db()
    db.execute('UPDATE productos SET activo=0 WHERE id=? AND tienda_id=?', (pid, tid))
    db.commit()
    flash('Producto eliminado.', 'success')
    return redirect(url_for('inventario'))


@app.route('/inventario/categoria/nueva', methods=['POST'])
@login_required
def categoria_nueva():
    tid    = session.get('tienda_id')
    nombre = request.form.get('nombre', '').strip()
    if nombre:
        db = get_db()
        try:
            db.execute('INSERT INTO categorias (tienda_id, nombre) VALUES (?,?)', (tid, nombre))
            db.commit()
            flash('Categoría creada.', 'success')
        except Exception:
            flash('Ya existe esa categoría.', 'error')
    return redirect(url_for('inventario'))


# ─────────────────────────────────────────────────────────────────
# REPORTES
# ─────────────────────────────────────────────────────────────────
@app.route('/reportes')
@login_required
def reportes():
    tid   = session.get('tienda_id')
    db    = get_db()
    hoy   = datetime.date.today().strftime('%Y-%m-%d')
    ini   = request.args.get('ini', hoy)
    fin   = request.args.get('fin', hoy)

    # Resumen del período
    resumen = db.execute('''
        SELECT COUNT(*) as num_ventas,
               COALESCE(SUM(total), 0) as total,
               COALESCE(SUM(CASE WHEN metodo_pago='Efectivo' THEN total ELSE 0 END),0) as efectivo,
               COALESCE(SUM(CASE WHEN metodo_pago='Tarjeta'  THEN total ELSE 0 END),0) as tarjeta
        FROM ventas WHERE tienda_id=? AND DATE(fecha) BETWEEN ? AND ?
    ''', (tid, ini, fin)).fetchone()

    # Ventas del período
    ventas = db.execute('''
        SELECT v.id, v.fecha, v.total, v.metodo_pago, v.caja, u.nombre as cajero
        FROM ventas v JOIN usuarios u ON v.usuario_id=u.id
        WHERE v.tienda_id=? AND DATE(v.fecha) BETWEEN ? AND ?
        ORDER BY v.fecha DESC
    ''', (tid, ini, fin)).fetchall()

    # Más vendidos
    mas_vendidos = db.execute('''
        SELECT p.nombre, SUM(dv.cantidad) as unidades,
               SUM(dv.cantidad * dv.precio_unitario) as total
        FROM detalle_ventas dv
        JOIN productos p ON dv.producto_id=p.id
        JOIN ventas v ON dv.venta_id=v.id
        WHERE v.tienda_id=? AND DATE(v.fecha) BETWEEN ? AND ?
        GROUP BY p.id ORDER BY unidades DESC LIMIT 10
    ''', (tid, ini, fin)).fetchall()

    # Ventas por día (para gráfica)
    por_dia = db.execute('''
        SELECT DATE(fecha) as dia, COUNT(*) as n, SUM(total) as total
        FROM ventas WHERE tienda_id=? AND DATE(fecha) BETWEEN ? AND ?
        GROUP BY DATE(fecha) ORDER BY dia
    ''', (tid, ini, fin)).fetchall()

    tienda = get_tienda()
    return render_template('pos/reportes.html',
                           resumen=resumen, ventas=ventas,
                           mas_vendidos=mas_vendidos,
                           por_dia=json.dumps([dict(r) for r in por_dia]),
                           ini=ini, fin=fin, tienda=tienda,
                           hoy=hoy,
                           mes_ini=datetime.date.today().replace(day=1).strftime('%Y-%m-%d'))


@app.route('/reportes/venta/<int:vid>')
@login_required
def ver_venta(vid):
    tid = session.get('tienda_id')
    db  = get_db()
    venta = db.execute('''
        SELECT v.*, u.nombre as cajero
        FROM ventas v JOIN usuarios u ON v.usuario_id=u.id
        WHERE v.id=? AND v.tienda_id=?
    ''', (vid, tid)).fetchone()
    if not venta:
        flash('Venta no encontrada.', 'error')
        return redirect(url_for('reportes'))
    detalle = db.execute('''
        SELECT p.nombre, dv.cantidad, dv.precio_unitario,
               dv.cantidad * dv.precio_unitario as subtotal
        FROM detalle_ventas dv JOIN productos p ON dv.producto_id=p.id
        WHERE dv.venta_id=?
    ''', (vid,)).fetchall()
    tienda = get_tienda()
    return render_template('pos/ver_venta.html', venta=venta, detalle=detalle, tienda=tienda)


# ─────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE TIENDA
# ─────────────────────────────────────────────────────────────────
@app.route('/configuracion', methods=['GET', 'POST'])
@login_required
def configuracion():
    if session.get('rol') not in ('admin', 'superadmin'):
        flash('Solo administradores pueden acceder.', 'error')
        return redirect(url_for('pos'))

    tid = session.get('tienda_id')
    db  = get_db()

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'tienda':
            db.execute('''
                UPDATE tiendas SET nombre=?, ciudad=?, direccion=?, telefono=?, mensaje_ticket=?
                WHERE id=?
            ''', (
                request.form.get('nombre'), request.form.get('ciudad'),
                request.form.get('direccion'), request.form.get('telefono'),
                request.form.get('mensaje_ticket'), tid
            ))
            db.commit()
            session['tienda_nombre'] = request.form.get('nombre')
            flash('Datos de tienda actualizados.', 'success')

        elif accion == 'usuario_nuevo':
            nombre   = request.form.get('nombre', '').strip()
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '').strip()
            rol      = request.form.get('rol', 'cajero')
            try:
                db.execute('''
                    INSERT INTO usuarios (tienda_id, nombre, username, password, rol)
                    VALUES (?, ?, ?, ?, ?)
                ''', (tid, nombre, username, hash_pw(password), rol))
                db.commit()
                flash(f'Usuario "{username}" creado.', 'success')
            except Exception:
                flash('Ese nombre de usuario ya existe.', 'error')

        elif accion == 'cambiar_password':
            uid_target = int(request.form.get('usuario_id'))
            nueva      = request.form.get('nueva_password', '').strip()
            if len(nueva) >= 4:
                db.execute('UPDATE usuarios SET password=? WHERE id=? AND tienda_id=?',
                           (hash_pw(nueva), uid_target, tid))
                db.commit()
                flash('Contraseña actualizada.', 'success')
            else:
                flash('La contraseña debe tener al menos 4 caracteres.', 'error')

        elif accion == 'eliminar_usuario':
            uid_target = int(request.form.get('usuario_id'))
            # No eliminar el último admin
            admins = db.execute(
                "SELECT COUNT(*) FROM usuarios WHERE tienda_id=? AND rol='admin' AND activo=1",
                (tid,)
            ).fetchone()[0]
            usuario = db.execute('SELECT rol FROM usuarios WHERE id=?', (uid_target,)).fetchone()
            if usuario and usuario['rol'] == 'admin' and admins <= 1:
                flash('No puedes eliminar el único administrador.', 'error')
            else:
                db.execute('UPDATE usuarios SET activo=0 WHERE id=? AND tienda_id=?',
                           (uid_target, tid))
                db.commit()
                flash('Usuario eliminado.', 'success')

        return redirect(url_for('configuracion'))

    tienda   = get_tienda()
    usuarios = db.execute(
        "SELECT * FROM usuarios WHERE tienda_id=? AND activo=1 ORDER BY rol, nombre", (tid,)
    ).fetchall()
    return render_template('pos/configuracion.html', tienda=tienda, usuarios=usuarios)


# ─────────────────────────────────────────────────────────────────
# API — Dashboard stats en tiempo real
# ─────────────────────────────────────────────────────────────────
@app.route('/api/dashboard/stats')
@login_required
def api_dashboard_stats():
    tid = session.get('tienda_id')
    db  = get_db()
    hoy = datetime.date.today().strftime('%Y-%m-%d')
    ventas_hoy = db.execute(
        'SELECT COUNT(*) as n, COALESCE(SUM(total),0) as t FROM ventas WHERE tienda_id=? AND DATE(fecha)=?',
        (tid, hoy)
    ).fetchone()
    stock_bajo = db.execute(
        'SELECT COUNT(*) FROM productos WHERE tienda_id=? AND activo=1 AND stock <= stock_minimo',
        (tid,)
    ).fetchone()[0]
    return jsonify({
        'num_ventas':  ventas_hoy['n'],
        'total_dia':   round(ventas_hoy['t'], 2),
        'stock_alerta': stock_bajo,
    })


# ─────────────────────────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    import traceback
    return render_template('500.html', error=traceback.format_exc()), 500


# ─────────────────────────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────────────────────────
try:
    init_db()
except Exception as e:
    print(f'⚠ init_db: {e}')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print('╔══════════════════════════════════╗')
    print('║   VENTIX WEB  —  POS Sistema     ║')
    print(f'║   http://localhost:{port}          ║')
    print('╚══════════════════════════════════╝')
    app.run(host='0.0.0.0', port=port, debug=False)
