from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash, g)
from functools import wraps
import os, datetime, json
from database.db import get_db, close_db, hash_pw, init_db, query, execute

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ventix_dev_key_cambiar_en_produccion')
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

app.teardown_appcontext(close_db)


# ── Auth decorators ──────────────────────────────────────────────
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
    tid = session.get('tienda_id')
    if not tid:
        return None
    return query("SELECT * FROM tiendas WHERE id=?", (tid,), one=True)


# ── LOGIN / LOGOUT ───────────────────────────────────────────────
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
        user = query(
            'SELECT * FROM usuarios WHERE username=? AND password=? AND activo=1',
            (username, hash_pw(password)), one=True
        )
        if user:
            if user['rol'] != 'superadmin':
                tienda = query(
                    'SELECT * FROM tiendas WHERE id=? AND activa=1',
                    (user['tienda_id'],), one=True
                )
                if not tienda:
                    error = 'Tu tienda está suspendida. Contacta a soporte.'
                else:
                    session.update({
                        'user_id': user['id'], 'username': user['username'],
                        'nombre': user['nombre'], 'rol': user['rol'],
                        'tienda_id': user['tienda_id'],
                        'tienda_nombre': tienda['nombre']
                    })
                    return redirect(url_for('pos'))
            else:
                session.update({
                    'user_id': user['id'], 'username': user['username'],
                    'nombre': user['nombre'], 'rol': 'superadmin',
                    'tienda_id': None
                })
                return redirect(url_for('admin_dashboard'))
        else:
            error = 'Usuario o contraseña incorrectos.'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── SUPER ADMIN ──────────────────────────────────────────────────
