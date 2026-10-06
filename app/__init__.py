"""Define los modelos, las rutas y la lógica de gestión de FerreSoft."""

import os
import re
import secrets
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from uuid import uuid4

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import case, func, inspect, text
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import check_password_hash, generate_password_hash

from .barcodes import barcode_image
from .schema import make_subcategories_independent


db = SQLAlchemy()


def alphabetical(query):
    """Orden español por nombre: ignora tildes y mayúsculas, conserva la ñ."""
    def key(item):
        name = unicodedata.normalize("NFC", item.name.strip().casefold()).replace("ñ", "n~")
        normalized = unicodedata.normalize("NFD", name)
        return ("".join(c for c in normalized if not unicodedata.combining(c)), item.id)
    return sorted(query.all(), key=key)


def local_now():
    """Hora local sin tzinfo para conservar el formato de la base de datos."""
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


def money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("Importe invalido")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # owner | employee
    active = db.Column(db.Boolean, default=True, nullable=False)
    first_name = db.Column(db.String(100), default="", nullable=False)
    last_name = db.Column(db.String(100), default="", nullable=False)
    dni = db.Column(db.String(30), unique=True, nullable=True)
    phone = db.Column(db.String(60), default="", nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=True)
    address = db.Column(db.String(250), default="", nullable=False)
    created_at = db.Column(db.DateTime, default=local_now, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_logout_at = db.Column(db.DateTime, nullable=True)

    @property
    def full_name(self):
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip() or self.username

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class Subcategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    company_name = db.Column(db.String(160), default="")
    phone = db.Column(db.String(60), default="")
    email = db.Column(db.String(160), default="")
    notes = db.Column(db.String(500), default="")
    active = db.Column(db.Boolean, default=True, nullable=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(500), default="")
    purchase_price = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    sale_price = db.Column(db.Numeric(14, 2), nullable=False)
    stock = db.Column(db.Numeric(14, 3), default=0, nullable=False)
    minimum_stock = db.Column(db.Numeric(14, 3), default=0, nullable=False)  # Cajas o unidades según la presentación.
    active = db.Column(db.Boolean, default=True, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    subcategory_id = db.Column(db.Integer, db.ForeignKey("subcategory.id"), nullable=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("supplier.id"), nullable=True)
    units_per_box = db.Column(db.Integer, default=1, nullable=False)
    closed_boxes = db.Column(db.Integer, default=0, nullable=False)
    loose_units = db.Column(db.Integer, default=0, nullable=False)
    box_price = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    has_box_presentation = db.Column(db.Boolean, default=False, nullable=False)
    image_filename = db.Column(db.String(100), nullable=True)
    category = db.relationship("Category")
    subcategory = db.relationship("Subcategory")
    supplier = db.relationship("Supplier", backref="products")

    @property
    def available_units(self):
        return self.closed_boxes * self.units_per_box + self.loose_units

    @hybrid_property
    def is_stock_critical(self):
        quantity = self.closed_boxes if self.has_box_presentation else self.available_units
        return self.active and quantity <= self.minimum_stock

    @is_stock_critical.expression
    def is_stock_critical(cls):
        quantity = case(
            (cls.has_box_presentation.is_(True), cls.closed_boxes),
            else_=cls.closed_boxes * cls.units_per_box + cls.loose_units,
        )
        return db.and_(cls.active.is_(True), quantity <= cls.minimum_stock)

    def sync_stock(self):
        self.stock = Decimal(self.available_units)


def minimum_stock_value(form):
    try:
        value = Decimal(form.get("minimum_stock") or 0)
        if not value.is_finite() or value < 0 or value != value.to_integral_value():
            raise ValueError
    except (InvalidOperation, ValueError):
        unit = "cajas" if "has_box_presentation" in form else "unidades"
        raise ValueError(f"El stock mínimo debe ser una cantidad entera de {unit}, mayor o igual a cero.")
    return value


def normalized_product_name(name):
    return unicodedata.normalize("NFC", " ".join(name.split())).casefold()


def generate_unique_product_code():
    """Genera un código numérico de 12 dígitos que no exista en el catálogo."""
    for _ in range(100):
        code = f"{secrets.randbelow(10 ** 12):012d}"
        if Product.query.filter_by(code=code).first() is None:
            return code
    raise RuntimeError("No se pudo generar un código único. Intentá nuevamente.")


def product_name_options():
    """Un nombre por sugerencia, aunque haya varias combinaciones de producto."""
    names = {}
    for product in alphabetical(Product.query):
        names.setdefault(normalized_product_name(product.name), product.name.strip())
    return list(names.values())


def validate_product_identity(form, product_id=None):
    """Valida la combinación completa; cada selección vacía equivale a None."""
    errors = {}
    code = form.get("code", "").strip()
    name = " ".join(form.get("name", "").split())
    codes = Product.query.filter_by(code=code)
    if product_id is not None:
        codes = codes.filter(Product.id != product_id)
    if not code:
        errors["code"] = "Ingresá un código."
    elif codes.first():
        errors["code"] = "Ya existe un producto con este código."
    if not name or len(name) > 160:
        errors["name"] = "El nombre debe tener entre 1 y 160 caracteres."

    selections = {}
    for field, model, label in (("category_id", Category, "categoría"), ("subcategory_id", Subcategory, "subcategoría")):
        value = form.get(field, "").strip()
        selections[field] = None
        if value:
            try:
                entry_id = int(value)
                if db.session.get(model, entry_id) is None:
                    raise ValueError
                selections[field] = entry_id
            except ValueError:
                errors[field] = f"Seleccioná una {label} válida."

    if not any(field in errors for field in ("name", "category_id", "subcategory_id")):
        candidates = Product.query.filter_by(**selections)
        if product_id is not None:
            candidates = candidates.filter(Product.id != product_id)
        normalized = normalized_product_name(name)
        for candidate in candidates:
            if normalized_product_name(candidate.name) == normalized:
                errors["name"] = "Ya existe un producto con este nombre y la misma combinación de categoría y subcategoría."
                if not candidate.active:
                    errors["name"] += " El producto existente está inactivo."
                break
    return {"code": code, "name": name, **selections}, errors


class InventoryMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    movement_type = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.String(300), nullable=False)
    reference = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=local_now, nullable=False)
    product = db.relationship("Product")
    user = db.relationship("User")


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    first_name = db.Column(db.String(100), default="", nullable=False)
    last_name = db.Column(db.String(100), default="", nullable=False)
    dni = db.Column(db.String(30), unique=True, nullable=True)
    phone = db.Column(db.String(60), default="")
    address = db.Column(db.String(250), default="")
    debt_balance = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    condition = db.Column(db.String(30), default="Al dia", nullable=False)
    discount_percent = db.Column(db.Numeric(5, 2), default=0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    total = db.Column(db.Numeric(14, 2), nullable=False)
    cash_received = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    change_due = db.Column(db.Numeric(14, 2), default=0, nullable=False)
    status = db.Column(db.String(20), default="CONFIRMADA", nullable=False)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancellation_reason = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=local_now, nullable=False)
    customer = db.relationship("Customer")
    user = db.relationship("User")
    items = db.relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    payments = db.relationship("Payment", back_populates="sale", cascade="all, delete-orphan")


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), nullable=False)
    subtotal = db.Column(db.Numeric(14, 2), nullable=False)
    presentation = db.Column(db.String(20), nullable=True)
    sale_quantity = db.Column(db.Numeric(14, 3), nullable=True)
    discount_percent = db.Column(db.Numeric(5, 2), default=0, nullable=False)
    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product")


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    sale = db.relationship("Sale", back_populates="payments")


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    entity = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.String(60), nullable=False)
    details = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=local_now, nullable=False)
    user = db.relationship("User")


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user() or current_user().role != "owner":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def audit(user, action, entity, entity_id, details):
    db.session.add(AuditEvent(user_id=user.id, action=action, entity=entity, entity_id=str(entity_id), details=details))


