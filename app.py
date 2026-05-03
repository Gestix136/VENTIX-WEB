from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory, g
from functools import wraps
import hashlib, os, datetime, sqlite3

app = Flask(__name__)

# ── Configuración — variables de entorno en producción, fallback para desarrollo local ──
app.secret_key = os.environ.get('SECRET_KEY', 'gestix_dev_key_cambiar_en_produccion')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_PATH       = os.environ.get('DATABASE_URL', os.path.join(BASE_DIR, 'database', 'gestix.db'))
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'uploads'))

app.config['UPLOAD_FOLDER']      = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.config['DEBUG']              = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

# ─────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    try:
        db = g.pop('db', None)
        if db:
            db.close()
    except Exception:
        pass

def hash_pw(p):
    return hashlib.sha256(p.encode()).hexdigest()

# ─────────────────────────────────────────
# Auth decorators
# ─────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*a, **k)
    return d

def role_required(*roles):
    def dec(f):
        @wraps(f)
        def d(*a, **k):
            if session.get('role') not in roles:
                flash('Sin permiso para esta acción', 'error')
                return redirect(url_for('inventario'))
            return f(*a, **k)
        return d
    return dec

def perm_required(perm):
    def dec(f):
        @wraps(f)
        def d(*a, **k):
            if session.get('role') == 'Administrador':
                return f(*a, **k)
            if 'permisos' not in session and 'user_id' in session:
                session['permisos'] = _load_permisos(session['user_id'])
            if not session.get('permisos', {}).get(perm, True):
                flash('Sin permiso para esta sección', 'error')
                return redirect(url_for('inventario'))
            return f(*a, **k)
        return d
    return dec

def _load_permisos(uid):
    try:
        p = get_db().execute('SELECT * FROM usuario_permisos WHERE usuario_id=?', (uid,)).fetchone()
        keys = ['inventario','entregas','solicitudes','historial','reportes','seguimiento','trabajos_ver','trabajos_subir','usuarios']
        if not p:
            return {k: True for k in keys}
        return {
            'inventario':    bool(p['puede_inventario']),
            'entregas':      bool(p['puede_entregas']),
            'solicitudes':   bool(p['puede_solicitudes']),
            'historial':     bool(p['puede_historial']),
            'reportes':      bool(p['puede_reportes']),
            'seguimiento':   bool(p['puede_seguimiento']),
            'trabajos_ver':  bool(p['puede_trabajos_ver']),
            'trabajos_subir':bool(p['puede_trabajos_subir']),
            'usuarios':      bool(p['puede_usuarios']),
        }
    except Exception:
        return {k: True for k in ['inventario','entregas','solicitudes','historial','reportes','seguimiento','trabajos_ver','trabajos_subir','usuarios']}

@app.context_processor
def inject_globals():
    default_perms = {k: True for k in ['inventario','entregas','solicitudes','historial','reportes','seguimiento','trabajos_ver','trabajos_subir','usuarios']}
    return {
        'now': datetime.datetime.now().strftime('%d/%m/%Y %H:%M'),
        'permisos': session.get('permisos', default_perms),
    }

# ─────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        u = get_db().execute(
            'SELECT * FROM usuarios WHERE username=? AND activo=1', (username,)
        ).fetchone()
        if u and u['password'] == hash_pw(password):
            session.update({
                'user_id':  u['id'],
                'username': u['username'],
                'nombre':   u['nombre'],
                'role':     u['rol'],
                'area':     u['area'],
                'permisos': _load_permisos(u['id']),
            })
            return redirect(url_for('dashboard'))
        error = 'Usuario o contraseña incorrectos'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─────────────────────────────────────────