@app.route('/admin')
@login_required
@superadmin_required
def admin_dashboard():
    tiendas = query('''
        SELECT t.*,
               COUNT(DISTINCT u.id) as num_usuarios,
               COUNT(DISTINCT v.id) as num_ventas,
               COALESCE(SUM(v.total), 0) as total_ventas
        FROM tiendas t
        LEFT JOIN usuarios u ON u.tienda_id = t.id
        LEFT JOIN ventas v ON v.tienda_id = t.id
        GROUP BY t.id, t.nombre, t.ciudad, t.direccion, t.telefono,
                 t.plan, t.activa, t.fecha_alta, t.mensaje_ticket
        ORDER BY t.fecha_alta DESC
    ''')
    stats = {
        'total_tiendas':   query('SELECT COUNT(*) as n FROM tiendas', one=True)['n'],
        'tiendas_activas': query('SELECT COUNT(*) as n FROM tiendas WHERE activa=1', one=True)['n'],
        'total_ventas':    query('SELECT COALESCE(SUM(total),0) as t FROM ventas', one=True)['t'],
        'total_usuarios':  query("SELECT COUNT(*) as n FROM usuarios WHERE rol != 'superadmin'", one=True)['n'],
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
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        nombre_admin = request.form.get('nombre_admin', '').strip()

        if not nombre or not username or not password:
            flash('Nombre, usuario y contraseña son obligatorios.', 'error')
            return render_template('admin/nueva_tienda.html')

        try:
            tid = execute(
                'INSERT INTO tiendas (nombre, ciudad, direccion, telefono, plan) VALUES (?,?,?,?,?)',
                (nombre, ciudad, direccion, telefono, plan)
            )
            execute(
                "INSERT INTO usuarios (tienda_id, nombre, username, password, rol) VALUES (?,?,?,?,'admin')",
                (tid, nombre_admin or nombre, username, hash_pw(password))
            )
            for cat in ['Bebidas','Lácteos','Carnes','Abarrotes','Dulces','Limpieza','Otros']:
                execute('INSERT INTO categorias (tienda_id, nombre) VALUES (?,?)', (tid, cat))

            flash(f'Tienda "{nombre}" creada. Usuario: {username}', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')

    return render_template('admin/nueva_tienda.html')


@app.route('/admin/tienda/<int:tid>/toggle')
@login_required
@superadmin_required
def admin_toggle_tienda(tid):
    tienda = query('SELECT * FROM tiendas WHERE id=?', (tid,), one=True)
    if tienda:
        execute('UPDATE tiendas SET activa=? WHERE id=?',
                (0 if tienda['activa'] else 1, tid))
        flash(f'Tienda {"activada" if not tienda["activa"] else "suspendida"}.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/tienda/<int:tid>')
@login_required
@superadmin_required
def admin_ver_tienda(tid):
    tienda = query('SELECT * FROM tiendas WHERE id=?', (tid,), one=True)
    if not tienda:
        flash('Tienda no encontrada.', 'error')
        return redirect(url_for('admin_dashboard'))

    usuarios = query(
        "SELECT * FROM usuarios WHERE tienda_id=? AND rol != 'superadmin'", (tid,)
    )
    ventas_hoy = query('''
        SELECT COUNT(*) as n, COALESCE(SUM(total),0) as total
        FROM ventas WHERE tienda_id=? AND DATE(fecha)=CURRENT_DATE
    ''', (tid,), one=True)
    ventas_mes = query('''
        SELECT COUNT(*) as n, COALESCE(SUM(total),0) as total
        FROM ventas WHERE tienda_id=?
        AND DATE_TRUNC('month', fecha::timestamp)=DATE_TRUNC('month', CURRENT_DATE)
    ''', (tid,), one=True) if os.environ.get('DATABASE_URL') else query('''
        SELECT COUNT(*) as n, COALESCE(SUM(total),0) as total
        FROM ventas WHERE tienda_id=?
        AND strftime('%Y-%m', fecha)=strftime('%Y-%m','now')
    ''', (tid,), one=True)
    total_productos = query(
        'SELECT COUNT(*) as n FROM productos WHERE tienda_id=? AND activo=1', (tid,), one=True
    )['n']

    return render_template('admin/ver_tienda.html',
                           tienda=tienda, usuarios=usuarios,
                           ventas_hoy=ventas_hoy, ventas_mes=ventas_mes,
                           total_productos=total_productos)


@app.route('/admin/tienda/<int:tid>/usuario/nuevo', methods=['POST'])
@login_required
@superadmin_required
def admin_nuevo_usuario(tid):
    try:
        execute('''
            INSERT INTO usuarios (tienda_id, nombre, username, password, rol)
            VALUES (?,?,?,?,?)
        ''', (tid,
              request.form.get('nombre', '').strip(),
              request.form.get('username', '').strip(),
              hash_pw(request.form.get('password', '')),
              request.form.get('rol', 'cajero')))
        flash('Usuario creado correctamente.', 'success')
    except Exception as e:
        flash('Error: ya existe ese usuario.' if 'unique' in str(e).lower() else str(e), 'error')
    return redirect(url_for('admin_ver_tienda', tid=tid))


# ── POS ──────────────────────────────────────────────────────────
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
    q   = request.args.get('q', '').strip()
    tid = session.get('tienda_id')
    if not q or not tid:
        return jsonify([])
    rows = query('''
        SELECT p.id, p.nombre, p.codigo_barras, p.precio, p.stock,
               c.nombre as categoria
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
        WHERE p.tienda_id=? AND p.activo=1 AND p.stock > 0
          AND (p.nombre ILIKE ? OR p.codigo_barras = ?)
        ORDER BY p.nombre LIMIT 10
    ''' if os.environ.get('DATABASE_URL') else '''
        SELECT p.id, p.nombre, p.codigo_barras, p.precio, p.stock,
               c.nombre as categoria
        FROM productos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
        WHERE p.tienda_id=? AND p.activo=1 AND p.stock > 0
          AND (p.nombre LIKE ? OR p.codigo_barras = ?)
        ORDER BY p.nombre LIMIT 10
    ''', (tid, f'%{q}%', q))
    return jsonify(rows)


@app.route('/api/ventas/registrar', methods=['POST'])
@login_required
def api_registrar_venta():
    data   = request.get_json()
    tid    = session.get('tienda_id')
    uid    = session.get('user_id')
    items  = data.get('items', [])
    metodo = data.get('metodo_pago', 'Efectivo')
    caja   = data.get('caja', 'Caja 1')

    if not items:
        return jsonify({'ok': False, 'msg': 'Carrito vacío.'})

    try:
        total = sum(i['cantidad'] * i['precio_unitario'] for i in items)
        fecha = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        vid = execute('''
            INSERT INTO ventas (tienda_id, usuario_id, fecha, total, metodo_pago, caja)
            VALUES (?,?,?,?,?,?)
        ''', (tid, uid, fecha, total, metodo, caja))

        for item in items:
            execute('''
                INSERT INTO detalle_ventas (venta_id, producto_id, cantidad, precio_unitario)
                VALUES (?,?,?,?)
            ''', (vid, item['producto_id'], item['cantidad'], item['precio_unitario']))
            execute('''
                UPDATE productos SET stock = stock - ? WHERE id=? AND tienda_id=?
            ''', (item['cantidad'], item['producto_id'], tid))

        return jsonify({'ok': True, 'venta_id': vid, 'total': total})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


# ── INVENTARIO ───────────────────────────────────────────────────
@app.route('/inventario')
@login_required
def inventario():
    tid = session.get('tienda_id')
    q   = request.args.get('q', '').strip()
    like_op = 'ILIKE' if os.environ.get('DATABASE_URL') else 'LIKE'

    if q:
        productos = query(f'''
            SELECT p.*, c.nombre as categoria_nombre
            FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id
            WHERE p.tienda_id=? AND p.activo=1
              AND (p.nombre {like_op} ? OR p.codigo_barras {like_op} ?)
            ORDER BY p.nombre
        ''', (tid, f'%{q}%', f'%{q}%'))
    else:
        productos = query('''
            SELECT p.*, c.nombre as categoria_nombre
            FROM productos p LEFT JOIN categorias c ON p.categoria_id=c.id
            WHERE p.tienda_id=? AND p.activo=1
            ORDER BY p.nombre
        ''', (tid,))

    categorias = query('SELECT * FROM categorias WHERE tienda_id=? ORDER BY nombre', (tid,))
    stock_bajo = query(
        'SELECT COUNT(*) as n FROM productos WHERE tienda_id=? AND activo=1 AND stock <= stock_minimo',
        (tid,), one=True
    )['n']
    tienda = get_tienda()
    return render_template('pos/inventario.html', productos=productos,
                           categorias=categorias, stock_bajo=stock_bajo,
                           q=q, tienda=tienda)


@app.route('/inventario/agregar', methods=['POST'])
@login_required
def inventario_agregar():
    tid = session.get('tienda_id')
    try:
        execute('''
            INSERT INTO productos
              (tienda_id, nombre, codigo_barras, precio, precio_costo,
               stock, stock_minimo, categoria_id)
            VALUES (?,?,?,?,?,?,?,?)
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
        flash('Producto agregado correctamente.', 'success')
    except Exception as e:
        flash('Error: ya existe ese código de barras.' if 'unique' in str(e).lower() else str(e), 'error')
    return redirect(url_for('inventario'))


@app.route('/inventario/editar/<int:pid>', methods=['POST'])
@login_required
def inventario_editar(pid):
    tid = session.get('tienda_id')
    try:
        execute('''
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
        flash('Producto actualizado.', 'success')
    except Exception as e:
        flash(str(e), 'error')
    return redirect(url_for('inventario'))


@app.route('/inventario/eliminar/<int:pid>')
@login_required
def inventario_eliminar(pid):
    execute('UPDATE productos SET activo=0 WHERE id=? AND tienda_id=?',
            (pid, session.get('tienda_id')))
    flash('Producto eliminado.', 'success')
    return redirect(url_for('inventario'))


@app.route('/inventario/categoria/nueva', methods=['POST'])
@login_required
def categoria_nueva():
    nombre = request.form.get('nombre', '').strip()
    if nombre:
        try:
            execute('INSERT INTO categorias (tienda_id, nombre) VALUES (?,?)',
                    (session.get('tienda_id'), nombre))
            flash('Categoría creada.', 'success')
        except Exception:
            flash('Ya existe esa categoría.', 'error')
    return redirect(url_for('inventario'))


# ── REPORTES ─────────────────────────────────────────────────────
@app.route('/reportes')
@login_required
def reportes():
    tid  = session.get('tienda_id')
    hoy  = datetime.date.today().strftime('%Y-%m-%d')
    ini  = request.args.get('ini', hoy)
    fin  = request.args.get('fin', hoy)
    mes_ini = datetime.date.today().replace(day=1).strftime('%Y-%m-%d')

    resumen = query('''
        SELECT COUNT(*) as num_ventas,
               COALESCE(SUM(total), 0) as total,
               COALESCE(SUM(CASE WHEN metodo_pago='Efectivo' THEN total ELSE 0 END),0) as efectivo,
               COALESCE(SUM(CASE WHEN metodo_pago='Tarjeta'  THEN total ELSE 0 END),0) as tarjeta
        FROM ventas WHERE tienda_id=? AND DATE(fecha) BETWEEN ? AND ?
    ''', (tid, ini, fin), one=True)

    ventas = query('''
        SELECT v.id, v.fecha, v.total, v.metodo_pago, v.caja, u.nombre as cajero
        FROM ventas v JOIN usuarios u ON v.usuario_id=u.id
        WHERE v.tienda_id=? AND DATE(v.fecha) BETWEEN ? AND ?
        ORDER BY v.fecha DESC
    ''', (tid, ini, fin))

    mas_vendidos = query('''
        SELECT p.nombre, SUM(dv.cantidad) as unidades,
               SUM(dv.cantidad * dv.precio_unitario) as total
        FROM detalle_ventas dv
        JOIN productos p ON dv.producto_id=p.id
        JOIN ventas v ON dv.venta_id=v.id
        WHERE v.tienda_id=? AND DATE(v.fecha) BETWEEN ? AND ?
        GROUP BY p.id, p.nombre ORDER BY unidades DESC LIMIT 10
    ''', (tid, ini, fin))

    por_dia = query('''
        SELECT DATE(fecha) as dia, COUNT(*) as n,
               COALESCE(SUM(total),0) as total
        FROM ventas WHERE tienda_id=? AND DATE(fecha) BETWEEN ? AND ?
        GROUP BY DATE(fecha) ORDER BY dia
    ''', (tid, ini, fin))

    por_dia_json = []
    for r in por_dia:
        row = dict(r)
        if hasattr(row.get('dia'), 'strftime'):
            row['dia'] = row['dia'].strftime('%Y-%m-%d')
        por_dia_json.append(row)

    tienda = get_tienda()
    return render_template('pos/reportes.html',
                           resumen=resumen, ventas=ventas,
                           mas_vendidos=mas_vendidos,
                           por_dia=json.dumps(por_dia_json),
                           ini=ini, fin=fin, hoy=hoy,
                           mes_ini=mes_ini, tienda=tienda)


@app.route('/reportes/venta/<int:vid>')
@login_required
def ver_venta(vid):
    tid   = session.get('tienda_id')
    venta = query('''
        SELECT v.*, u.nombre as cajero
        FROM ventas v JOIN usuarios u ON v.usuario_id=u.id
        WHERE v.id=? AND v.tienda_id=?
    ''', (vid, tid), one=True)
    if not venta:
        flash('Venta no encontrada.', 'error')
        return redirect(url_for('reportes'))
    detalle = query('''
        SELECT p.nombre, dv.cantidad, dv.precio_unitario,
               dv.cantidad * dv.precio_unitario as subtotal
        FROM detalle_ventas dv JOIN productos p ON dv.producto_id=p.id
        WHERE dv.venta_id=?
    ''', (vid,))
    tienda = get_tienda()
    return render_template('pos/ver_venta.html',
                           venta=venta, detalle=detalle, tienda=tienda)


# ── CONFIGURACIÓN ────────────────────────────────────────────────
@app.route('/configuracion', methods=['GET', 'POST'])
@login_required
def configuracion():
    if session.get('rol') not in ('admin', 'superadmin'):
        flash('Solo administradores pueden acceder.', 'error')
        return redirect(url_for('pos'))

    tid = session.get('tienda_id')

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'tienda':
            execute('''
                UPDATE tiendas SET nombre=?, ciudad=?, direccion=?, telefono=?, mensaje_ticket=?
                WHERE id=?
            ''', (request.form.get('nombre'), request.form.get('ciudad'),
                  request.form.get('direccion'), request.form.get('telefono'),
                  request.form.get('mensaje_ticket'), tid))
            session['tienda_nombre'] = request.form.get('nombre')
            flash('Datos actualizados.', 'success')

        elif accion == 'usuario_nuevo':
            try:
                execute('''
                    INSERT INTO usuarios (tienda_id, nombre, username, password, rol)
                    VALUES (?,?,?,?,?)
                ''', (tid,
                      request.form.get('nombre', '').strip(),
                      request.form.get('username', '').strip(),
                      hash_pw(request.form.get('password', '')),
                      request.form.get('rol', 'cajero')))
                flash('Usuario creado.', 'success')
            except