ACTIVITY_LABELS = {
    "INICIAR_SESION": "Inicio de sesión",
    "CERRAR_SESION": "Cierre de sesión",
    "CREAR_CUENTA": "Alta de cuenta",
    "ELIMINAR_CUENTA": "Baja de cuenta",
    "CONFIRMAR_VENTA": "Venta realizada",
    "ANULAR_VENTA": "Devolución / venta anulada",
    "REPOSICION": "Reposición de stock",
    "REGISTRAR_MERMA": "Merma / producto roto",
    "AJUSTAR_STOCK": "Ajuste de stock",
    "CREAR_PRODUCTO": "Alta de producto",
    "EDITAR_PRODUCTO": "Edición de producto",
    "CAMBIAR_PRECIO": "Cambio de precio",
    "ELIMINAR_PRODUCTO": "Baja de producto",
    "CREAR_CLIENTE": "Alta de cliente",
    "EDITAR_CLIENTE": "Edición de cliente",
    "ELIMINAR_CLIENTE": "Baja de cliente",
    "CREAR_PROVEEDOR": "Alta de proveedor",
    "EDITAR_PROVEEDOR": "Edición de proveedor",
    "QUITAR_PRODUCTO_PROVEEDOR": "Producto quitado de proveedor",
    "CREAR_CATEGORIA": "Alta de categoría",
    "EDITAR_CATEGORIA": "Edición de categoría",
    "ELIMINAR_CATEGORIA": "Baja de categoría",
    "CREAR_SUBCATEGORIA": "Alta de subcategoría",
    "EDITAR_SUBCATEGORIA": "Edición de subcategoría",
    "ELIMINAR_SUBCATEGORIA": "Baja de subcategoría",
}


def save_product_image(upload):
    """Valida y guarda una imagen con un nombre aleatorio seguro."""
    if not upload or not upload.filename:
        return None
    data = upload.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("La imagen no puede superar los 5 MB.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = "jpg"
    elif len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        extension = "webp"
    else:
        raise ValueError("La imagen debe estar en formato JPG, PNG o WebP.")
    upload_folder = current_app.config["PRODUCT_IMAGE_UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)
    filename = f"{uuid4().hex}.{extension}"
    with open(os.path.join(upload_folder, filename), "wb") as image_file:
        image_file.write(data)
    return filename


def delete_product_image(filename):
    if not filename:
        return
    safe_filename = os.path.basename(filename)
    image_path = os.path.join(current_app.config["PRODUCT_IMAGE_UPLOAD_FOLDER"], safe_filename)
    if os.path.isfile(image_path):
        os.remove(image_path)


def ensure_schema():
    """Actualiza instalaciones SQLite anteriores sin borrar ventas ni stock."""
    make_subcategories_independent(db.engine)
    additions = {
        "user": {
            "first_name": "VARCHAR(100) NOT NULL DEFAULT ''",
            "last_name": "VARCHAR(100) NOT NULL DEFAULT ''",
            "dni": "VARCHAR(30)",
            "phone": "VARCHAR(60) NOT NULL DEFAULT ''",
            "email": "VARCHAR(160)",
            "address": "VARCHAR(250) NOT NULL DEFAULT ''",
            "created_at": "DATETIME",
            "last_login_at": "DATETIME",
            "last_logout_at": "DATETIME",
        },
        "product": {
            "subcategory_id": "INTEGER", "supplier_id": "INTEGER", "units_per_box": "INTEGER NOT NULL DEFAULT 1",
            "closed_boxes": "INTEGER NOT NULL DEFAULT 0", "loose_units": "INTEGER NOT NULL DEFAULT 0",
            "box_price": "NUMERIC(14,2) NOT NULL DEFAULT 0", "unit_price": "NUMERIC(14,2) NOT NULL DEFAULT 0",
            "has_box_presentation": "BOOLEAN NOT NULL DEFAULT 0",
            "image_filename": "VARCHAR(100)",
        },
        "customer": {"discount_percent": "NUMERIC(5,2) NOT NULL DEFAULT 0", "first_name": "VARCHAR(100) NOT NULL DEFAULT ''", "last_name": "VARCHAR(100) NOT NULL DEFAULT ''", "dni": "VARCHAR(30)", "address": "VARCHAR(250) NOT NULL DEFAULT ''", "debt_balance": "NUMERIC(14,2) NOT NULL DEFAULT 0"},
        "supplier": {"company_name": "VARCHAR(160) NOT NULL DEFAULT ''"},
        "sale_item": {"presentation": "VARCHAR(20)", "sale_quantity": "NUMERIC(14,3)", "discount_percent": "NUMERIC(5,2) NOT NULL DEFAULT 0"},
        "sale": {"cash_received": "NUMERIC(14,2) NOT NULL DEFAULT 0", "change_due": "NUMERIC(14,2) NOT NULL DEFAULT 0"},
    }
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in additions.items():
        if table not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table)}
        for name, definition in columns.items():
            if name not in existing_columns:
                db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    # Productos creados antes de esta actualización se conservan como unidades sueltas.
    db.session.execute(text("UPDATE product SET units_per_box = 1 WHERE units_per_box IS NULL OR units_per_box < 1"))
    db.session.execute(text("UPDATE product SET has_box_presentation = 1 WHERE units_per_box > 1 AND has_box_presentation = 0"))
    db.session.execute(text("UPDATE product SET closed_boxes = CAST(stock AS INTEGER), loose_units = 0, box_price = sale_price, unit_price = sale_price WHERE closed_boxes = 0 AND loose_units = 0 AND stock > 0"))
    db.session.commit()