# Inventario
# ─────────────────────────────────────────
@app.route('/inventario')
@login_required
@perm_required('inventario')
def inventario():
    db = get_db()
    cat    = request.args.get('categoria', '')
    buscar = request.args.get('buscar', '')
    sb     = request.args.get('stock_bajo', '0')
    page   = max(1, int(request.args.get('page', 1)))
    pp     = 25

    q = 'SELECT * FROM productos WHERE activo=1'
    p = []
    if cat:    q += ' AND categoria=?';                  p.append(cat)
    if buscar: q += ' AND (nombre LIKE ? OR codigo LIKE ?)'; p += [f'%{buscar}%'] * 2
    if sb == '1': q += ' AND stock<=stock_minimo'

    try:
        total = db.execute(q.replace('SELECT *', 'SELECT COUNT(*)'), p).fetchone()[0]
    except Exception:
        total = 0

    prods = db.execute(q + f' ORDER BY nombre LIMIT {pp} OFFSET {(page-1)*pp}', p).fetchall()
    cats  = db.execute('SELECT DISTINCT categoria FROM productos WHERE activo=1 ORDER BY categoria').fetchall()
    stats = db.execute(
        'SELECT COUNT(*) as total, SUM(CASE WHEN stock<=stock_minimo THEN 1 ELSE 0 END) as bajo FROM productos WHERE activo=1'
    ).fetchone()

    return render_template('inventario.html',
        productos=prods, categorias=cats, stats=stats,
        total=total, page=page, per_page=pp,
        categoria=cat, buscar=buscar, stock_bajo=sb,
        total_pages=max(1, (total + pp - 1) // pp),
        active_tab='inventario')

@app.route('/inventario/agregar', methods=['POST'])
@login_required
@role_required('Administrador', 'Supervisor', 'Producción', 'General')
def agregar_producto():
    db = get_db()
    codigo = request.form.get('codigo', '').strip()
    nombre = request.form.get('nombre', '').strip()
    if not codigo or not nombre:
        flash('Código y nombre son requeridos', 'error')
        return redirect(url_for('inventario'))
    # Verificar código duplicado
    if db.execute('SELECT id FROM productos WHERE codigo=? AND activo=1', (codigo,)).fetchone():
        flash('Ya existe un producto con ese código', 'error')
        return redirect(url_for('inventario'))
    try:
        stock_inicial = max(0, int(request.form.get('stock', 0)))
        stock_min     = max(0, int(request.form.get('stock_minimo', 5)))
    except ValueError:
        flash('Stock debe ser un número', 'error')
        return redirect(url_for('inventario'))

    db.execute(
        'INSERT INTO productos (codigo,nombre,categoria,stock,unidad,stock_minimo,descripcion,activo) VALUES (?,?,?,?,?,?,?,1)',
        (codigo, nombre, request.form.get('categoria',''), stock_inicial,
         request.form.get('unidad','pza'), stock_min, request.form.get('descripcion',''))
    )
    db.commit()
    prod = db.execute('SELECT id FROM productos WHERE codigo=?', (codigo,)).fetchone()
    if prod and stock_inicial > 0:
        db.execute(
            'INSERT INTO movimientos (producto_id,tipo,cantidad,motivo,area,usuario_id,fecha) VALUES (?,?,?,?,?,?,?)',
            (prod['id'], 'entrada', stock_inicial, 'Stock inicial', session['area'], session['user_id'], datetime.datetime.now())
        )
        db.commit()
    flash('Producto agregado correctamente', 'success')
    return redirect(url_for('inventario'))

@app.route('/inventario/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('Administrador', 'Supervisor', 'Producción', 'General')
def editar_producto(id):
    db = get_db()
    prod = db.execute('SELECT * FROM productos WHERE id=?', (id,)).fetchone()
    if not prod:
        flash('Producto no encontrado', 'error')
        return redirect(url_for('inventario'))
    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        # Verificar código duplicado (excluyendo el actual)
        dup = db.execute('SELECT id FROM productos WHERE codigo=? AND id!=? AND activo=1', (codigo, id)).fetchone()
        if dup:
            flash('Ya existe otro producto con ese código', 'error')
            return render_template('editar_producto.html', producto=prod, active_tab='inventario')
        db.execute(
            'UPDATE productos SET codigo=?,nombre=?,categoria=?,unidad=?,stock=?,stock_minimo=?,descripcion=? WHERE id=?',
            (codigo, request.form['nombre'], request.form['categoria'],
             request.form['unidad'], max(0, int(request.form.get('stock', 0))),
             max(0, int(request.form.get('stock_minimo', 0))),
             request.form.get('descripcion',''), id)
        )
        db.commit()
        flash('Producto actualizado', 'success')
        return redirect(url_for('inventario'))
    return render_template('editar_producto.html', producto=prod, active_tab='inventario')

@app.route('/inventario/desactivar/<int:id>')
@login_required
@role_required('Administrador')
def desactivar_producto(id):
    db = get_db()
    db.execute('UPDATE productos SET activo=0 WHERE id=?', (id,))
    db.commit()
    flash('Producto desactivado', 'info')
    return redirect(url_for('inventario'))

@app.route('/inventario/exportar')
@login_required
def exportar_inventario():
    import csv, io
    from flask import Response
    rows = get_db().execute(
        'SELECT codigo,nombre,categoria,stock,unidad,stock_minimo FROM productos WHERE activo=1 ORDER BY nombre'
    ).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Código','Nombre','Categoría','Stock','Unidad','Stock Mínimo'])
    for r in rows:
        w.writerow(list(r))
    out.seek(0)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=inventario_gestix.csv'})

# ─────────────────────────────────────────
# Entregas
# ─────────────────────────────────────────
@app.route('/entregas')
@login_required
@perm_required('entregas')
def entregas():
    db     = get_db()
    buscar = request.args.get('buscar', '')
    cat    = request.args.get('categoria', '')
    q = 'SELECT * FROM productos WHERE activo=1 AND stock>0'
    p = []
    if buscar: q += ' AND (nombre LIKE ? OR codigo LIKE ?)'; p += [f'%{buscar}%'] * 2
    if cat:    q += ' AND categoria=?';                      p.append(cat)
    prods = db.execute(q + ' ORDER BY nombre', p).fetchall()
    cats  = db.execute('SELECT DISTINCT categoria FROM productos WHERE activo=1 ORDER BY categoria').fetchall()
    ult   = db.execute('SELECT folio FROM entregas ORDER BY id DESC LIMIT 1').fetchone()
    folio = f"EN-{(int(ult['folio'].split('-')[1]) + 1):05d}" if ult else 'EN-00001'
    return render_template('entregas.html',
        productos=prods, categorias=cats, folio=folio,
        buscar=buscar, categoria=cat, active_tab='entregas')

@app.route('/entregas/registrar', methods=['POST'])
@login_required
@role_required('Administrador', 'Supervisor', 'Producción', 'General')
def registrar_entrega():
    db    = get_db()
    data  = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Datos inválidos'}), 400
    items = data.get('items', [])
    if not items:
        return jsonify({'error': 'Sin productos seleccionados'}), 400

    ult   = db.execute('SELECT folio FROM entregas ORDER BY id DESC LIMIT 1').fetchone()
    folio = f"EN-{(int(ult['folio'].split('-')[1]) + 1):05d}" if ult else 'EN-00001'
    now   = datetime.datetime.now()

    eid = db.execute(
        'INSERT INTO entregas (folio,motivo,area,comentarios,usuario_id,fecha) VALUES (?,?,?,?,?,?)',
        (folio, data.get('motivo',''), data.get('area',''), data.get('comentarios',''), session['user_id'], now)
    ).lastrowid

    errores = []
    for item in items:
        try:
            prod = db.execute('SELECT * FROM productos WHERE id=?', (item['id'],)).fetchone()
            if not prod:
                errores.append(f"Producto ID {item['id']} no encontrado")
                continue
            cant = int(item['cantidad'])
            if cant <= 0:
                continue
            if prod['stock'] < cant:
                errores.append(f"Stock insuficiente para {prod['nombre']} (disponible: {prod['stock']})")
                continue
            db.execute('UPDATE productos SET stock=stock-? WHERE id=?', (cant, item['id']))
            db.execute('INSERT INTO entrega_items (entrega_id,producto_id,cantidad) VALUES (?,?,?)', (eid, item['id'], cant))
            db.execute(
                'INSERT INTO movimientos (producto_id,tipo,cantidad,motivo,area,usuario_id,fecha) VALUES (?,?,?,?,?,?,?)',
                (item['id'], 'salida', cant, data.get('motivo',''), data.get('area',''), session['user_id'], now)
            )
        except (KeyError, ValueError, TypeError) as e:
            errores.append(f"Error en item: {str(e)}")

    db.commit()
    resp = {'success': True, 'folio': folio}
    if errores:
        resp['advertencias'] = errores
    return jsonify(resp)

# ─────────────────────────────────────────
# Solicitudes
# ─────────────────────────────────────────
@app.route('/solicitudes')
@login_required
@perm_required('solicitudes')
def solicitudes():
    db     = get_db()
    estado = request.args.get('estado', '')
    q = '''SELECT s.*, p.nombre as producto_nombre, u.nombre as usuario_nombre
           FROM solicitudes s
           JOIN productos p ON s.producto_id=p.id
           JOIN usuarios u ON s.usuario_id=u.id'''
    p = []
    if estado: q += ' WHERE s.estado=?'; p.append(estado)
    return render_template('solicitudes.html',
        solicitudes=db.execute(q + ' ORDER BY s.fecha DESC', p).fetchall(),
        productos=db.execute('SELECT * FROM productos WHERE activo=1 ORDER BY nombre').fetchall(),
        estado=estado, active_tab='solicitudes')

@app.route('/solicitudes/nueva', methods=['POST'])
@login_required
def nueva_solicitud():
    db = get_db()
    try:
        cant = max(1, int(request.form.get('cantidad', 1)))
    except ValueError:
        flash('Cantidad inválida', 'error')
        return redirect(url_for('solicitudes'))
    db.execute(
        'INSERT INTO solicitudes (producto_id,cantidad,area,prioridad,motivo,usuario_id,estado,fecha) VALUES (?,?,?,?,?,?,?,?)',
        (request.form['producto_id'], cant, request.form.get('area',''),
         request.form.get('prioridad','Normal'), request.form.get('motivo',''),
         session['user_id'], 'pendiente', datetime.datetime.now())
    )
    db.commit()
    flash('Solicitud enviada correctamente', 'success')
    return redirect(url_for('solicitudes'))

@app.route('/solicitudes/accion/<int:id>/<accion>')
@login_required
@role_required('Administrador', 'Supervisor', 'Producción', 'General')
def accion_solicitud(id, accion):
    if accion not in ('aprobar', 'rechazar'):
        flash('Acción inválida', 'error')
        return redirect(url_for('solicitudes'))
    db = get_db()
    db.execute('UPDATE solicitudes SET estado=? WHERE id=?',
               ('aprobado' if accion == 'aprobar' else 'rechazado', id))
    db.commit()
    flash('Solicitud actualizada', 'success')
    return redirect(url_for('solicitudes'))

# ─────────────────────────────────────────
# Historial
# ─────────────────────────────────────────
@app.route('/historial')
@login_required
@perm_required('historial')
def historial():
    db     = get_db()
    buscar = request.args.get('buscar', '')
    tipo   = request.args.get('tipo', '')
    fi     = request.args.get('fecha_ini', '')
    ff     = request.args.get('fecha_fin', '')
    page   = max(1, int(request.args.get('page', 1)))
    pp     = 30

    base = '''SELECT m.*, p.nombre as producto_nombre, p.codigo, p.unidad, u.nombre as usuario_nombre
              FROM movimientos m
              JOIN productos p ON m.producto_id=p.id
              JOIN usuarios u ON m.usuario_id=u.id
              WHERE 1=1'''
    params = []
    if buscar: base += ' AND (p.nombre LIKE ? OR p.codigo LIKE ?)'; params += [f'%{buscar}%'] * 2
    if tipo:   base += ' AND m.tipo=?';                               params.append(tipo)
    if fi:     base += ' AND DATE(m.fecha)>=?';                       params.append(fi)
    if ff:     base += ' AND DATE(m.fecha)<=?';                       params.append(ff)

    try:
        total = db.execute(base.replace(
            'SELECT m.*, p.nombre as producto_nombre, p.codigo, p.unidad, u.nombre as usuario_nombre', 'SELECT COUNT(*)'
        ), params).fetchone()[0]
    except Exception:
        total = 0

    movs = db.execute(base + f' ORDER BY m.fecha DESC LIMIT {pp} OFFSET {(page-1)*pp}', params).fetchall()
    return render_template('historial.html',
        movimientos=movs, total=total, page=page, per_page=pp,
        total_pages=max(1, (total + pp - 1) // pp),
        buscar=buscar, tipo=tipo, fecha_ini=fi, fecha_fin=ff,
        active_tab='historial')

@app.route('/historial/exportar')
@login_required
def exportar_historial():
    import csv, io
    from flask import Response
    rows = get_db().execute(
        '''SELECT m.fecha, p.codigo, p.nombre, m.tipo, m.cantidad, p.unidad, m.motivo, m.area, u.nombre
           FROM movimientos m
           JOIN productos p ON m.producto_id=p.id
           JOIN usuarios u ON m.usuario_id=u.id
           ORDER BY m.fecha DESC'''
    ).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Fecha','Código','Producto','Tipo','Cantidad','Unidad','Motivo','Área','Usuario'])
    for r in rows:
        w.writerow(list(r))
    out.seek(0)
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=historial_gestix.csv'})

# ─────────────────────────────────────────
# Reportes
# ─────────────────────────────────────────
@app.route('/reportes')
@login_required
@perm_required('reportes')
def reportes():
    db = get_db()
    total_productos  = db.execute('SELECT COUNT(*) FROM productos WHERE activo=1').fetchone()[0]
    stock_bajo_count = db.execute('SELECT COUNT(*) FROM productos WHERE activo=1 AND stock<=stock_minimo').fetchone()[0]
    sol_pendientes   = db.execute("SELECT COUNT(*) FROM solicitudes WHERE estado='pendiente'").fetchone()[0]
    entregas_mes     = db.execute(
        "SELECT COUNT(*) FROM entregas WHERE strftime('%Y-%m',fecha)=strftime('%Y-%m','now')"
    ).fetchone()[0]

    stats = {
        'total_productos': total_productos,
        'stock_bajo':      stock_bajo_count,
        'sol_pendientes':  sol_pendientes,
        'entregas_mes':    entregas_mes,
    }
    por_cat = [
        {'categoria': r['categoria'], 'total_salidas': r['total_salidas'] or 0}
        for r in db.execute(
            """SELECT p.categoria,
                      SUM(CASE WHEN m.tipo='salida' THEN m.cantidad ELSE 0 END) as total_salidas
               FROM productos p
               LEFT JOIN movimientos m ON p.id=m.producto_id
               GROUP BY p.categoria
               ORDER BY total_salidas DESC"""
        ).fetchall()
    ]
    ent_sem = [
        {'dia': r['dia'], 'total': r['total']}
        for r in db.execute(
            """SELECT DATE(fecha) as dia, COUNT(*) as total
               FROM entregas
               WHERE fecha>=DATE('now','-49 days')
               GROUP BY DATE(fecha)
               ORDER BY dia"""
        ).fetchall()
    ]
    stock_bajo = db.execute(
        'SELECT * FROM productos WHERE activo=1 AND stock<=stock_minimo ORDER BY stock'
    ).fetchall()
    return render_template('reportes.html',
        stats=stats, por_categoria=por_cat,
        stock_bajo=stock_bajo, entregas_semana=ent_sem,
        active_tab='reportes')

# ─────────────────────────────────────────
# Seguimiento
# ─────────────────────────────────────────
@app.route('/seguimiento')
@login_required
@perm_required('seguimiento')
def seguimiento():
    db = get_db()
    regs = db.execute(
        '''SELECT r.*, u.nombre as creador,
                  COUNT(t.id) as total_tareas,
                  SUM(CASE WHEN t.estado="completada" THEN 1 ELSE 0 END) as completadas
           FROM registros_operador r
           JOIN usuarios u ON r.creado_por=u.id
           LEFT JOIN tareas_operador t ON t.registro_id=r.id
           GROUP BY r.id ORDER BY r.fecha DESC LIMIT 20'''
    ).fetchall()
    ords = db.execute(
        '''SELECT o.*, u.nombre as creador,
                  COUNT(i.id) as total_tareas,
                  SUM(CASE WHEN i.estado="Hecho" THEN 1 ELSE 0 END) as completadas
           FROM ordenes_tarea o
           JOIN usuarios u ON o.creado_por=u.id
           LEFT JOIN items_orden i ON i.orden_id=o.id
           GROUP BY o.id ORDER BY o.fecha DESC LIMIT 20'''
    ).fetchall()
    # Proyectos activos
    try:
        proyectos = db.execute(
            '''SELECT p.*, u.nombre as creador_nombre
               FROM proyectos p
               JOIN usuarios u ON p.creado_por=u.id
               WHERE p.activo=1
               ORDER BY
                 CASE p.prioridad WHEN 'Urgente' THEN 1 WHEN 'Alta' THEN 2 WHEN 'Normal' THEN 3 ELSE 4 END,
                 p.fecha_compromiso ASC'''
        ).fetchall()
    except Exception:
        proyectos = []
    return render_template('seguimiento.html', registros=regs, ordenes=ords,
                           proyectos=proyectos, active_tab='seguimiento')


# ── Proyectos CRUD ──────────────────────────────────────────────────────────
@app.route('/seguimiento/proyecto/nuevo', methods=['POST'])
@login_required
@perm_required('seguimiento')
def nuevo_proyecto():
    db  = get_db()
    rol = session.get('role', '')
    origen = request.form.get('origen', 'seguimiento')
    if rol not in ('Administrador', 'Supervisor'):
        flash('Solo Administrador o Supervisor pueden crear proyectos', 'error')
        return redirect(url_for('dashboard') if origen == 'dashboard' else url_for('seguimiento'))
    now = datetime.datetime.now().isoformat()
    db.execute(
        '''INSERT INTO proyectos
           (nombre,responsable,area,prioridad,avance,estado,
            fecha_inicio,fecha_compromiso,observaciones,creado_por,creado_en,actualizado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (request.form['nombre'], request.form.get('responsable',''),
         request.form.get('area',''), request.form.get('prioridad','Normal'),
         int(request.form.get('avance', 0)),
         request.form.get('estado','Pendiente'),
         request.form.get('fecha_inicio',''), request.form.get('fecha_compromiso',''),
         request.form.get('observaciones',''), session['user_id'], now, now)
    )
    db.commit()
    flash('Proyecto creado', 'success')
    return redirect(url_for('dashboard') if origen == 'dashboard' else url_for('seguimiento'))


@app.route('/seguimiento/proyecto/<int:id>/editar', methods=['POST'])
@login_required
@perm_required('seguimiento')
def editar_proyecto(id):
    db  = get_db()
    rol = session.get('role', '')
    proj = db.execute('SELECT * FROM proyectos WHERE id=?', (id,)).fetchone()
    if not proj:
        flash('Proyecto no encontrado', 'error')
        return redirect(url_for('seguimiento'))
    # Supervisor solo puede editar su área
    if rol == 'Supervisor' and proj['area'] != session.get('area',''):
        # Pero puede actualizar avance siempre
        pass
    now = datetime.datetime.now().isoformat()
    db.execute(
        '''UPDATE proyectos SET nombre=?,responsable=?,area=?,prioridad=?,
           avance=?,estado=?,fecha_inicio=?,fecha_compromiso=?,
           observaciones=?,actualizado_en=? WHERE id=?''',
        (request.form['nombre'], request.form.get('responsable',''),
         request.form.get('area',''), request.form.get('prioridad','Normal'),
         int(request.form.get('avance', 0)),
         request.form.get('estado','Pendiente'),
         request.form.get('fecha_inicio',''), request.form.get('fecha_compromiso',''),
         request.form.get('observaciones',''), now, id)
    )
    db.commit()
    flash('Proyecto actualizado', 'success')
    return redirect(url_for('seguimiento'))


@app.route('/seguimiento/proyecto/<int:id>/avance', methods=['POST'])
@login_required
@perm_required('seguimiento')
def actualizar_avance(id):
    """Actualización rápida de avance vía JSON (desde dashboard o tabla)."""
    data = request.get_json(silent=True) or {}
    db   = get_db()
    now  = datetime.datetime.now().isoformat()
    avance = max(0, min(100, int(data.get('avance', 0))))
    estado = data.get('estado', None)
    if estado:
        db.execute('UPDATE proyectos SET avance=?,estado=?,actualizado_en=? WHERE id=?',
                   (avance, estado, now, id))
    else:
        db.execute('UPDATE proyectos SET avance=?,actualizado_en=? WHERE id=?',
                   (avance, now, id))
    db.commit()
    return jsonify({'success': True, 'avance': avance})


@app.route('/seguimiento/proyecto/<int:id>/eliminar', methods=['POST'])
@login_required
@role_required('Administrador')
def eliminar_proyecto(id):
    db = get_db()
    db.execute('UPDATE proyectos SET activo=0 WHERE id=?', (id,))
    db.commit()
    flash('Proyecto archivado', 'info')
    origen = request.form.get('origen', 'seguimiento')
    return redirect(url_for('dashboard') if origen == 'dashboard' else url_for('seguimiento'))


@app.route('/api/alerta/descartar/<int:proyecto_id>', methods=['POST'])
@login_required
def descartar_alerta(proyecto_id):
    """Descarta una alerta para el usuario actual. No afecta el proyecto."""
    db  = get_db()
    now = datetime.datetime.now().isoformat()
    uid = session.get('user_id')
    try:
        db.execute(
            'INSERT OR REPLACE INTO alertas_descartadas (usuario_id, proyecto_id, descartada_en) VALUES (?,?,?)',
            (uid, proyecto_id, now)
        )
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/proyectos')
@login_required
def api_proyectos():
    """API JSON para el dashboard en vivo."""
    db = get_db()
    try:
        rows = db.execute(
            '''SELECT p.*, u.nombre as creador_nombre
               FROM proyectos p JOIN usuarios u ON p.creado_por=u.id
               WHERE p.activo=1
               ORDER BY CASE p.prioridad WHEN 'Urgente' THEN 1 WHEN 'Alta' THEN 2 ELSE 3 END,
                        p.fecha_compromiso ASC'''
        ).fetchall()
        return jsonify({'proyectos': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'proyectos': [], 'error': str(e)})

def _save_tareas(db, rid, form):
    ops = form.getlist('operador[]')
    fl = {k: form.getlist(k) for k in ['puesto_area[]','tarea[]','prioridad[]','hora_inicio[]','hora_fin[]','nueva_tarea[]','nueva_tarea2[]','observaciones[]','estado[]']}
    for i, op in enumerate(ops):
        if not op.strip():
            continue
        v = lambda k: fl[k][i] if i < len(fl[k]) else ''
        db.execute(
            'INSERT INTO tareas_operador (registro_id,operador,puesto_area,tarea,prioridad,hora_inicio,hora_fin,nueva_tarea,nueva_tarea2,observaciones,estado) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (rid, op, v('puesto_area[]'), v('tarea[]'), v('prioridad[]') or 'Alta',
             v('hora_inicio[]'), v('hora_fin[]'), v('nueva_tarea[]'), v('nueva_tarea2[]'),
             v('observaciones[]'), v('estado[]') or 'pendiente')
        )

@app.route('/seguimiento/registro/nuevo', methods=['GET', 'POST'])
@login_required
@perm_required('seguimiento')
def nuevo_registro():
    if request.method == 'POST':
        db = get_db()
        rid = db.execute(
            'INSERT INTO registros_operador (fecha,turno,supervisor,creado_por,creado_en) VALUES (?,?,?,?,?)',
            (request.form['fecha'], request.form['turno'], request.form['supervisor'],
             session['user_id'], datetime.datetime.now())
        ).lastrowid
        _save_tareas(db, rid, request.form)
        db.commit()
        flash('Registro guardado correctamente', 'success')
        return redirect(url_for('ver_registro', id=rid))
    return render_template('form_registro.html', active_tab='seguimiento')

@app.route('/seguimiento/registro/<int:id>')
@login_required
@perm_required('seguimiento')
def ver_registro(id):
    db = get_db()
    reg = db.execute(
        'SELECT r.*, u.nombre as creador FROM registros_operador r JOIN usuarios u ON r.creado_por=u.id WHERE r.id=?', (id,)
    ).fetchone()
    if not reg:
        flash('Registro no encontrado', 'error')
        return redirect(url_for('seguimiento'))
    tareas = db.execute('SELECT * FROM tareas_operador WHERE registro_id=? ORDER BY id', (id,)).fetchall()
    return render_template('ver_registro.html', registro=reg, tareas=tareas, active_tab='seguimiento')

@app.route('/seguimiento/registro/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@perm_required('seguimiento')
def editar_registro(id):
    db = get_db()
    if request.method == 'POST':
        db.execute(
            'UPDATE registros_operador SET fecha=?,turno=?,supervisor=? WHERE id=?',
            (request.form['fecha'], request.form['turno'], request.form['supervisor'], id)
        )
        db.execute('DELETE FROM tareas_operador WHERE registro_id=?', (id,))
        _save_tareas(db, id, request.form)
        db.commit()
        flash('Registro actualizado', 'success')
        return redirect(url_for('ver_registro', id=id))
    return render_template('form_registro.html',
        registro=db.execute('SELECT * FROM registros_operador WHERE id=?', (id,)).fetchone(),
        tareas=db.execute('SELECT * FROM tareas_operador WHERE registro_id=? ORDER BY id', (id,)).fetchall(),
        editando=True, active_tab='seguimiento')

def _save_items(db, oid, form):
    ts  = form.getlist('tarea[]')
    ps  = form.getlist('prioridad[]')
    es  = form.getlist('estado[]')
    obs = form.getlist('observaciones[]')
    for i, t in enumerate(ts):
        if not t.strip():
            continue
        db.execute(
            'INSERT INTO items_orden (orden_id,numero,tarea,prioridad,estado,observaciones) VALUES (?,?,?,?,?,?)',
            (oid, i + 1, t,
             ps[i]  if i < len(ps)  else 'Media',
             es[i]  if i < len(es)  else 'Pendiente',
             obs[i] if i < len(obs) else '')
        )

@app.route('/seguimiento/orden/nueva', methods=['GET', 'POST'])
@login_required
@perm_required('seguimiento')
def nueva_orden():
    if request.method == 'POST':
        db = get_db()
        oid = db.execute(
            'INSERT INTO ordenes_tarea (fecha,nombre,area,reporta_a,turno,creado_por,creado_en) VALUES (?,?,?,?,?,?,?)',
            (request.form['fecha'], request.form['nombre'], request.form['area'],
             request.form['reporta_a'], request.form['turno'],
             session['user_id'], datetime.datetime.now())
        ).lastrowid
        _save_items(db, oid, request.form)
        db.commit()
        flash('Orden guardada correctamente', 'success')
        return redirect(url_for('ver_orden', id=oid))
    return render_template('form_orden.html', active_tab='seguimiento')

@app.route('/seguimiento/orden/<int:id>')
@login_required
@perm_required('seguimiento')
def ver_orden(id):
    db = get_db()
    orden = db.execute(
        'SELECT o.*, u.nombre as creador FROM ordenes_tarea o JOIN usuarios u ON o.creado_por=u.id WHERE o.id=?', (id,)
    ).fetchone()
    if not orden:
        flash('Orden no encontrada', 'error')
        return redirect(url_for('seguimiento'))
    items = db.execute('SELECT * FROM items_orden WHERE orden_id=? ORDER BY numero', (id,)).fetchall()
    return render_template('ver_orden.html', orden=orden, items=items, active_tab='seguimiento')

@app.route('/seguimiento/orden/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@perm_required('seguimiento')
def editar_orden(id):
    db = get_db()
    if request.method == 'POST':
        db.execute(
            'UPDATE ordenes_tarea SET fecha=?,nombre=?,area=?,reporta_a=?,turno=? WHERE id=?',
            (request.form['fecha'], request.form['nombre'], request.form['area'],
             request.form['reporta_a'], request.form['turno'], id)
        )
        db.execute('DELETE FROM items_orden WHERE orden_id=?', (id,))
        _save_items(db, id, request.form)
        db.commit()
        flash('Orden actualizada', 'success')
        return redirect(url_for('ver_orden', id=id))
    return render_template('form_orden.html',
        orden=db.execute('SELECT * FROM ordenes_tarea WHERE id=?', (id,)).fetchone(),
        items=db.execute('SELECT * FROM items_orden WHERE orden_id=? ORDER BY numero', (id,)).fetchall(),
        editando=True, active_tab='seguimiento')

# ─────────────────────────────────────────
# Trabajos
# ─────────────────────────────────────────
@app.route('/trabajos')
@login_required
@perm_required('trabajos_ver')
def trabajos():
    db     = get_db()
    buscar = request.args.get('buscar', '')
    pri    = request.args.get('prioridad', '')
    q = 'SELECT t.*, u.nombre as subido_por_nombre FROM trabajos t JOIN usuarios u ON t.subido_por=u.id WHERE t.activo=1'
    p = []
    if buscar: q += ' AND (t.numero_dibujo LIKE ? OR t.nombre_pieza LIKE ? OR t.area_responsable LIKE ?)'; p += [f'%{buscar}%'] * 3
    if pri:    q += ' AND t.prioridad=?'; p.append(pri)
    lista = db.execute(q + ' ORDER BY t.subido_en DESC', p).fetchall()
    return render_template('trabajos.html',
        trabajos=lista,
        buscar=buscar, prioridad=pri, active_tab='trabajos')

@app.route('/trabajos/subir', methods=['POST'])
@login_required
@perm_required('trabajos_subir')
def subir_trabajo():
    from werkzeug.utils import secure_filename
    if 'archivo' not in request.files:
        flash('No se seleccionó archivo', 'error')
        return redirect(url_for('trabajos'))
    f = request.files['archivo']
    if not f or not f.filename:
        flash('Archivo vacío', 'error')
        return redirect(url_for('trabajos'))
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext != 'pdf':
        flash('Solo se permiten archivos PDF', 'error')
        return redirect(url_for('trabajos'))
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    fn = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{secure_filename(f.filename)}"
    f.save(os.path.join(UPLOAD_FOLDER, fn))
    db = get_db()
    db.execute(
        'INSERT INTO trabajos (numero_dibujo,nombre_pieza,area_responsable,prioridad,observaciones,archivo,subido_por,subido_en) VALUES (?,?,?,?,?,?,?,?)',
        (request.form['numero_dibujo'], request.form['nombre_pieza'],
         request.form.get('area_responsable',''), request.form.get('prioridad','Normal'),
         request.form.get('observaciones',''), fn, session['user_id'], datetime.datetime.now())
    )
    db.commit()
    flash('Dibujo subido correctamente', 'success')
    return redirect(url_for('trabajos'))

@app.route('/trabajos/ver/<int:id>')
@login_required
@perm_required('trabajos_ver')
def ver_trabajo(id):
    t = get_db().execute(
        'SELECT t.*, u.nombre as subido_por_nombre FROM trabajos t JOIN usuarios u ON t.subido_por=u.id WHERE t.id=? AND t.activo=1', (id,)
    ).fetchone()
    if not t:
        flash('Trabajo no encontrado', 'error')
        return redirect(url_for('trabajos'))
    return render_template('ver_trabajo.html', trabajo=t, active_tab='trabajos')

@app.route('/trabajos/pdf/<int:id>')
@login_required
@perm_required('trabajos_ver')
def ver_pdf(id):
    t = get_db().execute('SELECT archivo FROM trabajos WHERE id=? AND activo=1', (id,)).fetchone()
    if not t:
        flash('Archivo no encontrado', 'error')
        return redirect(url_for('trabajos'))
    return send_from_directory(UPLOAD_FOLDER, t['archivo'])

@app.route('/trabajos/eliminar/<int:id>')
@login_required
@perm_required('trabajos_subir')
def eliminar_trabajo(id):
    db = get_db()
    db.execute('UPDATE trabajos SET activo=0 WHERE id=?', (id,))
    db.commit()
    flash('Trabajo eliminado', 'info')
    return redirect(url_for('trabajos'))

# ─────────────────────────────────────────
# Usuarios
# ─────────────────────────────────────────
@app.route('/usuarios')
@login_required
@role_required('Administrador')
def usuarios():
    db     = get_db()
    buscar = request.args.get('buscar', '')
    q = 'SELECT * FROM usuarios WHERE 1=1'
    p = []
    if buscar: q += ' AND (nombre LIKE ? OR username LIKE ?)'; p += [f'%{buscar}%'] * 2
    users  = db.execute(q + ' ORDER BY nombre', p).fetchall()
    pm     = {u['id']: db.execute('SELECT * FROM usuario_permisos WHERE usuario_id=?', (u['id'],)).fetchone() for u in users}
    return render_template('usuarios.html', usuarios=users, buscar=buscar, permisos_map=pm, active_tab='usuarios')

def _save_permisos(db, uid, form):
    keys = ['puede_inventario','puede_entregas','puede_solicitudes','puede_historial',
            'puede_reportes','puede_seguimiento','puede_trabajos_ver','puede_trabajos_subir','puede_usuarios']
    vals = [1 if form.get(k) else 0 for k in keys]
    if db.execute('SELECT id FROM usuario_permisos WHERE usuario_id=?', (uid,)).fetchone():
        db.execute(f'UPDATE usuario_permisos SET {",".join(k+"=?" for k in keys)} WHERE usuario_id=?', (*vals, uid))
    else:
        db.execute(
            f'INSERT INTO usuario_permisos (usuario_id,{",".join(keys)}) VALUES (?,{",".join("?"*len(keys))})',
            (uid, *vals)
        )

@app.route('/usuarios/agregar', methods=['POST'])
@login_required
@role_required('Administrador')
def agregar_usuario():
    db = get_db()
    username = request.form.get('username', '').strip()
    if not username:
        flash('Nombre de usuario requerido', 'error')
        return redirect(url_for('usuarios'))
    if db.execute('SELECT id FROM usuarios WHERE username=?', (username,)).fetchone():
        flash('Ese nombre de usuario ya existe', 'error')
        return redirect(url_for('usuarios'))
    password = request.form.get('password', '')
    if not password or len(password) < 4:
        flash('La contraseña debe tener al menos 4 caracteres', 'error')
        return redirect(url_for('usuarios'))
    uid = db.execute(
        'INSERT INTO usuarios (nombre,username,password,rol,area,correo,turno,activo,fecha_alta) VALUES (?,?,?,?,?,?,?,1,?)',
        (request.form['nombre'], username, hash_pw(password),
         request.form['rol'], request.form.get('area',''),
         request.form.get('correo',''), request.form.get('turno','Matutino'),
         datetime.date.today())
    ).lastrowid
    _save_permisos(db, uid, request.form)
    db.commit()
    flash('Usuario creado correctamente', 'success')
    return redirect(url_for('usuarios'))

@app.route('/usuarios/editar/<int:id>', methods=['POST'])
@login_required
@role_required('Administrador')
def editar_usuario(id):
    db = get_db()
    db.execute(
        'UPDATE usuarios SET nombre=?,rol=?,area=?,correo=?,turno=?,activo=? WHERE id=?',
        (request.form['nombre'], request.form['rol'], request.form.get('area',''),
         request.form.get('correo',''), request.form.get('turno','Matutino'),
         1 if request.form.get('activo') == '1' else 0, id)
    )
    password = request.form.get('password', '').strip()
    if password:
        if len(password) < 4:
            flash('La contraseña debe tener al menos 4 caracteres', 'error')
            return redirect(url_for('usuarios'))
        db.execute('UPDATE usuarios SET password=? WHERE id=?', (hash_pw(password), id))
    _save_permisos(db, id, request.form)
    db.commit()
    flash('Usuario actualizado correctamente', 'success')
    return redirect(url_for('usuarios'))

@app.route('/usuarios/eliminar/<int:id>')
@login_required
@role_required('Administrador')
def eliminar_usuario(id):
    if id == session['user_id']:
        flash('No puedes desactivarte a ti mismo', 'error')
        return redirect(url_for('usuarios'))
    db = get_db()
    db.execute('UPDATE usuarios SET activo=0 WHERE id=?', (id,))
    db.commit()
    flash('Usuario desactivado', 'info')
    return redirect(url_for('usuarios'))

@app.route('/usuarios/permisos_json/<int:id>')
@login_required
@role_required('Administrador')
def permisos_json(id):
    p    = get_db().execute('SELECT * FROM usuario_permisos WHERE usuario_id=?', (id,)).fetchone()
    keys = ['puede_inventario','puede_entregas','puede_solicitudes','puede_historial',
            'puede_reportes','puede_seguimiento','puede_trabajos_ver','puede_trabajos_subir','puede_usuarios']
    if not p:
        return jsonify({k: True for k in keys})
    return jsonify({k: bool(p[k]) for k in keys})

# ─────────────────────────────────────────
# Init DB
# ─────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS empresas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL,
            slug        TEXT UNIQUE NOT NULL,
            activa      INTEGER DEFAULT 1,
            creada_en   TEXT DEFAULT CURRENT_DATE
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'General',
            area TEXT DEFAULT '',
            correo TEXT DEFAULT '',
            turno TEXT DEFAULT 'Matutino',
            activo INTEGER DEFAULT 1,
            fecha_alta TEXT DEFAULT CURRENT_DATE,
            empresa_id INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
            stock INTEGER DEFAULT 0,
            unidad TEXT DEFAULT 'pza',
            stock_minimo INTEGER DEFAULT 5,
            descripcion TEXT DEFAULT '',
            activo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            motivo TEXT DEFAULT '',
            area TEXT DEFAULT '',
            usuario_id INTEGER NOT NULL,
            fecha TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entregas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folio TEXT UNIQUE NOT NULL,
            motivo TEXT DEFAULT '',
            area TEXT DEFAULT '',
            comentarios TEXT DEFAULT '',
            usuario_id INTEGER NOT NULL,
            fecha TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entrega_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entrega_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS solicitudes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            cantidad INTEGER NOT NULL,
            area TEXT DEFAULT '',
            prioridad TEXT DEFAULT 'Normal',
            motivo TEXT DEFAULT '',
            usuario_id INTEGER NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registros_operador (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            turno TEXT DEFAULT 'Matutino',
            supervisor TEXT DEFAULT '',
            creado_por INTEGER NOT NULL,
            creado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tareas_operador (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registro_id INTEGER NOT NULL,
            operador TEXT NOT NULL,
            puesto_area TEXT DEFAULT '',
            tarea TEXT NOT NULL,
            prioridad TEXT DEFAULT 'Alta',
            hora_inicio TEXT DEFAULT '',
            hora_fin TEXT DEFAULT '',
            nueva_tarea TEXT DEFAULT '',
            nueva_tarea2 TEXT DEFAULT '',
            observaciones TEXT DEFAULT '',
            estado TEXT DEFAULT 'pendiente'
        );
        CREATE TABLE IF NOT EXISTS ordenes_tarea (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            nombre TEXT NOT NULL,
            area TEXT DEFAULT '',
            reporta_a TEXT DEFAULT '',
            turno TEXT DEFAULT 'Matutino',
            creado_por INTEGER NOT NULL,
            creado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items_orden (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            numero INTEGER NOT NULL,
            tarea TEXT NOT NULL,
            prioridad TEXT DEFAULT 'Media',
            estado TEXT DEFAULT 'Pendiente',
            observaciones TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS proyectos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre          TEXT NOT NULL,
            responsable     TEXT DEFAULT '',
            area            TEXT DEFAULT '',
            prioridad       TEXT DEFAULT 'Normal',
            avance          INTEGER DEFAULT 0,
            estado          TEXT DEFAULT 'Pendiente',
            fecha_inicio    TEXT DEFAULT '',
            fecha_compromiso TEXT DEFAULT '',
            observaciones   TEXT DEFAULT '',
            creado_por      INTEGER NOT NULL,
            creado_en       TEXT NOT NULL,
            actualizado_en  TEXT NOT NULL,
            activo          INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS trabajos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_dibujo TEXT NOT NULL,
            nombre_pieza TEXT NOT NULL,
            area_responsable TEXT DEFAULT '',
            prioridad TEXT DEFAULT 'Normal',
            observaciones TEXT DEFAULT '',
            archivo TEXT NOT NULL,
            subido_por INTEGER NOT NULL,
            subido_en TEXT NOT NULL,
            activo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS usuario_permisos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL UNIQUE,
            puede_inventario INTEGER DEFAULT 1,
            puede_entregas INTEGER DEFAULT 1,
            puede_solicitudes INTEGER DEFAULT 1,
            puede_historial INTEGER DEFAULT 1,
            puede_reportes INTEGER DEFAULT 1,
            puede_seguimiento INTEGER DEFAULT 1,
            puede_trabajos_ver INTEGER DEFAULT 1,
            puede_trabajos_subir INTEGER DEFAULT 0,
            puede_usuarios INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bom_items (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            trabajo_id            INTEGER NOT NULL,
            material              TEXT NOT NULL,
            especificacion        TEXT DEFAULT '',
            longitud              REAL DEFAULT 0,
            unidad                TEXT DEFAULT 'in',
            cantidad              INTEGER DEFAULT 1,
            desperdicio           REAL DEFAULT 0,
            total_con_desperdicio REAL DEFAULT 0,
            creado_en             TEXT NOT NULL,
            creado_por            INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alertas_descartadas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id  INTEGER NOT NULL,
            proyecto_id INTEGER NOT NULL,
            descartada_en TEXT NOT NULL,
            UNIQUE(usuario_id, proyecto_id)
        );
    ''')

    # Empresa por defecto (para instalaciones existentes y nuevas)
    if not db.execute('SELECT id FROM empresas WHERE slug="default"').fetchone():
        db.execute(
            'INSERT INTO empresas (nombre, slug) VALUES (?,?)',
            ('ACE PACK', 'default')
        )
        db.commit()

    if not db.execute('SELECT id FROM usuarios WHERE username="admin"').fetchone():
        uid = db.execute(
            'INSERT INTO usuarios (nombre,username,password,rol,area,turno,empresa_id) VALUES (?,?,?,?,?,?,?)',
            ('Administrador', 'admin', hash_pw('admin123'), 'Administrador', 'Administración', 'Matutino', 1)
        ).lastrowid
        for prod in [
            ('72345', 'Llave Inglesa 12"',    'Herramientas',  15,  'pza',  5, ''),
            ('88432', 'Cinta Aislante Negra', 'Material',      90,  'pza', 20, ''),
            ('65789', 'Rodamiento 6205',       'Refacciones',    3,  'pza', 10, ''),
            ('74021', 'Casco de Seguridad',    'Seguridad',     25,  'pza', 10, ''),
            ('89213', 'Tornillo Hexagonal M8', 'Refacciones',  280,  'pza', 50, ''),
            ('99105', 'Guantes de Nitrilo',    'Seguridad',    280, 'caja', 30, ''),
            ('55021', 'Aceite Lubricante 1L',  'Consumibles',    0,  'pza',  5, ''),
        ]:
            db.execute('INSERT INTO productos (codigo,nombre,categoria,stock,unidad,stock_minimo,descripcion) VALUES (?,?,?,?,?,?,?)', prod)

    for u in db.execute('SELECT id, rol FROM usuarios').fetchall():
        if not db.execute('SELECT id FROM usuario_permisos WHERE usuario_id=?', (u['id'],)).fetchone():
            subir   = 1 if u['rol'] in ('Administrador', 'Supervisor', 'Diseño') else 0
            ver_usr = 1 if u['rol'] == 'Administrador' else 0
            db.execute(
                'INSERT INTO usuario_permisos (usuario_id,puede_inventario,puede_entregas,puede_solicitudes,puede_historial,puede_reportes,puede_seguimiento,puede_trabajos_ver,puede_trabajos_subir,puede_usuarios) VALUES (?,1,1,1,1,1,1,1,?,?)',
                (u['id'], subir, ver_usr)
            )
    db.commit()
    db.close()
    print('✔ Gestix DB lista')


# ─────────────────────────────────────────
# Dashboard principal
# ─────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    stats = {
        'total_productos':  db.execute('SELECT COUNT(*) FROM productos WHERE activo=1').fetchone()[0],
        'stock_bajo':       db.execute('SELECT COUNT(*) FROM productos WHERE activo=1 AND stock<=stock_minimo').fetchone()[0],
        'sol_pendientes':   db.execute("SELECT COUNT(*) FROM solicitudes WHERE estado='pendiente'").fetchone()[0],
        'entregas_mes':     db.execute("SELECT COUNT(*) FROM entregas WHERE strftime('%Y-%m',fecha)=strftime('%Y-%m','now')").fetchone()[0],
    }
    # KPIs de proyectos desde Seguimiento
    try:
        rows = db.execute('''
            SELECT
              COUNT(*) as total,
              SUM(CASE WHEN estado IN ('Pendiente','En proceso') AND activo=1 THEN 1 ELSE 0 END) as activos,
              SUM(CASE WHEN estado='Terminado' AND activo=1 THEN 1 ELSE 0 END) as terminados,
              SUM(CASE WHEN estado='Atrasado' AND activo=1 THEN 1 ELSE 0 END) as atrasados,
              AVG(CASE WHEN estado IN ('Pendiente','En proceso') AND activo=1 THEN avance ELSE NULL END) as avance_prom
            FROM proyectos WHERE activo=1
        ''').fetchone()
        proyectos_kpi = {
            'activos':     rows['activos'] or 0,
            'terminados':  rows['terminados'] or 0,
            'atrasados':   rows['atrasados'] or 0,
            'avance_prom': round(rows['avance_prom'] or 0, 1),
        }
        # Alertas: urgentes + atrasados + próximos vencimientos (excluyendo descartadas)
        hoy = datetime.date.today().isoformat()
        en3dias = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        uid = session.get('user_id')
        alertas = db.execute('''
            SELECT id, nombre, prioridad, estado, avance, fecha_compromiso, responsable
            FROM proyectos
            WHERE activo=1
              AND id NOT IN (
                  SELECT proyecto_id FROM alertas_descartadas WHERE usuario_id=?
              )
              AND (
                estado='Atrasado'
                OR prioridad='Urgente'
                OR (fecha_compromiso != '' AND fecha_compromiso <= ?)
              )
            ORDER BY CASE prioridad WHEN 'Urgente' THEN 1 ELSE 2 END
            LIMIT 10
        ''', (uid, en3dias)).fetchall()
        proyectos_lista = db.execute('''
            SELECT p.*, u.nombre as creador_nombre
            FROM proyectos p JOIN usuarios u ON p.creado_por=u.id
            WHERE p.activo=1 AND p.estado != 'Terminado'
            ORDER BY CASE p.prioridad WHEN 'Urgente' THEN 1 WHEN 'Alta' THEN 2 ELSE 3 END,
                     p.fecha_compromiso ASC
            LIMIT 10
        ''').fetchall()
    except Exception:
        proyectos_kpi  = {'activos':0,'terminados':0,'atrasados':0,'avance_prom':0}
        alertas        = []
        proyectos_lista = []
    return render_template('dashboard.html', stats=stats,
                           proyectos_kpi=proyectos_kpi,
                           alertas=alertas,
                           proyectos_lista=proyectos_lista,
                           active_tab='dashboard')



# Inicializar DB siempre al arrancar (no solo en __main__)
try:
    init_db()
except Exception as _init_err:
    print(f'⚠ init_db error: {_init_err}')

@app.errorhandler(500)
def error_500(e):
    import traceback
    tb = traceback.format_exc()
    print('=== ERROR 500 ===')
    print(tb)
    return f"""<!DOCTYPE html><html><head><title>Error – Gestix</title>
    <style>body{{font-family:monospace;padding:30px;background:#1C1B29;color:#FC8181;}}
    h2{{color:#FC8181;}} pre{{background:#0F0E17;padding:16px;border-radius:8px;color:#E2E8F0;font-size:12px;overflow:auto;}}</style></head>
    <body><h2>⚠ Error interno del servidor</h2>
    <p>Por favor comparte este mensaje para diagnóstico:</p>
    <pre>{tb}</pre></body></html>""", 500

if __name__ == '__main__':
    print('╔══════════════════════════════╗')
    print('║   GESTIX  –  ERP Sistema     ║')
    print('║   http://localhost:5000      ║')
    print('╚══════════════════════════════╝')
    app.run(host='0.0.0.0', port=5000, debug=False)