def seed_data():
    if not User.query.first():
        owner = User(username="dueno", role="owner", first_name="Dueño", last_name="Principal")
        owner.set_password("Cambiar123!")
        employee = User(username="empleado", role="employee", first_name="Empleado", last_name="Inicial")
        employee.set_password("Empleado123!")
        db.session.add_all([owner, employee])
        db.session.commit()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "desarrollo-cambiar-antes-de-produccion"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///ferresoft.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        PRODUCT_IMAGE_UPLOAD_FOLDER=os.path.join(app.static_folder, "uploads", "products"),
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)

    @app.context_processor
    def inject_globals():
        return {"current_user": current_user(), "today": local_now()}

    @app.template_filter("ars")
    def ars_filter(value):
        return f"$ {Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @app.route("/")
    @login_required
    def dashboard():
        confirmed = Sale.query.filter_by(status="CONFIRMADA")
        today_start = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_total = confirmed.filter(Sale.created_at >= today_start).with_entities(func.coalesce(func.sum(Sale.total), 0)).scalar()
        critical = Product.query.filter(Product.is_stock_critical).count()
        return render_template("dashboard.html", daily_total=daily_total, critical=critical, sale_count=confirmed.count())

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
            if user and user.active and user.verify_password(request.form.get("password", "")):
                session.clear(); session["user_id"] = user.id
                user.last_login_at = local_now()
                audit(user, "INICIAR_SESION", "session", user.id, "Ingreso correcto al sistema")
                db.session.commit()
                return redirect(url_for("dashboard"))
            flash("Usuario o contraseña incorrectos.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        user = current_user()
        if user:
            user.last_logout_at = local_now()
            audit(user, "CERRAR_SESION", "session", user.id, "Cierre de sesión")
            db.session.commit()
        session.clear(); return redirect(url_for("login"))

    @app.route("/employees", methods=["GET", "POST"])
    @owner_required
    def employees():
        form_data, field_errors = {}, {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            first_name = " ".join(request.form.get("first_name", "").split())
            last_name = " ".join(request.form.get("last_name", "").split())
            raw_dni = request.form.get("dni", "").strip()
            dni = re.sub(r"\D", "", raw_dni)
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip().lower()
            address = " ".join(request.form.get("address", "").split())
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "")

            required_fields = {
                "first_name": (first_name, "Ingresá el nombre."),
                "last_name": (last_name, "Ingresá el apellido."),
                "dni": (raw_dni, "Ingresá el DNI."),
                "phone": (phone, "Ingresá el teléfono o celular."),
                "email": (email, "Ingresá el correo electrónico."),
                "address": (address, "Ingresá la dirección."),
                "username": (username, "Ingresá un usuario."),
                "password": (password, "Ingresá una contraseña."),
            }
            for field, (value, message) in required_fields.items():
                if not value:
                    field_errors[field] = message
            if first_name and not 2 <= len(first_name) <= 100:
                field_errors["first_name"] = "El nombre debe tener entre 2 y 100 caracteres."
            if last_name and not 2 <= len(last_name) <= 100:
                field_errors["last_name"] = "El apellido debe tener entre 2 y 100 caracteres."
            if raw_dni and (not re.fullmatch(r"[0-9.\s-]+", raw_dni) or not 7 <= len(dni) <= 9):
                field_errors["dni"] = "El DNI debe contener entre 7 y 9 números."
            if dni and User.query.filter_by(dni=dni).first():
                field_errors["dni"] = "Ya existe una cuenta con este DNI."
            phone_digits = re.sub(r"\D", "", phone)
            if phone and (len(phone) > 30 or not re.fullmatch(r"[+0-9()\s-]+", phone) or not 8 <= len(phone_digits) <= 15):
                field_errors["phone"] = "El teléfono debe contener entre 8 y 15 números; podés usar +, espacios, paréntesis o guiones."
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                field_errors["email"] = "Ingresá un correo electrónico válido."
            if email and len(email) > 160:
                field_errors["email"] = "El correo no puede superar 160 caracteres."
            if email and User.query.filter(func.lower(User.email) == email).first():
                field_errors["email"] = "Ya existe una cuenta con este correo."
            if address and not 5 <= len(address) <= 250:
                field_errors["address"] = "La dirección debe tener entre 5 y 250 caracteres."
            if username and not re.fullmatch(r"[A-Za-z0-9._-]{3,60}", username):
                field_errors["username"] = "Usá entre 3 y 60 letras, números, puntos, guiones o guiones bajos."
            if username and User.query.filter(func.lower(User.username) == username.lower()).first():
                field_errors["username"] = "Este usuario ya existe."
            if password and not 8 <= len(password) <= 128:
                field_errors["password"] = "La contraseña debe tener entre 8 y 128 caracteres."
            elif password and not (re.search(r"[A-Z]", password) and re.search(r"[a-z]", password) and re.search(r"\d", password)):
                field_errors["password"] = "La contraseña debe incluir al menos una mayúscula, una minúscula y un número."
            if role not in {"owner", "employee"}:
                field_errors["role"] = "Seleccioná un tipo de cuenta válido."

            if not field_errors:
                try:
                    new_user = User(
                        first_name=first_name, last_name=last_name, dni=dni, phone=phone,
                        email=email, address=address, username=username, role=role,
                    )
                    new_user.set_password(password)
                    db.session.add(new_user); db.session.flush()
                    audit(current_user(), "CREAR_CUENTA", "user", new_user.id, f"{new_user.full_name} · {new_user.username} · {new_user.role}")
                    db.session.commit()
                    flash("Cuenta creada correctamente.", "success")
                    return redirect(url_for("employees"))
                except Exception as exc:
                    current_app.logger.exception("Error al crear una cuenta")
                    db.session.rollback()
                    flash(f"No se pudo crear la cuenta: {exc}", "error")
            else:
                flash("Revisá los campos marcados.", "error")

        selected_user = request.args.get("user_id", "").strip()
        selected_action = request.args.get("action", "").strip()
        activity_query = AuditEvent.query
        if selected_user.isdigit():
            activity_query = activity_query.filter_by(user_id=int(selected_user))
        if selected_action:
            activity_query = activity_query.filter_by(action=selected_action)
        activities = activity_query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).all()
        accounts = User.query.order_by(User.last_name, User.first_name, User.username).all()
        actions = [row[0] for row in db.session.query(AuditEvent.action).distinct().order_by(AuditEvent.action).all()]
        return render_template(
            "employees.html", accounts=accounts, activities=activities, actions=actions,
            activity_labels=ACTIVITY_LABELS, form_data=form_data, field_errors=field_errors,
            selected_user=selected_user, selected_action=selected_action,
        )

    @app.post("/employees/<int:user_id>/delete")
    @owner_required
    def delete_employee(user_id):
        account = db.session.get(User, user_id)
        if not account:
            abort(404)
        actor = current_user()
        if account.id == actor.id:
            flash("No podés eliminar la cuenta con la que estás conectado.", "error")
        elif not account.active:
            flash("La cuenta ya está eliminada.", "error")
        elif account.role == "owner" and User.query.filter_by(role="owner", active=True).count() <= 1:
            flash("No se puede eliminar la única cuenta de dueño activa.", "error")
        else:
            account.active = False
            audit(actor, "ELIMINAR_CUENTA", "user", account.id, f"{account.full_name} · {account.username} · {account.role}")
            db.session.commit()
            flash("Cuenta eliminada. Sus ventas y registros de actividad se conservaron.", "success")
        return redirect(url_for("employees"))

    @app.route("/products")
    @login_required
    def products():
        query = request.args.get("q", "").strip()
        products_query = Product.query.filter_by(active=True)
        if query:
            pattern = f"%{query}%"
            products_query = products_query.filter(db.or_(Product.name.ilike(pattern), Product.code.ilike(pattern)))
        template = "_product_table.html" if request.args.get("partial") == "1" else "products.html"
        return render_template(template, products=alphabetical(products_query), query=query)

    @app.route("/products/<int:product_id>/barcode")
    @login_required
    def product_barcode(product_id):
        product = db.get_or_404(Product, product_id)
        try:
            image = barcode_image(product.code)
        except ValueError as exc:
            return render_template("product_barcode.html", product=product, barcode_image=None, barcode_error=str(exc)), 422
        return render_template("product_barcode.html", product=product, barcode_image=image, barcode_error=None)

    @app.route("/products/generate-code")
    @owner_required
    def product_generate_code():
        return {"code": generate_unique_product_code()}

    @app.route("/products/new", methods=["GET", "POST"])
    @owner_required
    def product_new():
        form_data, field_errors = {}, {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            new_image_filename = None
            try:
                identity, field_errors = validate_product_identity(request.form)
                if field_errors: raise ValueError("Revise los campos marcados.")
                units_per_box = int(request.form.get("units_per_box") or 1)
                closed_boxes = int(request.form.get("closed_boxes") or 0)
                loose_units = int(request.form.get("loose_units") or 0)
                if units_per_box < 1 or closed_boxes < 0 or loose_units < 0: raise ValueError("Las cantidades no pueden ser negativas")
                has_box_presentation = "has_box_presentation" in request.form
                minimum_stock = minimum_stock_value(request.form)
                box_price = money(request.form.get("box_price") or 0)
                unit_price = money(request.form["unit_price"])
                if has_box_presentation and box_price <= 0: raise ValueError("Debe indicar el precio por caja")
                new_image_filename = save_product_image(request.files.get("image"))
                product = Product(**identity,
                    description=request.form.get("description", "").strip(),
                    purchase_price=money(request.form.get("purchase_price") or 0), sale_price=box_price if has_box_presentation else unit_price,
                    box_price=box_price, unit_price=unit_price, has_box_presentation=has_box_presentation,
                    units_per_box=units_per_box, closed_boxes=closed_boxes, loose_units=loose_units,
                    supplier_id=request.form.get("supplier_id") or None, stock=Decimal(0), minimum_stock=minimum_stock,
                    image_filename=new_image_filename)
                product.sync_stock()
                db.session.add(product); db.session.flush()
                if product.stock:
                    db.session.add(InventoryMovement(product_id=product.id, user_id=current_user().id, quantity=product.stock, movement_type="ALTA", reason="Stock inicial", reference=None))
                audit(current_user(), "CREAR_PRODUCTO", "product", product.id, product.name)
                db.session.commit(); flash("Producto creado.", "success"); return redirect(url_for("products"))
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback()
                if new_image_filename: delete_product_image(new_image_filename)
                flash(f"No se pudo crear el producto: {exc}", "error")
        return render_template("product_form.html", product=None, form_data=form_data, field_errors=field_errors, product_names=product_name_options(), categories=alphabetical(Category.query), subcategories=alphabetical(Subcategory.query), suppliers=alphabetical(Supplier.query.filter_by(active=True)))

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @owner_required
    def product_edit(product_id):
        product = db.get_or_404(Product, product_id)
        form_data = {column.name: getattr(product, column.name) for column in Product.__table__.columns}
        field_errors = {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            new_image_filename = None
            old_image_filename = product.image_filename
            try:
                identity, field_errors = validate_product_identity(request.form, product.id)
                if field_errors: raise ValueError("Revise los campos marcados.")
                old_price = product.box_price
                product.code = identity["code"]
                product.name = identity["name"]; product.description = request.form.get("description", "").strip()
                product.purchase_price = money(request.form.get("purchase_price") or 0); product.box_price = money(request.form.get("box_price") or 0); product.unit_price = money(request.form["unit_price"]); product.has_box_presentation = "has_box_presentation" in request.form; product.sale_price = product.box_price or product.unit_price
                product.minimum_stock = minimum_stock_value(request.form); product.active = "active" in request.form
                product.category_id = identity["category_id"]; product.subcategory_id = identity["subcategory_id"]; product.supplier_id = request.form.get("supplier_id") or None
                if int(request.form.get("units_per_box") or 1) < 1: raise ValueError("Unidades por caja debe ser mayor a cero")
                product.units_per_box = int(request.form.get("units_per_box") or 1)
                if not product.has_box_presentation:
                    product.loose_units += product.closed_boxes * product.units_per_box
                    product.closed_boxes = 0
                    product.units_per_box = 1
                    product.box_price = Decimal(0)
                if request.files.get("image") and request.files["image"].filename:
                    new_image_filename = save_product_image(request.files["image"])
                    product.image_filename = new_image_filename
                product.sync_stock()
                audit(current_user(), "EDITAR_PRODUCTO", "product", product.id, product.name)
                if old_price != product.box_price: audit(current_user(), "CAMBIAR_PRECIO", "product", product.id, f"{old_price} a {product.box_price}")
                db.session.commit()
                if new_image_filename and old_image_filename: delete_product_image(old_image_filename)
                flash("Producto actualizado.", "success"); return redirect(url_for("products"))
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback()
                if new_image_filename: delete_product_image(new_image_filename)
                flash(f"No se pudo actualizar: {exc}", "error")
        return render_template("product_form.html", product=product, form_data=form_data, field_errors=field_errors, product_names=product_name_options(), categories=alphabetical(Category.query), subcategories=alphabetical(Subcategory.query), suppliers=alphabetical(Supplier.query.filter_by(active=True)))

    @app.route("/products/<int:product_id>/adjust", methods=["POST"])
    @owner_required
    def product_adjust(product_id):
        product = db.get_or_404(Product, product_id)
        try:
            quantity = int(request.form["quantity"])
            reason = request.form["reason"].strip()
            if not quantity or not reason: raise ValueError("Cantidad y motivo son obligatorios")
            if product.available_units + quantity < 0: raise ValueError("El ajuste no puede dejar stock negativo")
            product.loose_units += quantity
            if product.loose_units < 0:
                boxes_to_open = (-product.loose_units + product.units_per_box - 1) // product.units_per_box
                if product.closed_boxes < boxes_to_open: raise ValueError("No hay cajas suficientes")
                product.closed_boxes -= boxes_to_open; product.loose_units += boxes_to_open * product.units_per_box
            product.sync_stock()
            db.session.add(InventoryMovement(product_id=product.id, user_id=current_user().id, quantity=quantity, movement_type="AJUSTE", reason=reason, reference=None))
            audit(current_user(), "AJUSTAR_STOCK", "product", product.id, f"{quantity}: {reason}")
            db.session.commit(); flash("Stock ajustado.", "success")
        except Exception as exc:
            current_app.logger.exception("Error al procesar %s", request.path)
            db.session.rollback(); flash(f"No se pudo ajustar el stock: {exc}", "error")
        return redirect(url_for("products"))

    @app.route("/products/<int:product_id>/loss", methods=["GET", "POST"])
    @owner_required
    def product_loss(product_id):
        product = db.get_or_404(Product, product_id)
        if not product.active:
            flash("No se puede registrar una merma sobre un producto eliminado.", "error")
            return redirect(url_for("products"))
        form_data = {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            try:
                presentation = request.form.get("presentation", "UNIDAD").upper()
                quantity = int(request.form.get("quantity") or 0)
                loss_type = request.form.get("loss_type", "").strip().upper()
                reason_detail = " ".join(request.form.get("reason_detail", "").split())
                loss_labels = {"ROTURA": "Rotura", "DESPERFECTO": "Desperfecto", "OTRO": "Otro motivo"}
                if presentation not in {"UNIDAD", "CAJA"}:
                    raise ValueError("Seleccioná una presentación válida.")
                if presentation == "CAJA" and not product.has_box_presentation:
                    raise ValueError("Este producto se administra solamente por unidades.")
                if quantity <= 0:
                    raise ValueError("La cantidad a descontar debe ser mayor a cero.")
                if loss_type not in loss_labels:
                    raise ValueError("Seleccioná si se trata de una rotura, desperfecto u otro motivo.")
                if not 5 <= len(reason_detail) <= 300:
                    raise ValueError("La descripción del motivo debe tener entre 5 y 300 caracteres.")

                stock_before = product.available_units
                if presentation == "CAJA":
                    if quantity > product.closed_boxes:
                        raise ValueError("No hay suficientes cajas cerradas para descontar esa cantidad.")
                    product.closed_boxes -= quantity
                    removed_units = quantity * product.units_per_box
                    quantity_label = f"{quantity} caja(s)"
                else:
                    if quantity > product.available_units:
                        raise ValueError("No hay suficientes unidades disponibles para descontar esa cantidad.")
                    if product.loose_units < quantity:
                        boxes_to_open = (quantity - product.loose_units + product.units_per_box - 1) // product.units_per_box
                        product.closed_boxes -= boxes_to_open
                        product.loose_units += boxes_to_open * product.units_per_box
                    product.loose_units -= quantity
                    removed_units = quantity
                    quantity_label = f"{quantity} unidad(es)"

                product.sync_stock()
                reason = f"{loss_labels[loss_type]} — {reason_detail}"
                actor = current_user()
                db.session.add(InventoryMovement(
                    product_id=product.id, user_id=actor.id, quantity=-removed_units,
                    movement_type="MERMA", reason=reason, reference=None,
                ))
                audit(
                    actor, "REGISTRAR_MERMA", "product", product.id,
                    f"{product.name}: -{quantity_label} ({removed_units} unidades); {reason}; stock {stock_before}->{product.available_units} unidades",
                )
                db.session.commit()
                flash("Merma registrada y stock actualizado correctamente.", "success")
                return redirect(url_for("products"))
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback()
                flash(f"No se pudo registrar la merma: {exc}", "error")
        return render_template("product_loss_form.html", product=product, form_data=form_data)

    @app.route("/products/<int:product_id>/restock", methods=["GET", "POST"])
    @owner_required
    def product_restock(product_id):
        product = db.get_or_404(Product, product_id)
        form_data = {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            try:
                add_boxes = int(request.form.get("add_boxes") or 0)
                add_units = int(request.form.get("add_units") or 0)
                if add_boxes < 0 or add_units < 0: raise ValueError("La reposición no puede ser negativa")
                added = add_units
                if product.has_box_presentation:
                    added += add_boxes * product.units_per_box
                    product.closed_boxes += add_boxes
                product.loose_units += add_units
                if added <= 0: raise ValueError("Indique una cantidad a reponer")
                old_purchase, old_unit, old_box = product.purchase_price, product.unit_price, product.box_price
                product.purchase_price = money(request.form.get("purchase_price") or 0)
                product.unit_price = money(request.form["unit_price"])
                if product.has_box_presentation:
                    product.box_price = money(request.form["box_price"])
                    if product.box_price <= 0: raise ValueError("El precio por caja debe ser mayor a cero")
                product.sale_price = product.box_price if product.has_box_presentation else product.unit_price
                product.sync_stock()
                db.session.add(InventoryMovement(product_id=product.id, user_id=current_user().id, quantity=added, movement_type="REPOSICION", reason="Reposición de mercadería", reference=None))
                audit(current_user(), "REPOSICION", "product", product.id, f"+{added} unidades; compra {old_purchase}->{product.purchase_price}; venta unidad {old_unit}->{product.unit_price}; venta caja {old_box}->{product.box_price}")
                db.session.commit(); flash("Reposición y precios actualizados.", "success"); return redirect(url_for("products"))
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback(); flash(f"No se pudo registrar la reposición: {exc}", "error")
        return render_template("restock_form.html", product=product, form_data=form_data)

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    @owner_required
    def product_delete(product_id):
        product = db.get_or_404(Product, product_id)
        try:
            # Baja lógica: preserva el historial de ventas, remitos y auditoría.
            product.active = False
            audit(current_user(), "ELIMINAR_PRODUCTO", "product", product.id, f"Baja lógica: {product.name}")
            db.session.commit()
            flash("Producto eliminado del catálogo. Su historial se conserva.", "success")
        except Exception as exc:
            current_app.logger.exception("Error al procesar %s", request.path)
            db.session.rollback(); flash(f"No se pudo eliminar el producto: {exc}", "error")
        return redirect(url_for("products"))

    @app.route("/sales/new")
    @login_required
    def sale_new():
        return render_template("sale_new.html", products=alphabetical(Product.query.filter_by(active=True)), customers=alphabetical(Customer.query.filter_by(active=True)))

    @app.route("/suppliers", methods=["GET", "POST"])
    @owner_required
    def suppliers():
        form_data, field_errors = {}, {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            try:
                name = request.form.get("name", "").strip()
                if Supplier.query.filter_by(name=name).first(): field_errors["name"] = "Ya existe un proveedor con este nombre."
                if field_errors: raise ValueError("Revise los campos marcados.")
                supplier = Supplier(name=name, company_name=request.form.get("company_name", "").strip(), phone=request.form.get("phone", "").strip(), email=request.form.get("email", "").strip(), notes=request.form.get("notes", "").strip())
                db.session.add(supplier); db.session.flush(); audit(current_user(), "CREAR_PROVEEDOR", "supplier", supplier.id, supplier.name)
                db.session.commit(); flash("Proveedor agregado.", "success")
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback(); flash(f"No se pudo agregar el proveedor: {exc}", "error")
        return render_template("suppliers.html", suppliers=alphabetical(Supplier.query), form_data=form_data, field_errors=field_errors)

    @app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
    @owner_required
    def supplier_edit(supplier_id):
        supplier = db.get_or_404(Supplier, supplier_id)
        if request.method == "POST":
            try:
                supplier.name = request.form["name"].strip(); supplier.company_name = request.form.get("company_name", "").strip()
                supplier.phone = request.form.get("phone", "").strip(); supplier.email = request.form.get("email", "").strip(); supplier.notes = request.form.get("notes", "").strip()
                audit(current_user(), "EDITAR_PROVEEDOR", "supplier", supplier.id, supplier.name); db.session.commit(); flash("Proveedor actualizado.", "success")
                return redirect(url_for("suppliers"))
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback(); flash(f"No se pudo actualizar: {exc}", "error")
        return render_template("supplier_form.html", supplier=supplier)

    @app.route("/suppliers/<int:supplier_id>")
    @owner_required
    def supplier_detail(supplier_id):
        supplier = db.get_or_404(Supplier, supplier_id)
        products = alphabetical(Product.query.filter_by(supplier_id=supplier.id, active=True))
        return render_template("supplier_detail.html", supplier=supplier, products=products)

    @app.route("/suppliers/<int:supplier_id>/products/<int:product_id>/remove", methods=["POST"])
    @owner_required
    def supplier_remove_product(supplier_id, product_id):
        supplier = db.get_or_404(Supplier, supplier_id)
        product = db.get_or_404(Product, product_id)
        if product.supplier_id != supplier.id:
            abort(404)
        product.supplier_id = None
        audit(current_user(), "QUITAR_PRODUCTO_PROVEEDOR", "product", product.id, f"Producto quitado de {supplier.name}")
        db.session.commit()
        flash("Producto quitado del proveedor. El producto continúa disponible en el catálogo.", "success")
        return redirect(url_for("supplier_detail", supplier_id=supplier.id))

    @app.route("/catalog", methods=["GET", "POST"])
    @owner_required
    def catalog():
        if request.method == "POST":
            try:
                action = request.form.get("action")

                def get_entry(model, field):
                    entry = db.session.get(model, int(request.form.get(field, "0")))
                    if entry is None:
                        raise ValueError("La categoría o subcategoría ya no existe.")
                    return entry

                if action in {"category", "subcategory", "edit_category", "edit_subcategory"}:
                    name = request.form.get("name", "").strip()
                    if not name or len(name) > 100:
                        raise ValueError("El nombre debe tener entre 1 y 100 caracteres.")
                    is_category = action in {"category", "edit_category"}
                    model = Category if is_category else Subcategory
                    entry = get_entry(model, "id") if action.startswith("edit_") else None
                    duplicates = model.query.filter(func.lower(model.name) == name.lower())
                    if entry:
                        duplicates = duplicates.filter(model.id != entry.id)
                    if duplicates.first():
                        raise ValueError("Ya existe una categoría con ese nombre." if is_category else "Ya existe una subcategoría con ese nombre.")
                    if entry is None:
                        entry = model(name=name)
                        db.session.add(entry)
                    else:
                        entry.name = name
                    db.session.flush()
                    audit(current_user(), ("EDITAR_" if action.startswith("edit_") else "CREAR_") + ("CATEGORIA" if is_category else "SUBCATEGORIA"), "category" if is_category else "subcategory", entry.id, entry.name)
                    message = "Catálogo actualizado."
                elif action == "delete_subcategory":
                    entry = get_entry(Subcategory, "id")
                    Product.query.filter_by(subcategory_id=entry.id).update({Product.subcategory_id: None}, synchronize_session=False)
                    audit(current_user(), "ELIMINAR_SUBCATEGORIA", "subcategory", entry.id, entry.name)
                    db.session.delete(entry)
                    message = "Subcategoría eliminada. Los productos se conservaron con su categoría."
                elif action == "delete_category":
                    entry = get_entry(Category, "id")
                    Product.query.filter_by(category_id=entry.id).update({Product.category_id: None}, synchronize_session=False)
                    audit(current_user(), "ELIMINAR_CATEGORIA", "category", entry.id, entry.name)
                    db.session.delete(entry)
                    message = "Categoría eliminada. Los productos y sus subcategorías se conservaron."
                else:
                    raise ValueError("Acción no válida")
                db.session.commit()
                flash(message, "success")
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback()
                flash(f"No se pudo actualizar el catálogo: {exc}", "error")
            return redirect(url_for("catalog"))
        return render_template("catalog.html", categories=alphabetical(Category.query), subcategories=alphabetical(Subcategory.query))

    @app.route("/customers", methods=["GET", "POST"])
    @login_required
    def customers():
        form_data, field_errors = {}, {}
        if request.method == "POST":
            form_data = request.form.to_dict()
            try:
                discount = Decimal(request.form.get("discount_percent", 0))
                if not 0 <= discount <= 100: raise ValueError("El descuento debe estar entre 0 y 100")
                first_name = request.form["first_name"].strip()
                last_name = request.form["last_name"].strip()
                dni = request.form.get("dni", "").strip() or None
                if not first_name or not last_name: raise ValueError("Nombre y apellido son obligatorios")
                if dni and Customer.query.filter_by(dni=dni).first(): field_errors["dni"] = "Ya existe un cliente con este DNI."
                if field_errors: raise ValueError("Revise los campos marcados.")
                customer = Customer(name=f"{last_name}, {first_name}", first_name=first_name, last_name=last_name, dni=dni, phone=request.form.get("phone", "").strip(), address=request.form.get("address", "").strip(), condition=request.form.get("condition", "Al dia"), discount_percent=discount)
                db.session.add(customer); db.session.flush(); audit(current_user(), "CREAR_CLIENTE", "customer", customer.id, customer.name)
                db.session.commit(); flash("Cliente agregado.", "success")
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback(); flash(f"No se pudo agregar el cliente: {exc}", "error")
        return render_template("customers.html", customers=alphabetical(Customer.query.filter_by(active=True)), debt_customers=alphabetical(Customer.query.filter(Customer.debt_balance > 0)), form_data=form_data, field_errors=field_errors)

    @app.route("/customers/<int:customer_id>/delete", methods=["POST"])
    @owner_required
    def customer_delete(customer_id):
        customer = db.get_or_404(Customer, customer_id)
        try:
            if customer.active:
                customer.active = False
                audit(current_user(), "ELIMINAR_CLIENTE", "customer", customer.id, customer.name)
                db.session.commit()
            flash("Cliente eliminado de la lista. Sus ventas anteriores y deudas se conservan.", "success")
        except Exception as exc:
            current_app.logger.exception("Error al procesar %s", request.path)
            db.session.rollback()
            flash(f"No se pudo eliminar el cliente: {exc}", "error")
        return redirect(url_for("customers"))

    @app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
    @owner_required
    def customer_edit(customer_id):
        customer = db.get_or_404(Customer, customer_id)
        if request.method == "POST":
            try:
                customer.first_name = request.form["first_name"].strip(); customer.last_name = request.form["last_name"].strip()
                customer.name = f"{customer.last_name}, {customer.first_name}"; customer.phone = request.form.get("phone", "").strip(); customer.address = request.form.get("address", "").strip()
                discount = Decimal(request.form.get("discount_percent") or 0)
                if not 0 <= discount <= 100: raise ValueError("El descuento debe estar entre 0 y 100")
                customer.discount_percent = discount; audit(current_user(), "EDITAR_CLIENTE", "customer", customer.id, customer.name)
                db.session.commit(); flash("Cliente actualizado.", "success"); return redirect(url_for("customers"))
            except Exception as exc:
                current_app.logger.exception("Error al procesar %s", request.path)
                db.session.rollback(); flash(f"No se pudo actualizar: {exc}", "error")
        return render_template("customer_form.html", customer=customer)

    @app.route("/sales", methods=["POST"])
    @login_required
    def sale_create():
        try:
            cart = request.get_json(silent=True) or {}
            lines = cart.get("items", [])
            if not lines: raise ValueError("El carrito está vacío")
            payments = cart.get("payments", [])
            if not payments: raise ValueError("Debe registrar un pago")
            customer = db.session.get(Customer, int(cart["customer_id"])) if cart.get("customer_id") else None
            if cart.get("customer_id") and (customer is None or not customer.active):
                raise ValueError("El cliente seleccionado ya no está disponible. Seleccione otro cliente o quite la selección.")
            sale = Sale(user_id=current_user().id, customer_id=customer.id if customer else None, total=0)
            db.session.add(sale); db.session.flush()
            total = Decimal(0)
            for line in lines:
                product_id = line.get("product_id", line.get("id"))
                if not product_id: raise ValueError("El carrito contiene un producto sin identificador")
                product = db.session.get(Product, int(product_id))
                if not product or not product.active: raise ValueError("Producto inválido o inactivo")
                presentation = line.get("presentation")
                sale_quantity = Decimal(str(line["quantity"]))
                quantity = sale_quantity * product.units_per_box if presentation == "CAJA" else sale_quantity
                if quantity <= 0: raise ValueError("Cantidad inválida")
                if presentation not in {"CAJA", "UNIDAD"}: raise ValueError("Debe seleccionar caja o unidad")
                if presentation == "CAJA" and not product.has_box_presentation: raise ValueError(f"{product.name} se vende sólo por unidad")
                if product.available_units < quantity: raise ValueError(f"Stock insuficiente para {product.name}")
                if presentation == "CAJA":
                    if sale_quantity != int(sale_quantity) or product.closed_boxes < int(sale_quantity): raise ValueError(f"No hay cajas cerradas suficientes para {product.name}")
                    product.closed_boxes -= int(sale_quantity); base_price = product.box_price
                else:
                    units = int(sale_quantity)
                    if sale_quantity != units: raise ValueError("Las unidades deben ser enteras")
                    if product.loose_units < units:
                        boxes_to_open = (units - product.loose_units + product.units_per_box - 1) // product.units_per_box
                        product.closed_boxes -= boxes_to_open; product.loose_units += boxes_to_open * product.units_per_box
                    product.loose_units -= units; base_price = product.unit_price
                product.sync_stock()
                discount = Decimal(customer.discount_percent if customer else 0)
                unit_price = (base_price * (Decimal(1) - discount / Decimal(100))).quantize(Decimal("0.01"))
                subtotal = (unit_price * sale_quantity).quantize(Decimal("0.01")); total += subtotal
                db.session.add(SaleItem(sale_id=sale.id, product_id=product.id, quantity=quantity, sale_quantity=sale_quantity, presentation=presentation, discount_percent=discount, unit_price=unit_price, subtotal=subtotal))
                db.session.add(InventoryMovement(product_id=product.id, user_id=current_user().id, quantity=-quantity, movement_type="VENTA", reason=f"Venta {presentation.lower()}", reference=f"V-{sale.id}"))
            allowed_methods = {"EFECTIVO", "DEBITO", "CREDITO", "TRANSFERENCIA", "QR"}
            parsed_payments = []
            for payment in payments:
                method = payment["method"]
                amount = money(payment["amount"])
                if method not in allowed_methods: raise ValueError("Medio de pago inválido")
                if amount <= 0: raise ValueError("Los importes de pago deben ser mayores a cero")
                parsed_payments.append([method, amount])
            paid = sum((amount for _, amount in parsed_payments), Decimal(0))
            if paid < total: raise ValueError(f"Pago insuficiente. Faltan {(total - paid):.2f}")
            change_due = (paid - total).quantize(Decimal("0.01"))
            cash_received = sum((amount for method, amount in parsed_payments if method == "EFECTIVO"), Decimal(0))
            if change_due > cash_received: raise ValueError("El importe excedente sólo puede corresponder a un pago en efectivo")
            remaining_change = change_due
            for method, amount in parsed_payments:
                applied_amount = amount
                if method == "EFECTIVO" and remaining_change > 0:
                    reduction = min(applied_amount, remaining_change)
                    applied_amount -= reduction; remaining_change -= reduction
                if applied_amount > 0:
                    db.session.add(Payment(sale_id=sale.id, method=method, amount=applied_amount))
            sale.total = total
            sale.cash_received = cash_received
            sale.change_due = change_due
            audit(current_user(), "CONFIRMAR_VENTA", "sale", sale.id, f"Total {total}")
            db.session.commit()
            return {"ok": True, "sale_id": sale.id, "change_due": str(change_due), "redirect": url_for("sale_receipt", sale_id=sale.id)}
        except Exception as exc:
            current_app.logger.exception("Error al procesar %s", request.path)
            db.session.rollback(); return {"ok": False, "error": str(exc)}, 400

    @app.route("/sales")
    @login_required
    def sales():
        sales = Sale.query.order_by(Sale.created_at.desc()).limit(100).all()
        return render_template("sales.html", sales=sales)

    @app.route("/sales/<int:sale_id>/receipt")
    @login_required
    def sale_receipt(sale_id):
        return render_template("receipt.html", sale=db.get_or_404(Sale, sale_id))

    @app.route("/sales/<int:sale_id>/cancel", methods=["POST"])
    @login_required
    def sale_cancel(sale_id):
        sale = db.get_or_404(Sale, sale_id)
        try:
            if sale.status != "CONFIRMADA": raise ValueError("La venta ya fue anulada")
            reason = request.form.get("reason", "").strip()
            if not reason: raise ValueError("Debe indicar un motivo")
            for item in sale.items:
                if item.presentation == "CAJA": item.product.closed_boxes += int(item.sale_quantity)
                else: item.product.loose_units += int(item.sale_quantity or item.quantity)
                item.product.sync_stock()
                db.session.add(InventoryMovement(product_id=item.product_id, user_id=current_user().id, quantity=item.quantity, movement_type="ANULACION", reason=reason, reference=f"V-{sale.id}"))
            sale.status = "ANULADA"; sale.cancelled_at = local_now(); sale.cancellation_reason = reason
            audit(current_user(), "ANULAR_VENTA", "sale", sale.id, reason)
            db.session.commit(); flash("Ticket anulado y stock restituido.", "success")
        except Exception as exc:
            current_app.logger.exception("Error al procesar %s", request.path)
            db.session.rollback(); flash(f"No se pudo anular: {exc}", "error")
        return redirect(url_for("sales"))

    @app.route("/reports/sales")
    @login_required
    def sales_report():
        owner_view = current_user().role == "owner"
        start_value = request.args.get("start_date", "").strip()
        end_value = request.args.get("end_date", "").strip()
        selected_user = request.args.get("user_id", "").strip() if owner_view else ""
        selected_payment = request.args.get("payment_method", "").strip().upper()
        selected_status = request.args.get("status", "CONFIRMADA").strip().upper()
        selected_action = request.args.get("action", "").strip() if owner_view else ""
        selected_group = request.args.get("activity_group", "").strip().upper() if owner_view else ""
        search = request.args.get("q", "").strip()

        start_date = end_date = None
        try:
            start_date = date.fromisoformat(start_value) if start_value else None
            end_date = date.fromisoformat(end_value) if end_value else None
            if start_date and end_date and start_date > end_date:
                raise ValueError("La fecha desde no puede ser posterior a la fecha hasta.")
        except ValueError as exc:
            flash(str(exc) if "posterior" in str(exc) else "Las fechas seleccionadas no son válidas.", "error")
            start_date = end_date = None
            start_value = end_value = ""

        if selected_status not in {"", "CONFIRMADA", "ANULADA"}:
            selected_status = "CONFIRMADA"

        sales_query = Sale.query
        if selected_status:
            sales_query = sales_query.filter(Sale.status == selected_status)
        if start_date:
            sales_query = sales_query.filter(Sale.created_at >= datetime.combine(start_date, time.min))
        if end_date:
            sales_query = sales_query.filter(Sale.created_at < datetime.combine(end_date, time.min) + timedelta(days=1))
        if selected_user.isdigit():
            sales_query = sales_query.filter(Sale.user_id == int(selected_user))
        if selected_payment:
            sales_query = sales_query.join(Payment).filter(Payment.method == selected_payment).distinct()
        sales = sales_query.order_by(Sale.created_at.desc(), Sale.id.desc()).all()

        if search:
            needle = search.casefold()
            sales = [sale for sale in sales if needle in " ".join([
                str(sale.id), sale.status, sale.user.full_name, sale.user.username,
                sale.customer.name if sale.customer else "sin cliente",
                " ".join(f"{item.product.code} {item.product.name}" for item in sale.items),
                " ".join(payment.method for payment in sale.payments),
            ]).casefold()]

        confirmed_sales = [sale for sale in sales if sale.status == "CONFIRMADA"]
        total = sum((Decimal(sale.total) for sale in confirmed_sales), Decimal(0))
        payment_totals = {}
        for sale in confirmed_sales:
            for payment in sale.payments:
                if selected_payment and payment.method != selected_payment:
                    continue
                payment_totals[payment.method] = payment_totals.get(payment.method, Decimal(0)) + Decimal(payment.amount)

        activities = []
        losses = []
        activity_focus = owner_view and bool(selected_action or selected_group)
        if owner_view:
            activity_query = AuditEvent.query
            if start_date:
                activity_query = activity_query.filter(AuditEvent.created_at >= datetime.combine(start_date, time.min))
            if end_date:
                activity_query = activity_query.filter(AuditEvent.created_at < datetime.combine(end_date, time.min) + timedelta(days=1))
            if selected_user.isdigit():
                activity_query = activity_query.filter(AuditEvent.user_id == int(selected_user))
            if selected_action:
                activity_query = activity_query.filter(AuditEvent.action == selected_action)
            group_patterns = {
                "ALTAS": ("CREAR_%",),
                "MODIFICACIONES": ("EDITAR_%", "CAMBIAR_%", "AJUSTAR_%", "REPOSICION"),
                "BAJAS": ("ELIMINAR_%", "QUITAR_%", "ANULAR_%", "REGISTRAR_MERMA"),
                "VENTAS": ("CONFIRMAR_VENTA", "ANULAR_VENTA"),
                "SESIONES": ("INICIAR_SESION", "CERRAR_SESION"),
            }
            patterns = group_patterns.get(selected_group)
            if patterns:
                activity_query = activity_query.filter(db.or_(*(AuditEvent.action.like(pattern) for pattern in patterns)))
            activities = activity_query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).all()
            if search:
                needle = search.casefold()
                activities = [event for event in activities if needle in " ".join([
                    event.user.full_name, event.user.username, event.action,
                    ACTIVITY_LABELS.get(event.action, event.action), event.entity,
                    event.entity_id, event.details,
                ]).casefold()]

            if selected_action == "REGISTRAR_MERMA":
                loss_query = InventoryMovement.query.filter_by(movement_type="MERMA")
                if start_date:
                    loss_query = loss_query.filter(InventoryMovement.created_at >= datetime.combine(start_date, time.min))
                if end_date:
                    loss_query = loss_query.filter(InventoryMovement.created_at < datetime.combine(end_date, time.min) + timedelta(days=1))
                if selected_user.isdigit():
                    loss_query = loss_query.filter(InventoryMovement.user_id == int(selected_user))
                losses = loss_query.order_by(InventoryMovement.created_at.desc(), InventoryMovement.id.desc()).all()
                if search:
                    needle = search.casefold()
                    losses = [movement for movement in losses if needle in " ".join([
                        movement.product.name, movement.product.code, movement.user.full_name,
                        movement.user.username, movement.reason,
                    ]).casefold()]

        accounts = User.query.order_by(User.last_name, User.first_name, User.username).all() if owner_view else []
        actions = [row[0] for row in db.session.query(AuditEvent.action).distinct().order_by(AuditEvent.action).all()] if owner_view else []
        stored_methods = [row[0] for row in db.session.query(Payment.method).distinct().order_by(Payment.method).all()]
        payment_methods = sorted(set(stored_methods) | {"EFECTIVO", "DEBITO", "CREDITO", "TRANSFERENCIA", "QR"})
        return render_template(
            "report_sales.html",
            total=total,
            count=len(confirmed_sales),
            by_payment=sorted(payment_totals.items()),
            sales=sales,
            daily_summaries=build_daily_sales_summaries(confirmed_sales),
            today=local_now().strftime("%Y-%m-%d"),
            owner_view=owner_view, accounts=accounts, activities=activities,
            activity_focus=activity_focus, losses=losses,
            activity_labels=ACTIVITY_LABELS, actions=actions, payment_methods=payment_methods,
            start_value=start_value, end_value=end_value, selected_user=selected_user,
            selected_payment=selected_payment, selected_status=selected_status,
            selected_action=selected_action, selected_group=selected_group, search=search,
        )

    @app.route("/reports/sales/daily/<report_date>")
    @login_required
    def daily_sales_report(report_date):
        try:
            selected_date = date.fromisoformat(report_date)
        except ValueError:
            abort(404)
        start = datetime.combine(selected_date, time.min)
        end = start + timedelta(days=1)
        sales = Sale.query.filter(
            Sale.status == "CONFIRMADA",
            Sale.created_at >= start,
            Sale.created_at < end,
        ).order_by(Sale.created_at).all()
        summaries = build_daily_sales_summaries(sales)
        day = summaries[0] if summaries else {
            "date": selected_date,
            "sales": [],
            "sale_count": 0,
            "total": Decimal(0),
            "products": [],
            "payments": [],
        }
        return render_template("daily_close.html", day=day, generated_at=local_now())

    @app.route("/reports/sales/range")
    @login_required
    def sales_range_report():
        try:
            start_date = date.fromisoformat(request.args["start_date"])
            end_date = date.fromisoformat(request.args["end_date"])
            if start_date > end_date:
                raise ValueError("La fecha desde no puede ser posterior a la fecha hasta.")
        except KeyError:
            flash("Seleccione la fecha desde y la fecha hasta.", "error")
            return redirect(url_for("sales_report"))
        except ValueError as exc:
            message = str(exc) if "posterior" in str(exc) else "Las fechas seleccionadas no son válidas."
            flash(message, "error")
            return redirect(url_for("sales_report"))

        start = datetime.combine(start_date, time.min)
        end = datetime.combine(end_date, time.min) + timedelta(days=1)
        sales = Sale.query.filter(
            Sale.status == "CONFIRMADA",
            Sale.created_at >= start,
            Sale.created_at < end,
        ).order_by(Sale.created_at.desc()).all()
        daily_summaries = build_daily_sales_summaries(sales)
        total = sum((Decimal(sale.total) for sale in sales), Decimal(0))
        payments = {}
        for sale in sales:
            for payment in sale.payments:
                payments[payment.method] = payments.get(payment.method, Decimal(0)) + Decimal(payment.amount)
        return render_template(
            "range_sales_report.html",
            start_date=start_date,
            end_date=end_date,
            sales=sales,
            total=total,
            payments=sorted(payments.items()),
            daily_summaries=daily_summaries,
            generated_at=local_now(),
        )

    with app.app_context():
        db.create_all(); ensure_schema(); seed_data()
    return app


def build_daily_sales_summaries(sales):
    """Agrupa ventas confirmadas por fecha, producto y medio de pago."""
    days = {}
    for sale in sales:
        sale_date = sale.created_at.date()
        day = days.setdefault(sale_date, {
            "date": sale_date,
            "sales": [],
            "sale_count": 0,
            "total": Decimal(0),
            "products_map": {},
            "payments_map": {},
        })
        day["sales"].append(sale)
        day["sale_count"] += 1
        day["total"] += Decimal(sale.total)
        for item in sale.items:
            presentation = item.presentation or "UNIDAD"
            key = (item.product_id, presentation)
            product = day["products_map"].setdefault(key, {
                "name": item.product.name,
                "code": item.product.code,
                "presentation": presentation,
                "quantity": Decimal(0),
                "amount": Decimal(0),
            })
            product["quantity"] += Decimal(item.sale_quantity if item.sale_quantity is not None else item.quantity)
            product["amount"] += Decimal(item.subtotal)
        for payment in sale.payments:
            current_amount = day["payments_map"].get(payment.method, Decimal(0))
            day["payments_map"][payment.method] = current_amount + Decimal(payment.amount)

    result = []
    for day in days.values():
        day["products"] = sorted(day.pop("products_map").values(), key=lambda product: (product["name"].lower(), product["presentation"]))
        day["payments"] = sorted(day.pop("payments_map").items())
        result.append(day)
    return sorted(result, key=lambda day: day["date"], reverse=True)
