"""Verifica ventas, stock, clientes, proveedores y reportes con una base de prueba."""

from datetime import datetime
from io import BytesIO

from app import AuditEvent, Customer, Payment, Product, Sale, SaleItem, User, create_app, db


def app_client(extra_config=None):
    config = {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test"}
    if extra_config: config.update(extra_config)
    app = create_app(config)
    with app.app_context():
        db.drop_all(); db.create_all()
        owner = User(username="owner", role="owner"); owner.set_password("secret")
        employee = User(username="employee", role="employee"); employee.set_password("secret")
        product = Product(code="MART-1", name="Martillo", sale_price=1000, box_price=1000, unit_price=100, purchase_price=500, stock=5, closed_boxes=5, loose_units=0, units_per_box=1, has_box_presentation=True, minimum_stock=1)
        db.session.add_all([owner, employee, product]); db.session.commit()
    return app.test_client(), app


def login(client, username="employee"):
    return client.post("/login", data={"username": username, "password": "secret"}, follow_redirects=True)


def test_owner_creates_a_personal_employee_account_that_can_log_in():
    client, app = app_client(); login(client, "owner")
    response = client.post("/employees", data={
        "first_name": "Lucía", "last_name": "Gómez", "dni": "30111222",
        "phone": "11 5555 9876", "email": "lucia@example.com",
        "address": "Av. Siempre Viva 123", "username": "lucia.gomez",
        "password": "ClaveSegura123", "role": "employee",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Cuenta creada correctamente." in response.get_data(as_text=True)
    with app.app_context():
        user = User.query.filter_by(username="lucia.gomez").one()
        assert (user.first_name, user.last_name, user.dni, user.phone) == ("Lucía", "Gómez", "30111222", "11 5555 9876")
        assert (user.email, user.address, user.role) == ("lucia@example.com", "Av. Siempre Viva 123", "employee")
        assert user.password_hash != "ClaveSegura123" and user.verify_password("ClaveSegura123")
        assert AuditEvent.query.filter_by(action="CREAR_CUENTA", entity_id=str(user.id)).one().user.username == "owner"
    client.get("/logout")
    response = client.post("/login", data={"username":"lucia.gomez", "password":"ClaveSegura123"}, follow_redirects=True)
    assert response.status_code == 200 and "Nueva venta" in response.get_data(as_text=True)


def test_employee_cannot_manage_accounts_or_view_account_activity():
    client, _ = app_client(); login(client)
    assert client.get("/employees").status_code == 403
    assert client.post("/employees", data={}).status_code == 403
    assert "Empleados" not in client.get("/").get_data(as_text=True)


def test_login_logout_and_employee_operations_are_recorded_for_the_owner():
    client, app = app_client(); login(client)
    sale = client.post("/sales", json={
        "items":[{"product_id":1, "presentation":"CAJA", "quantity":1}],
        "payments":[{"method":"EFECTIVO", "amount":"1000"}],
    })
    assert sale.status_code == 200
    client.get("/logout")
    with app.app_context():
        employee = User.query.filter_by(username="employee").one()
        actions = [event.action for event in AuditEvent.query.filter_by(user_id=employee.id).order_by(AuditEvent.id)]
        assert actions == ["INICIAR_SESION", "CONFIRMAR_VENTA", "CERRAR_SESION"]
        assert employee.last_login_at is not None and employee.last_logout_at is not None
    login(client, "owner")
    response = client.get("/employees?user_id=2")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Inicio de sesión" in html and "Venta realizada" in html and "Cierre de sesión" in html
    assert "employee" in html and "Empleado" in html


def test_account_form_preserves_data_and_reports_duplicates_before_creating():
    client, app = app_client(); login(client, "owner")
    with app.app_context():
        employee = User.query.filter_by(username="employee").one()
        employee.dni = "12345678"; employee.email = "empleado@example.com"; db.session.commit()
    response = client.post("/employees", data={
        "first_name":"Ana", "last_name":"Pérez", "dni":"12345678", "phone":"123",
        "email":"empleado@example.com", "address":"Calle 1", "username":"employee",
        "password":"123", "role":"invalid",
    })
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'value="Ana"' in html and 'value="Pérez"' in html and 'value="Calle 1"' in html
    assert "Ya existe una cuenta con este DNI." in html
    assert "Ya existe una cuenta con este correo." in html
    assert "Este usuario ya existe." in html
    assert "El teléfono debe contener entre 8 y 15 números" in html
    assert "La contraseña debe tener entre 8 y 128 caracteres." in html
    assert "Seleccioná un tipo de cuenta válido." in html


def test_owner_deletes_employee_without_erasing_sales_or_activity():
    client, app = app_client(); login(client, "employee")
    response = client.post("/sales", json={
        "items":[{"product_id":1, "presentation":"CAJA", "quantity":1}],
        "payments":[{"method":"EFECTIVO", "amount":"1000"}],
    })
    assert response.status_code == 200
    client.get("/logout"); login(client, "owner")
    response = client.post("/employees/2/delete", follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Cuenta eliminada. Sus ventas y registros de actividad se conservaron." in html
    assert "Historial conservado" in html
    with app.app_context():
        employee = db.session.get(User, 2)
        assert employee is not None and not employee.active
        assert Sale.query.filter_by(user_id=employee.id).count() == 1
        assert AuditEvent.query.filter_by(user_id=employee.id, action="CONFIRMAR_VENTA").count() == 1
        removal = AuditEvent.query.filter_by(action="ELIMINAR_CUENTA", entity_id="2").one()
        assert removal.user_id == 1
    client.get("/logout")
    response = client.post("/login", data={"username":"employee", "password":"secret"}, follow_redirects=True)
    assert "Usuario o contraseña incorrectos." in response.get_data(as_text=True)


def test_account_deletion_is_owner_only_and_protects_current_account():
    client, app = app_client(); login(client, "employee")
    assert client.post("/employees/1/delete").status_code == 403
    client.get("/logout"); login(client, "owner")
    response = client.post("/employees/1/delete", follow_redirects=True)
    assert "No podés eliminar la cuenta con la que estás conectado." in response.get_data(as_text=True)
    assert client.get("/employees/99999/delete").status_code == 405
    assert client.post("/employees/99999/delete").status_code == 404
    with app.app_context():
        assert db.session.get(User, 1).active


def test_account_form_validates_personal_data_and_password_complexity():
    client, app = app_client(); login(client, "owner")
    base = {
        "first_name":"A", "last_name":"B", "dni":"12AB", "phone":"123-45",
        "email":"correo-invalido", "address":"123", "username":"x",
        "password":"solominusculas1", "role":"employee",
    }
    response = client.post("/employees", data=base)
    html = response.get_data(as_text=True)
    for message in [
        "El nombre debe tener entre 2 y 100 caracteres.",
        "El apellido debe tener entre 2 y 100 caracteres.",
        "El DNI debe contener entre 7 y 9 números.",
        "El teléfono debe contener entre 8 y 15 números",
        "Ingresá un correo electrónico válido.",
        "La dirección debe tener entre 5 y 250 caracteres.",
        "Usá entre 3 y 60 letras",
        "La contraseña debe incluir al menos una mayúscula, una minúscula y un número.",
    ]:
        assert message in html
    with app.app_context():
        assert User.query.count() == 2

    for password in ["SINMINUSCULAS1", "sinmayusculas1", "SinNumero"]:
        payload = {
            "first_name":"Juan", "last_name":"Pérez", "dni":"33.333.333",
            "phone":"+54 (11) 5555-1234", "email":"juan@example.com",
            "address":"Calle 123", "username":"juan.perez", "password":password,
            "role":"employee",
        }
        response = client.post("/employees", data=payload)
        assert "La contraseña debe incluir al menos una mayúscula, una minúscula y un número." in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 2


def test_sale_updates_stock():
    client, app = app_client(); login(client)
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"CAJA","quantity":2}],"payments":[{"method":"EFECTIVO","amount":"2000"}]})
    assert response.status_code == 200 and response.json["ok"]
    with app.app_context(): assert db.session.get(Product, 1).stock == 3


def test_sale_rejects_insufficient_stock():
    client, _ = app_client(); login(client)
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"CAJA","quantity":8}],"payments":[{"method":"EFECTIVO","amount":"8000"}]})
    assert response.status_code == 400


def test_employee_cannot_edit_products():
    client, _ = app_client(); login(client)
    assert client.get("/products/1/edit").status_code == 403


def test_owner_records_box_and_unit_losses_with_account_date_and_reason():
    from app import InventoryMovement
    client, app = app_client(); login(client, "owner")
    with app.app_context():
        product = db.session.get(Product, 1)
        product.units_per_box = 10; product.closed_boxes = 5; product.loose_units = 0
        product.has_box_presentation = True; product.sync_stock(); db.session.commit()

    response = client.post("/products/1/loss", data={
        "presentation":"CAJA", "quantity":"2", "loss_type":"ROTURA",
        "reason_detail":"Las cajas se mojaron durante la descarga.",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert "Merma registrada y stock actualizado correctamente." in response.get_data(as_text=True)
    with app.app_context():
        product = db.session.get(Product, 1)
        assert (product.closed_boxes, product.loose_units, product.available_units) == (3, 0, 30)
        movement = InventoryMovement.query.filter_by(movement_type="MERMA").one()
        assert movement.quantity == -20 and movement.user_id == 1
        assert movement.created_at is not None and "Rotura" in movement.reason and "mojaron" in movement.reason
        event = AuditEvent.query.filter_by(action="REGISTRAR_MERMA").one()
        assert event.user_id == 1 and event.created_at is not None
        assert "-2 caja(s)" in event.details and "stock 50->30" in event.details

    response = client.post("/products/1/loss", data={
        "presentation":"UNIDAD", "quantity":"12", "loss_type":"DESPERFECTO",
        "reason_detail":"Las unidades tienen el mango quebrado.",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        product = db.session.get(Product, 1)
        assert (product.closed_boxes, product.loose_units, product.available_units) == (1, 8, 18)
        movements = InventoryMovement.query.filter_by(movement_type="MERMA").order_by(InventoryMovement.id).all()
        assert len(movements) == 2 and movements[1].quantity == -12
        assert AuditEvent.query.filter_by(action="REGISTRAR_MERMA").count() == 2
    report = client.get("/reports/sales?status=&activity_group=BAJAS").get_data(as_text=True)
    assert "Merma / producto roto" in report and "mango quebrado" in report
    report = client.get("/reports/sales?action=REGISTRAR_MERMA").get_data(as_text=True)
    assert "Detalle de mermas" in report and "Cantidad descontada" in report
    assert "Martillo" in report and "Código MART-1" in report and "owner" in report
    assert "Rotura" in report and "cajas se mojaron" in report
    assert "Desperfecto" in report and "mango quebrado" in report
    assert "20 unidades" in report and "12 unidades" in report
    assert "20.000 unidades" not in report and "12.000 unidades" not in report
    assert "Dinero ingresado por medio de pago" not in report


def test_product_loss_rejects_invalid_or_excessive_quantity_for_employee():
    from app import InventoryMovement
    client, app = app_client(); login(client, "employee")
    assert client.get("/products/1/loss").status_code == 200
    assert client.post("/products/1/loss", data={}).status_code == 200
    for payload, message in [
        ({"presentation":"CAJA", "quantity":"99", "loss_type":"ROTURA", "reason_detail":"Cantidad superior al stock."}, "No hay suficientes cajas cerradas"),
        ({"presentation":"UNIDAD", "quantity":"1", "loss_type":"ROTURA", "reason_detail":"mal"}, "entre 5 y 300 caracteres"),
        ({"presentation":"UNIDAD", "quantity":"0", "loss_type":"DESPERFECTO", "reason_detail":"Cantidad inválida."}, "debe ser mayor a cero"),
    ]:
        response = client.post("/products/1/loss", data=payload)
        assert response.status_code == 200 and message in response.get_data(as_text=True)
    with app.app_context():
        product = db.session.get(Product, 1)
        assert product.available_units == 5
        assert InventoryMovement.query.filter_by(movement_type="MERMA").count() == 0
        assert AuditEvent.query.filter_by(action="REGISTRAR_MERMA").count() == 0


def test_unit_sale_opens_a_box_and_keeps_loose_units():
    client, app = app_client(); login(client)
    with app.app_context():
        product = db.session.get(Product, 1)
        product.units_per_box = 10; product.closed_boxes = 5; product.loose_units = 0; product.box_price = 1000; product.unit_price = 120; product.sync_stock(); db.session.commit()
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"UNIDAD","quantity":3}],"payments":[{"method":"EFECTIVO","amount":"360"}]})
    assert response.status_code == 200
    with app.app_context():
        product = db.session.get(Product, 1)
        assert (product.closed_boxes, product.loose_units, product.available_units) == (4, 7, 47)


def test_customer_discount_is_calculated_on_the_server():
    client, app = app_client(); login(client)
    with app.app_context():
        product = db.session.get(Product, 1)
        product.units_per_box = 10; product.closed_boxes = 1; product.unit_price = 120; product.sync_stock()
        customer = Customer(name="Cliente descuento", discount_percent=10)
        db.session.add(customer); db.session.commit()
    response = client.post("/sales", json={"customer_id": 1, "items":[{"product_id":1,"presentation":"UNIDAD","quantity":2}],"payments":[{"method":"DEBITO","amount":"216"}]})
    assert response.status_code == 200
    with app.app_context(): assert db.session.get(Sale, 1).total == 216


def test_unit_only_product_can_be_sold_without_box_presentation():
    client, app = app_client(); login(client)
    with app.app_context():
        product = db.session.get(Product, 1)
        product.has_box_presentation = False; product.closed_boxes = 0; product.loose_units = 5; product.units_per_box = 1; product.unit_price = 250; product.sync_stock()
        db.session.commit()
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"UNIDAD","quantity":2}],"payments":[{"method":"EFECTIVO","amount":"500"}]})
    assert response.status_code == 200 and response.json["ok"]
    with app.app_context(): assert db.session.get(Product, 1).loose_units == 3


def test_owner_can_create_a_unit_only_product_without_box_price():
    client, app = app_client(); login(client, "owner")
    response = client.post("/products/new", data={"code":"TOR-1", "name":"Tornillo", "unit_price":"50", "loose_units":"20"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        product = Product.query.filter_by(code="TOR-1").one()
        assert not product.has_box_presentation and product.sale_price == 50


def test_minimum_stock_in_boxes_controls_catalog_and_dashboard_after_stock_changes():
    client, app = app_client(); login(client, 'owner')
    response = client.post('/products/new', data={
        'code':'MIN-BOX', 'name':'Producto minimo cajas', 'has_box_presentation':'on',
        'units_per_box':'100', 'closed_boxes':'2', 'loose_units':'99',
        'unit_price':'10', 'box_price':'1000', 'minimum_stock':'2',
    }, follow_redirects=True)
    assert b'Producto creado.' in response.data
    with app.app_context():
        product = Product.query.filter_by(code='MIN-BOX').one()
        product_id = product.id
        assert product.minimum_stock == 2 and product.available_units == 299
        assert product.is_stock_critical
    assert 'Stock mínimo (cajas)' in client.get(f'/products/{product_id}/edit').get_data(as_text=True)
    assert '<tr class="critical">' in client.get('/products?q=Producto+minimo+cajas').get_data(as_text=True)
    assert 'Stock crítico</span><strong>1</strong>' in client.get('/').get_data(as_text=True)
    response = client.post(f'/products/{product_id}/restock', data={
        'add_boxes':'1', 'purchase_price':'100', 'unit_price':'10', 'box_price':'1000',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert '<tr class="critical">' not in client.get('/products?q=Producto+minimo+cajas').get_data(as_text=True)
    assert 'Stock crítico</span><strong>0</strong>' in client.get('/').get_data(as_text=True)
    response = client.post('/sales', json={
        'items':[{'product_id':product_id, 'presentation':'CAJA', 'quantity':1}],
        'payments':[{'method':'EFECTIVO', 'amount':'1000'}],
    })
    assert response.status_code == 200
    assert 'Stock crítico</span><strong>1</strong>' in client.get('/').get_data(as_text=True)


def test_minimum_stock_uses_units_when_box_presentation_is_disabled():
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        product = db.session.get(Product, 1)
        product.units_per_box = 10; product.closed_boxes = 2; product.loose_units = 5
        product.minimum_stock = 3; product.sync_stock(); db.session.commit()
        assert product.is_stock_critical
    response = client.post('/products/1/edit', data={
        'code':'MART-1', 'name':'Martillo', 'unit_price':'100',
        'units_per_box':'10', 'minimum_stock':'3', 'active':'on',
    }, follow_redirects=True)
    assert b'Producto actualizado.' in response.data
    with app.app_context():
        product = db.session.get(Product, 1)
        assert product.minimum_stock == 3 and product.available_units == 25
        assert not product.has_box_presentation and not product.is_stock_critical
        product.loose_units = 3; product.sync_stock(); db.session.commit()
        assert product.is_stock_critical
    assert 'Stock mínimo (unidades)' in client.get('/products/1/edit').get_data(as_text=True)
    assert 'Stock crítico</span><strong>1</strong>' in client.get('/').get_data(as_text=True)
    with app.app_context():
        product = db.session.get(Product, 1)
        product.active = False; db.session.commit()
        assert not product.is_stock_critical
    assert 'Stock crítico</span><strong>0</strong>' in client.get('/').get_data(as_text=True)


def test_minimum_stock_validates_whole_nonnegative_counts_and_preserves_box_selection():
    client, app = app_client(); login(client, 'owner')
    payload = {'code':'MIN-INVALID', 'name':'Prueba minimo', 'unit_price':'10',
               'box_price':'100', 'has_box_presentation':'on', 'units_per_box':'10', 'closed_boxes':'1'}
    for value in ['-1', '1.5', 'NaN', 'Infinity']:
        response = client.post('/products/new', data=dict(payload, minimum_stock=value))
        html = response.get_data(as_text=True)
        assert 'cantidad entera de cajas' in html and 'Stock mínimo (cajas)' in html
    with app.app_context():
        assert Product.query.filter_by(code='MIN-INVALID').first() is None
    response = client.post('/products/new', data=dict(payload, minimum_stock=''), follow_redirects=True)
    assert b'Producto creado.' in response.data
    with app.app_context():
        assert Product.query.filter_by(code='MIN-INVALID').one().minimum_stock == 0


def test_owner_deletes_product_from_catalog_without_erasing_history():
    client, app = app_client(); login(client, "owner")
    response = client.post("/products/1/delete", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context(): assert not db.session.get(Product, 1).active
    response = client.get("/products")
    assert b"Martillo" not in response.data


def test_sale_form_marks_unit_only_products_as_not_sold_by_box():
    client, app = app_client(); login(client)
    with app.app_context():
        product = db.session.get(Product, 1)
        product.has_box_presentation = False; db.session.commit()
    response = client.get("/sales/new")
    assert b'data-has-box="0"' in response.data


def test_employee_customer_discount_remains_pending_until_owner_approves():
    client, app = app_client(); login(client)
    response = client.post("/customers", data={"first_name":"Ana", "last_name":"Perez", "dni":"30111222", "phone":"11 5555 1234", "discount_percent":"12.5"}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        customer = Customer.query.filter_by(dni="30111222").one()
        assert (customer.name, customer.discount_percent, customer.pending_discount_percent) == ("Perez, Ana", 0, 12.5)


def test_duplicate_product_keeps_values_and_marks_code():
    client, _ = app_client(); login(client, "owner")
    response = client.post("/products/new", data={"code":"MART-1", "name":"Otro martillo", "unit_price":"100", "purchase_price":"55", "minimum_stock":"8"})
    assert response.status_code == 200
    assert b'Ya existe un producto con este c' in response.data
    assert b'value="Otro martillo"' in response.data
    assert b'value="55"' in response.data and b'value="8"' in response.data


def test_product_names_can_repeat_only_with_different_optional_classifications():
    from app import Category, Subcategory
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        categories = [Category(name='Phillips'), Category(name='Plano')]
        subcategories = [Subcategory(name='5mm'), Subcategory(name='8mm')]
        db.session.add_all(categories + subcategories); db.session.commit()
        category_ids = ['', *(entry.id for entry in categories)]
        subcategory_ids = ['', *(entry.id for entry in subcategories)]
    for index, (category_id, subcategory_id) in enumerate((c, s) for c in category_ids for s in subcategory_ids):
        payload = {'code':f'TOR-{index}', 'name':'Tornillos', 'category_id':category_id,
                   'subcategory_id':subcategory_id, 'unit_price':'100', 'minimum_stock':'0'}
        response = client.post('/products/new', data=payload, follow_redirects=True)
        assert b'Producto creado.' in response.data
        with app.app_context():
            product = Product.query.filter_by(code=payload['code']).one()
            assert (product.category_id, product.subcategory_id) == (category_id or None, subcategory_id or None)
        duplicate = dict(payload, code=f'DUP-{index}', name='  TORNILLOS  ', unit_price='250')
        response = client.post('/products/new', data=duplicate)
        html = response.get_data(as_text=True)
        assert 'Ya existe un producto con este nombre y la misma combinación' in html
        assert f'value="DUP-{index}"' in html and 'value="250"' in html
        if category_id:
            assert f'<option value="{category_id}" selected>' in html
        if subcategory_id:
            assert f'<option value="{subcategory_id}" selected>' in html
    with app.app_context():
        assert Product.query.filter_by(name='Tornillos').count() == 9
        assert Product.query.filter(Product.code.like('DUP-%')).count() == 0


def test_edit_rejects_duplicate_combination_keeps_form_and_can_save_another_variant():
    from app import Category
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        category = Category(name='Phillips')
        db.session.add(category); db.session.flush()
        db.session.add(Product(code='TOR-1', name='Tornillos', category_id=category.id, unit_price=50, sale_price=50))
        db.session.commit()
        category_id = category.id
    payload = {'code':'MART-1', 'name':'Tornillos', 'category_id':category_id,
               'subcategory_id':'', 'unit_price':'250', 'purchase_price':'35',
               'minimum_stock':'2', 'description':'Conservar este texto', 'active':'on'}
    response = client.post('/products/1/edit', data=payload)
    html = response.get_data(as_text=True)
    assert 'Ya existe un producto con este nombre y la misma combinación' in html
    assert 'value="Tornillos"' in html and 'value="250"' in html and 'Conservar este texto' in html
    assert f'<option value="{category_id}" selected>Phillips</option>' in html
    with app.app_context():
        original = db.session.get(Product, 1)
        assert original.name == 'Martillo' and original.category_id is None
        assert original.unit_price == 100 and original.available_units == 5
    payload['category_id'] = ''
    for _ in range(2):
        response = client.post('/products/1/edit', data=payload, follow_redirects=True)
        assert b'Producto actualizado.' in response.data
    with app.app_context():
        product = db.session.get(Product, 1)
        assert product.name == 'Tornillos' and product.category_id is None and product.subcategory_id is None


def test_product_name_suggestions_are_unique_and_available_on_new_and_edit():
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        for index, name in enumerate(['Tornillos', 'tornillos', 'Bulón', 'BULO\u0301N', 'Tuerca <M8>']):
            db.session.add(Product(code=f'NAME-{index}', name=name, unit_price=10, sale_price=10))
        db.session.commit()
    for path in ['/products/new', '/products/1/edit']:
        html = client.get(path).get_data(as_text=True)
        assert 'aria-controls="product-name-options"' in html
        suggestions = html.split('aria-label="Nombres existentes" hidden>')[1].split('</ul>')[0]
        assert suggestions.count('role="option"') == 4
        assert '>Tornillos</li>' in suggestions and '>tornillos</li>' not in suggestions
        assert 'Tuerca &lt;M8&gt;' in suggestions
        assert 'Escribí para buscar un nombre existente o ingresá uno nuevo.' in html


def test_product_combination_treats_omitted_selections_as_none_and_normalizes_names():
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        db.session.add(Product(code='BUL-1', name='Bulón  largo', unit_price=10, sale_price=10, active=False))
        db.session.commit()
    response = client.post('/products/new', data={'code':'BUL-2', 'name':' BULO\u0301N largo ', 'unit_price':'10'})
    html = response.get_data(as_text=True)
    assert 'Ya existe un producto con este nombre y la misma combinación' in html
    assert 'El producto existente está inactivo.' in html
    with app.app_context():
        assert Product.query.filter_by(code='BUL-2').first() is None


def test_product_rejects_invalid_classification_instead_of_treating_it_as_none():
    client, app = app_client(); login(client, 'owner')
    for field in ['category_id', 'subcategory_id']:
        for value in ['invalid', '99999']:
            response = client.post('/products/new', data={'code':'INVALID', 'name':'Producto', 'unit_price':'10', field:value})
            assert 'válida.' in response.get_data(as_text=True)
    with app.app_context():
        assert Product.query.filter_by(code='INVALID').first() is None


def test_restock_increases_stock_and_updates_prices():
    client, app = app_client(); login(client, "owner")
    response = client.post("/products/1/restock", data={"add_units":"3", "purchase_price":"600", "unit_price":"120", "box_price":"1200"})
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Product, 1)
        assert (product.loose_units, product.purchase_price, product.unit_price) == (3, 600, 120)


def test_supplier_detail_lists_associated_products():
    client, app = app_client(); login(client, "owner")
    with app.app_context():
        from app import Supplier
        supplier = Supplier(name="Proveedor prueba", company_name="Empresa SA")
        db.session.add(supplier); db.session.flush()
        db.session.get(Product, 1).supplier_id = supplier.id
        db.session.commit()
    response = client.get("/suppliers/1")
    assert response.status_code == 200
    assert b"Proveedor prueba" in response.data and b"Martillo" in response.data


def test_owner_can_remove_product_from_supplier_without_deleting_product():
    client, app = app_client(); login(client, "owner")
    with app.app_context():
        from app import Supplier
        supplier = Supplier(name="Proveedor removible")
        db.session.add(supplier); db.session.flush()
        db.session.get(Product, 1).supplier_id = supplier.id
        supplier_id = supplier.id; db.session.commit()
    response = client.post(f"/suppliers/{supplier_id}/products/1/remove")
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Product, 1)
        assert product.supplier_id is None and product.active


def test_mixed_payment_is_rejected_when_total_is_insufficient():
    client, app = app_client(); login(client)
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"UNIDAD","quantity":1}],"payments":[{"method":"TRANSFERENCIA","amount":"40"},{"method":"QR","amount":"50"}]})
    assert response.status_code == 400
    assert "Faltan" in response.json["error"]
    with app.app_context(): assert Sale.query.count() == 0


def test_cash_payment_calculates_change_and_records_sale():
    client, app = app_client(); login(client)
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"UNIDAD","quantity":1}],"payments":[{"method":"EFECTIVO","amount":"150"}]})
    assert response.status_code == 200 and response.json["change_due"] == "50.00"
    with app.app_context():
        sale = Sale.query.one()
        assert sale.change_due == 50 and sale.cash_received == 150


def test_transfer_and_qr_are_valid_payment_methods():
    client, _ = app_client(); login(client)
    response = client.post("/sales", json={"items":[{"product_id":1,"presentation":"UNIDAD","quantity":1}],"payments":[{"method":"TRANSFERENCIA","amount":"40"},{"method":"QR","amount":"60"}]})
    assert response.status_code == 200


def test_browser_cart_payload_with_id_records_sale_and_change():
    client, app = app_client(); login(client)
    response = client.post("/sales", json={"items":[{"id":"1","presentation":"UNIDAD","qty":1,"quantity":1}],"payments":[{"method":"EFECTIVO","amount":"1000"}]})
    assert response.status_code == 200
    assert response.json["change_due"] == "900.00"
    with app.app_context(): assert Sale.query.count() == 1


def test_sales_summary_is_grouped_by_day_and_product():
    client, app = app_client(); login(client)
    with app.app_context():
        sale_one = Sale(user_id=2, total=200, status="CONFIRMADA", created_at=datetime(2026, 9, 19, 10, 0))  # noqa: DTZ001 -- La BD guarda fechas locales sin zona horaria.
        sale_two = Sale(user_id=2, total=100, status="CONFIRMADA", created_at=datetime(2026, 9, 20, 11, 0))  # noqa: DTZ001 -- La BD guarda fechas locales sin zona horaria.
        db.session.add_all([sale_one, sale_two]); db.session.flush()
        db.session.add_all([
            SaleItem(sale_id=sale_one.id, product_id=1, quantity=2, sale_quantity=2, presentation="UNIDAD", unit_price=100, subtotal=200),
            SaleItem(sale_id=sale_two.id, product_id=1, quantity=1, sale_quantity=1, presentation="UNIDAD", unit_price=100, subtotal=100),
            Payment(sale_id=sale_one.id, method="EFECTIVO", amount=200),
            Payment(sale_id=sale_two.id, method="QR", amount=100),
        ])
        db.session.commit()
    response = client.get("/reports/sales")
    assert response.status_code == 200
    assert b"20/09/2026" in response.data and b"19/09/2026" in response.data
    assert b"Cantidad vendida" in response.data and b"Martillo" in response.data


def test_daily_close_contains_products_payments_and_total():
    client, app = app_client(); login(client)
    with app.app_context():
        sale = Sale(user_id=2, total=200, status="CONFIRMADA", created_at=datetime(2026, 9, 20, 17, 30))  # noqa: DTZ001 -- La BD guarda fechas locales sin zona horaria.
        db.session.add(sale); db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id, product_id=1, quantity=2, sale_quantity=2, presentation="UNIDAD", unit_price=100, subtotal=200))
        db.session.add(Payment(sale_id=sale.id, method="TRANSFERENCIA", amount=200))
        db.session.commit()
    response = client.get("/reports/sales/daily/2026-09-20")
    assert response.status_code == 200
    assert b"CIERRE DIARIO" in response.data
    assert b"Martillo" in response.data and b"Transferencia" in response.data
    assert b"Imprimir cierre diario" in response.data


def test_sales_report_has_date_range_selector():
    client, _ = app_client(); login(client)
    response = client.get("/reports/sales")
    assert response.status_code == 200
    assert b'name="start_date"' in response.data
    assert b'name="end_date"' in response.data
    assert b"Imprimir resumen por fecha" in response.data
    assert b"Imprimir cierre de hoy" not in response.data


def test_owner_report_filters_sales_payments_and_activity_by_account_and_period():
    client, app = app_client(); login(client, "owner")
    with app.app_context():
        employee_sale = Sale(user_id=2, total=150, status="CONFIRMADA", created_at=datetime(2026, 9, 20, 10, 0))
        owner_sale = Sale(user_id=1, total=200, status="CONFIRMADA", created_at=datetime(2026, 9, 20, 11, 0))
        outside_sale = Sale(user_id=2, total=300, status="CONFIRMADA", created_at=datetime(2026, 9, 21, 10, 0))
        db.session.add_all([employee_sale, owner_sale, outside_sale]); db.session.flush()
        db.session.add_all([
            SaleItem(sale_id=employee_sale.id, product_id=1, quantity=1, sale_quantity=1, presentation="UNIDAD", unit_price=150, subtotal=150),
            SaleItem(sale_id=owner_sale.id, product_id=1, quantity=2, sale_quantity=2, presentation="UNIDAD", unit_price=100, subtotal=200),
            SaleItem(sale_id=outside_sale.id, product_id=1, quantity=3, sale_quantity=3, presentation="UNIDAD", unit_price=100, subtotal=300),
            Payment(sale_id=employee_sale.id, method="EFECTIVO", amount=100),
            Payment(sale_id=employee_sale.id, method="TRANSFERENCIA", amount=50),
            Payment(sale_id=owner_sale.id, method="CREDITO", amount=200),
            Payment(sale_id=outside_sale.id, method="EFECTIVO", amount=300),
            AuditEvent(user_id=2, action="EDITAR_PRODUCTO", entity="product", entity_id="1", details="cambio realizado por empleado", created_at=datetime(2026, 9, 20, 10, 5)),
            AuditEvent(user_id=1, action="CREAR_PRODUCTO", entity="product", entity_id="2", details="alta realizada por dueño", created_at=datetime(2026, 9, 20, 11, 5)),
            AuditEvent(user_id=2, action="EDITAR_PRODUCTO", entity="product", entity_id="3", details="cambio fuera del período", created_at=datetime(2026, 9, 21, 10, 5)),
        ])
        db.session.commit()
        employee_sale_id, owner_sale_id, outside_sale_id = employee_sale.id, owner_sale.id, outside_sale.id

    response = client.get("/reports/sales?start_date=2026-09-20&end_date=2026-09-20&user_id=2&payment_method=EFECTIVO&status=CONFIRMADA")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Resumen detallado" in html and "Detalle de cambios y actividad por cuenta" in html
    assert f"Ticket N.º {employee_sale_id}" in html
    assert f"Ticket N.º {owner_sale_id}" not in html and f"Ticket N.º {outside_sale_id}" not in html
    assert "Efectivo" in html and "$ 100,00" in html
    assert "Dinero confirmado" in html and "$ 150,00" in html
    assert 'option value="2" selected' in html and 'option value="EFECTIVO" selected' in html

    response = client.get("/reports/sales?start_date=2026-09-20&end_date=2026-09-20&user_id=2&activity_group=MODIFICACIONES")
    html = response.get_data(as_text=True)
    assert "cambio realizado por empleado" in html
    assert "alta realizada por dueño" not in html and "cambio fuera del período" not in html
    assert "Acciones encontradas" in html and "Dinero ingresado por medio de pago" not in html


def test_detailed_account_activity_in_reports_is_visible_only_to_owner():
    client, app = app_client()
    with app.app_context():
        db.session.add(AuditEvent(user_id=2, action="EDITAR_PRODUCTO", entity="product", entity_id="1", details="detalle reservado al dueño"))
        db.session.commit()
    login(client, "employee")
    html = client.get("/reports/sales").get_data(as_text=True)
    assert "Detalle de cambios y actividad por cuenta" not in html
    assert "detalle reservado al dueño" not in html
    assert 'name="user_id"' not in html and 'name="activity_group"' not in html


def test_range_report_only_includes_sales_between_selected_dates():
    client, app = app_client(); login(client)
    with app.app_context():
        included = Sale(user_id=2, total=200, status="CONFIRMADA", created_at=datetime(2026, 9, 15, 12, 0))  # noqa: DTZ001 -- La BD guarda fechas locales sin zona horaria.
        excluded = Sale(user_id=2, total=100, status="CONFIRMADA", created_at=datetime(2026, 9, 20, 12, 0))  # noqa: DTZ001 -- La BD guarda fechas locales sin zona horaria.
        db.session.add_all([included, excluded]); db.session.flush()
        db.session.add_all([
            SaleItem(sale_id=included.id, product_id=1, quantity=2, sale_quantity=2, presentation="UNIDAD", unit_price=100, subtotal=200),
            SaleItem(sale_id=excluded.id, product_id=1, quantity=1, sale_quantity=1, presentation="UNIDAD", unit_price=100, subtotal=100),
            Payment(sale_id=included.id, method="EFECTIVO", amount=200),
            Payment(sale_id=excluded.id, method="QR", amount=100),
        ])
        db.session.commit()
    response = client.get("/reports/sales/range?start_date=2026-09-14&end_date=2026-09-16")
    assert response.status_code == 200
    assert b"14/09/2026" in response.data and b"16/09/2026" in response.data
    assert b"15/09/2026" in response.data
    assert b"Efectivo" in response.data and b">QR<" not in response.data


def test_range_report_rejects_reversed_dates():
    client, _ = app_client(); login(client)
    response = client.get("/reports/sales/range?start_date=2026-09-20&end_date=2026-09-10", follow_redirects=True)
    assert response.status_code == 200
    assert b"fecha desde no puede ser posterior" in response.data


def test_owner_can_add_product_with_image(tmp_path):
    client, app = app_client({"PRODUCT_IMAGE_UPLOAD_FOLDER": str(tmp_path)}); login(client, "owner")
    png = b"\x89PNG\r\n\x1a\n" + b"test-image"
    response = client.post("/products/new", data={
        "code": "IMG-1", "name": "Producto con imagen", "unit_price": "100", "loose_units": "4",
        "image": (BytesIO(png), "producto.png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        product = Product.query.filter_by(code="IMG-1").one()
        assert product.image_filename.endswith(".png")
        assert (tmp_path / product.image_filename).exists()


def test_product_rejects_non_image_upload(tmp_path):
    client, app = app_client({"PRODUCT_IMAGE_UPLOAD_FOLDER": str(tmp_path)}); login(client, "owner")
    response = client.post("/products/new", data={
        "code": "BAD-IMG", "name": "Producto inválido", "unit_price": "100", "loose_units": "4",
        "image": (BytesIO(b"not-an-image"), "archivo.jpg"),
    }, content_type="multipart/form-data")
    assert response.status_code == 200
    assert b"JPG, PNG o WebP" in response.data
    with app.app_context(): assert Product.query.filter_by(code="BAD-IMG").first() is None


def test_single_product_code_with_legacy_database():
    from sqlalchemy import text

    client, app = app_client(); login(client, "owner")
    with app.app_context():
        # An existing database may still contain the retired, nullable column.
        db.session.execute(text("ALTER TABLE product ADD COLUMN barcode VARCHAR(80)"))
        db.session.execute(text("UPDATE product SET barcode = '987654321' WHERE id = 1"))
        db.session.commit()
    for path in ("/products/new", "/products/1/edit"):
        html = client.get(path).get_data(as_text=True)
        assert 'name="code"' in html
        assert 'name="barcode"' not in html
    assert 'Martillo' not in client.get('/products?q=987654321').get_data(as_text=True)
    response = client.post('/products/new', data={
        'code': '00123456789', 'name': 'Producto escaneado', 'unit_price': '100',
        'minimum_stock': '0', 'loose_units': '2',
    })
    assert response.status_code == 302
    html = client.get('/products?q=00123456789').get_data(as_text=True)
    assert 'Producto escaneado' in html and 'Martillo' not in html
    response = client.post('/products/1/edit', data={
        'code': 'MART-1', 'name': 'Martillo actualizado', 'unit_price': '100',
        'minimum_stock': '0', 'active': 'on',
    })
    assert response.status_code == 302
    with app.app_context():
        assert Product.query.filter_by(code='00123456789').one().name == 'Producto escaneado'
        assert db.session.execute(text('SELECT barcode FROM product WHERE id = 1')).scalar() == '987654321'


def test_product_lists_use_spanish_alphabetical_order_and_column_order():
    import re
    from app import Supplier

    client, app = app_client(); login(client, 'owner')
    expected = ['Ábaco', 'alambre', 'Martillo', 'nuez', 'Ñandú', 'Óleo', 'Zinc']
    with app.app_context():
        supplier = Supplier(name='Proveedor de prueba')
        db.session.add(supplier); db.session.flush()
        supplier_id = supplier.id
        db.session.get(Product, 1).supplier_id = supplier_id
        for i, name in enumerate(reversed(expected)):
            if name == 'Martillo': continue
            db.session.add(Product(code=f'ORD-{i}', name=name, sale_price=100,
                                   unit_price=100, supplier_id=supplier_id))
        db.session.commit()
    for path in ('/products', f'/suppliers/{supplier_id}'):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert re.findall(r'<th>(.*?)</th>', html) == [
            'Imagen', 'Nombre del producto', 'Clasificación', 'Proveedor', 'Código', 'Stock',
        ]
        positions = [html.index(f'<strong>{name}</strong>') for name in expected]
        assert positions == sorted(positions)
        assert 'Reposición' in html
    html = client.get('/sales/new').get_data(as_text=True)
    positions = [html.index(f'data-name="{name}"') for name in expected]
    assert positions == sorted(positions)


def test_product_search_partial_results_and_clear():
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        db.session.add(Product(code='00123', name='Tornillo', sale_price=10, unit_price=10))
        db.session.commit()
    for query, expected, excluded in [('mar', 'Martillo', 'Tornillo'), ('00123', 'Tornillo', 'Martillo')]:
        response = client.get('/products', query_string={'q': query, 'partial': '1'})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert expected in html and excluded not in html
        assert '<html' not in html
    html = client.get('/products?partial=1&q=').get_data(as_text=True)
    assert 'Martillo' in html and 'Tornillo' in html
    assert 'No hay productos.' in client.get('/products?partial=1&q=missing').get_data(as_text=True)


def catalog_client():
    from app import Category, Subcategory
    from sqlalchemy import text
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        category = Category(name='Herramientas')
        other = Category(name='Fijaciones')
        db.session.add_all([category, other]); db.session.flush()
        sub = Subcategory(name='Manuales')
        db.session.add(sub); db.session.flush()
        product = db.session.get(Product, 1)
        product.category_id = category.id; product.subcategory_id = sub.id
        inactive = Product(code='INACTIVO', name='Producto inactivo', sale_price=50, unit_price=50,
                           active=False, category_id=category.id, subcategory_id=sub.id)
        db.session.add(inactive); db.session.commit()
        ids = category.id, other.id, sub.id
    return client, app, ids


def test_subcategory_can_be_created_without_any_categories():
    from app import Category, Subcategory
    client, app = app_client(); login(client, 'owner')
    html = client.get('/catalog').get_data(as_text=True)
    assert 'name="action" value="subcategory"' in html
    assert 'name="category_id"' not in html
    response = client.post('/catalog', data={'action':'subcategory', 'name':'Manuales'}, follow_redirects=True)
    assert response.status_code == 200 and 'Catálogo actualizado.' in response.get_data(as_text=True)
    with app.app_context():
        assert Category.query.count() == 0
        sub = Subcategory.query.one()
        assert sub.name == 'Manuales'
        sub_id = sub.id
    response = client.post('/catalog', data={'action':'edit_subcategory', 'id':sub_id, 'name':'Electricas'}, follow_redirects=True)
    assert b'Cat\xc3\xa1logo actualizado.' in response.data
    assert b'name="category_id"' not in response.data
    with app.app_context():
        assert db.session.get(Subcategory, sub_id).name == 'Electricas'


def test_same_subcategory_can_be_used_with_any_product_category():
    client, app, (category_id, other_id, sub_id) = catalog_client()
    for index, selected_category in enumerate([category_id, other_id, '']):
        payload = {'code':f'INDEPENDIENTE-{index}', 'name':f'Producto independiente {index}',
                   'unit_price':'20', 'category_id':selected_category, 'subcategory_id':sub_id}
        response = client.post('/products/new', data=payload, follow_redirects=True)
        assert response.status_code == 200 and b'Producto creado.' in response.data
        with app.app_context():
            product = Product.query.filter_by(code=payload['code']).one()
            assert product.category_id == (selected_category or None)
            assert product.subcategory_id == sub_id
            product_id = product.id
        html = client.get(f'/products/{product_id}/edit').get_data(as_text=True)
        assert f'<option value="{sub_id}" selected>Manuales</option>' in html
    payload.update(category_id=other_id, active='on')
    response = client.post(f'/products/{product_id}/edit', data=payload, follow_redirects=True)
    assert b'Producto actualizado.' in response.data
    with app.app_context():
        assert db.session.get(Product, product_id).category_id == other_id
        assert db.session.get(Product, product_id).subcategory_id == sub_id
        assert db.session.get(Product, 1).category_id == category_id


def test_subcategory_duplicate_names_are_rejected_globally():
    from app import Subcategory
    client, app, (_, other_id, sub_id) = catalog_client()
    response = client.post('/catalog', data={'action':'subcategory', 'name':' manuales ', 'category_id':other_id}, follow_redirects=True)
    assert 'Ya existe una subcategoría con ese nombre.' in response.get_data(as_text=True)
    with app.app_context():
        assert Subcategory.query.count() == 1
        assert db.session.get(Subcategory, sub_id).name == 'Manuales'


def test_sale_form_shows_subcategory_without_a_category():
    client, app, (_, _, sub_id) = catalog_client()
    with app.app_context():
        db.session.get(Product, 1).category_id = None
        db.session.commit()
    html = client.get('/sales/new').get_data(as_text=True)
    assert 'data-name="Martillo · Manuales"' in html
    assert '<small>Manuales</small>' in html


def test_catalog_edit_renames_without_changing_product_assignments():
    from app import Category, Subcategory
    client, app, (category_id, other_id, sub_id) = catalog_client()
    response = client.post('/catalog', data={'action':'edit_category', 'id':category_id, 'name':'Herramientas nuevas'}, follow_redirects=True)
    assert response.status_code == 200 and b'Herramientas nuevas' in response.data
    response = client.post('/catalog', data={'action':'edit_subcategory', 'id':sub_id, 'name':'Tornillos'}, follow_redirects=True)
    assert response.status_code == 200 and b'Tornillos' in response.data
    with app.app_context():
        assert db.session.get(Category, category_id).name == 'Herramientas nuevas'
        sub = db.session.get(Subcategory, sub_id)
        assert sub.name == 'Tornillos'
        for product in Product.query.all():
            assert product.category_id == category_id and product.subcategory_id == sub_id
        assert db.session.get(Product, 1).available_units == 5


def test_delete_subcategory_keeps_products_and_category():
    from app import Category, Subcategory
    client, app, (category_id, other_id, sub_id) = catalog_client()
    response = client.post('/catalog', data={'action':'delete_subcategory', 'id':sub_id}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Subcategory, sub_id) is None
        assert db.session.get(Category, category_id) is not None
        assert Product.query.count() == 2
        for product in Product.query.all():
            assert product.category_id == category_id and product.subcategory_id is None
        assert db.session.get(Product, 1).available_units == 5
        assert Product.query.filter_by(code='INACTIVO').one().active is False


def test_delete_category_keeps_subcategories_stock_sales_and_other_categories():
    from app import Category, Subcategory
    client, app, (category_id, other_id, sub_id) = catalog_client()
    with app.app_context():
        unrelated = Subcategory(name='Otra')
        db.session.add(unrelated); db.session.flush()
        unrelated_id = unrelated.id
        db.session.add(Product(code='OTRO', name='Otro', sale_price=20, unit_price=20,
                               category_id=other_id, subcategory_id=unrelated_id))
        db.session.commit()
    response = client.post('/sales', json={'items':[{'product_id':1,'presentation':'CAJA','quantity':1}], 'payments':[{'method':'EFECTIVO','amount':'1000'}]})
    assert response.status_code == 200
    response = client.post('/catalog', data={'action':'delete_category', 'id':category_id}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Category, category_id) is None
        assert db.session.get(Subcategory, sub_id).name == 'Manuales'
        assert Product.query.count() == 3
        product = db.session.get(Product, 1)
        assert product.category_id is None and product.subcategory_id == sub_id
        assert product.available_units == 4 and product.active
        inactive = Product.query.filter_by(code='INACTIVO').one()
        assert inactive.category_id is None and inactive.subcategory_id == sub_id and not inactive.active
        other = Product.query.filter_by(code='OTRO').one()
        assert other.category_id == other_id and other.subcategory_id == unrelated_id
        assert Sale.query.count() == 1 and SaleItem.query.one().product_id == 1


def test_catalog_rejects_invalid_edits_without_changing_assignments():
    from app import Category, Subcategory
    client, app, (category_id, other_id, sub_id) = catalog_client()
    with app.app_context():
        db.session.add(Subcategory(name='Duplicada')); db.session.commit()
    for payload in [
        {'action':'edit_category', 'id':category_id, 'name':'Fijaciones'},
        {'action':'edit_category', 'id':category_id, 'name':'   '},
        {'action':'edit_subcategory', 'id':sub_id, 'name':'Duplicada'},
        {'action':'edit_subcategory', 'id':sub_id, 'name':'   '},
        {'action':'edit_subcategory', 'id':99999, 'name':'Manual'},
        {'action':'delete_category', 'id':99999},
    ]:
        response = client.post('/catalog', data=payload, follow_redirects=True)
        assert response.status_code == 200 and b'No se pudo actualizar' in response.data
    with app.app_context():
        assert db.session.get(Category, category_id).name == 'Herramientas'
        assert db.session.get(Subcategory, sub_id).name == 'Manuales'
        assert db.session.get(Product, 1).category_id == category_id


def test_employee_cannot_modify_catalog():
    client, app, (category_id, other_id, sub_id) = catalog_client(); login(client, 'employee')
    for action, entry_id in [('edit_category', category_id), ('delete_category', category_id), ('edit_subcategory', sub_id), ('delete_subcategory', sub_id)]:
        assert client.post('/catalog', data={'action':action,'id':entry_id,'name':'Otro','category_id':other_id}).status_code == 403


def test_sales_reports_identify_customers_and_historical_discounts():
    client, app = app_client(); login(client)
    with app.app_context():
        customer = Customer(name='María Álvarez', first_name='María', last_name='Álvarez',
                            dni='12345678', discount_percent=30, active=False)
        legacy = Customer(name='Cliente anterior', discount_percent=0)
        cancelled_customer = Customer(name='Cliente venta anulada')
        outside_customer = Customer(name='Cliente fuera de fecha')
        db.session.add_all([customer, legacy, cancelled_customer, outside_customer]); db.session.flush()
        records = [
            (customer.id, 170, 'CONFIRMADA', datetime(2026, 9, 20, 10, 30), 15),
            (None, 200, 'CONFIRMADA', datetime(2026, 9, 20, 11, 0), 0),
            (legacy.id, 200, 'CONFIRMADA', datetime(2026, 9, 20, 12, 0), 0),
            (cancelled_customer.id, 200, 'ANULADA', datetime(2026, 9, 20, 13, 0), 0),
            (outside_customer.id, 200, 'CONFIRMADA', datetime(2026, 9, 21, 10, 0), 0),
        ]
        for customer_id, amount, status, created_at, discount in records:
            sale = Sale(customer_id=customer_id, user_id=2, total=amount, status=status, created_at=created_at)
            db.session.add(sale); db.session.flush()
            db.session.add(SaleItem(sale_id=sale.id, product_id=1, quantity=2, sale_quantity=2,
                                   presentation='UNIDAD', unit_price=amount/2, subtotal=amount,
                                   discount_percent=discount))
            db.session.add(Payment(sale_id=sale.id, method='EFECTIVO', amount=amount))
        db.session.commit()
    for path in ['/reports/sales', '/reports/sales/daily/2026-09-20',
                 '/reports/sales/range?start_date=2026-09-20&end_date=2026-09-20']:
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Ventas y clientes' in html
        assert 'Álvarez, María' in html and 'DNI 12345678' in html
        assert 'Cliente anterior' in html and 'Sin cliente asignado' in html
        assert '15.00% de descuento' in html and '30.00% de descuento' not in html
        assert '10:30' in html and '170,00' in html and 'Martillo' in html
        assert 'Cliente venta anulada' not in html
        if path != '/reports/sales':
            assert 'Cliente fuera de fecha' not in html
            assert '570,00' in html
    assert client.get('/reports/sales/daily/2026-09-22').status_code == 200


def test_product_barcode_print_page_uses_existing_code_without_modifying_product():
    from base64 import b64decode
    from xml.etree import ElementTree
    import re

    client, app = app_client(); login(client)
    for code in ['5646', '001234567890', 'TOR-001']:
        with app.app_context():
            product = db.session.get(Product, 1); product.code = code; db.session.commit()
        response = client.get('/products/1/barcode')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Imprimir etiqueta' in html and 'Martillo' in html
        assert f'<span class="barcode-code">{code}</span>' in html
        encoded = re.search(r'src="data:image/svg\+xml;base64,([^"]+)"', html).group(1)
        root = ElementTree.fromstring(b64decode(encoded))
        assert len(root.findall('.//{http://www.w3.org/2000/svg}rect')) > 10
        with app.app_context():
            product = db.session.get(Product, 1)
            assert product.code == code and product.available_units == 5
        assert '/products/1/barcode' in client.get('/products').get_data(as_text=True)
        assert '/products/1/barcode' in client.get('/products?partial=1&q='+code).get_data(as_text=True)


def test_product_barcode_requires_login_and_handles_invalid_codes():
    client, app = app_client()
    assert client.get('/products/1/barcode').status_code == 302
    login(client)
    assert client.get('/products/99999/barcode').status_code == 404
    for code in ['CÓDIGO-1', '', '123\n456']:
        with app.app_context():
            db.session.get(Product, 1).code = code; db.session.commit()
        response = client.get('/products/1/barcode')
        assert response.status_code == 422
        assert 'Imprimir etiqueta' not in response.get_data(as_text=True)
        assert 'data:image/svg' not in response.get_data(as_text=True)


def test_owner_generates_random_code_skipping_existing_products(monkeypatch):
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        product = db.session.get(Product, 1)
        product.code = '000000000123'
        db.session.commit()
    values = iter([123, 456])
    monkeypatch.setattr('app.secrets.randbelow', lambda limit: next(values))
    response = client.get('/products/generate-code')
    assert response.status_code == 200
    assert response.get_json()['code'] == '000000000456'


def test_generate_product_code_is_available_to_employee_and_button_is_on_new_form():
    client, _ = app_client(); login(client)
    assert client.get('/products/generate-code').status_code == 200
    new_form = client.get('/products/new').get_data(as_text=True)
    edit_form = client.get('/products/1/edit').get_data(as_text=True)
    assert 'id="generate-product-code"' in new_form
    assert 'id="generate-product-code"' not in edit_form


def test_owner_deletes_customer_without_erasing_sales_or_debts():
    from decimal import Decimal
    from app import AuditEvent
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        customer = Customer(name='Cliente a eliminar', dni='44555666', debt_balance=80)
        db.session.add(customer); db.session.flush(); customer_id = customer.id
        sale = Sale(user_id=1, customer_id=customer_id, total=100)
        db.session.add(sale); db.session.commit(); sale_id = sale.id
    response = client.post(f'/customers/{customer_id}/delete', follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Cliente a eliminar' not in html.split('<h2>Lista de deudas</h2>')[0]
    assert 'Cliente a eliminar' in html.split('<h2>Lista de deudas</h2>')[1]
    assert 'Cliente eliminado' in html
    assert 'Cliente a eliminar' not in client.get('/sales/new').get_data(as_text=True)
    assert 'Cliente a eliminar' in client.get('/reports/sales').get_data(as_text=True)
    with app.app_context():
        customer = db.session.get(Customer, customer_id)
        assert not customer.active and customer.debt_balance == Decimal('80')
        assert customer.dni == '44555666'
        sale = db.session.get(Sale, sale_id)
        assert sale.customer_id == customer_id and sale.total == Decimal('100')
    # Repetir la solicitud no elimina el historial ni genera una segunda baja.
    assert client.post(f'/customers/{customer_id}/delete').status_code == 302
    with app.app_context():
        assert AuditEvent.query.filter_by(action='ELIMINAR_CLIENTE').count() == 1


def test_customer_delete_requires_owner_and_post():
    client, app = app_client(); login(client, 'employee')
    with app.app_context():
        customer = Customer(name='Cliente protegido')
        db.session.add(customer); db.session.commit(); customer_id = customer.id
    assert client.post(f'/customers/{customer_id}/delete').status_code == 403
    assert f'/customers/{customer_id}/delete' not in client.get('/customers').get_data(as_text=True)
    login(client, 'owner')
    assert f'/customers/{customer_id}/delete' in client.get('/customers').get_data(as_text=True)
    assert client.get(f'/customers/{customer_id}/delete').status_code == 405
    assert client.post('/customers/99999/delete').status_code == 404
    with app.app_context(): assert db.session.get(Customer, customer_id).active


def test_stale_cart_cannot_sell_to_deleted_customer():
    client, app = app_client(); login(client, 'owner')
    with app.app_context():
        customer = Customer(name='Cliente eliminado', active=False, discount_percent=20)
        db.session.add(customer); db.session.commit(); customer_id = customer.id
    response = client.post('/sales', json={'customer_id':customer_id,
        'items':[{'product_id':1,'presentation':'UNIDAD','quantity':1}],
        'payments':[{'method':'EFECTIVO','amount':'80'}]})
    assert response.status_code == 400
    assert 'cliente seleccionado ya no está disponible' in response.json['error']
    with app.app_context():
        assert Sale.query.count() == 0 and db.session.get(Product, 1).available_units == 5


def test_employee_can_create_restock_and_record_loss_with_low_stock_sidebar():
    from app import InventoryMovement

    client, app = app_client(); login(client, 'employee')
    response = client.post('/products/new', data={
        'code':'EMP-1', 'name':'Producto empleado', 'unit_price':'100',
        'loose_units':'1', 'minimum_stock':'2',
    }, follow_redirects=True)
    assert response.status_code == 200 and 'Producto creado.' in response.get_data(as_text=True)
    with app.app_context():
        product = Product.query.filter_by(code='EMP-1').one()
        product_id = product.id
    html = client.get('/products?q=No+existe').get_data(as_text=True)
    assert 'Necesitan reposición' in html and 'Producto empleado' in html
    html = client.get('/products').get_data(as_text=True)
    assert f'/products/{product_id}/restock' in html and f'/products/{product_id}/loss' in html
    assert f'/products/{product_id}/edit' not in html
    assert f'/products/{product_id}/delete' not in html
    assert client.get(f'/products/{product_id}/loss').status_code == 200
    assert client.get(f'/products/{product_id}/restock').status_code == 200

    response = client.post(f'/products/{product_id}/restock', data={
        'add_units':'3', 'purchase_price':'50', 'unit_price':'100',
    }, follow_redirects=True)
    assert 'Reposición y precios actualizados.' in response.get_data(as_text=True)
    assert 'Producto empleado' not in client.get('/products?q=No+existe').get_data(as_text=True).split('Necesitan reposición')[1]
    response = client.post(f'/products/{product_id}/loss', data={
        'presentation':'UNIDAD', 'quantity':'2', 'loss_type':'ROTURA',
        'reason_detail':'Dos unidades dañadas.',
    }, follow_redirects=True)
    assert 'Merma registrada y stock actualizado correctamente.' in response.get_data(as_text=True)
    with app.app_context():
        product = db.session.get(Product, product_id)
        assert product.available_units == 2 and product.is_stock_critical
        movements = InventoryMovement.query.filter_by(product_id=product_id).order_by(InventoryMovement.id).all()
        assert [movement.movement_type for movement in movements] == ['ALTA', 'REPOSICION', 'MERMA']
        assert all(movement.user_id == 2 for movement in movements)
    login(client, 'owner')
    assert 'Producto empleado' in client.get('/products?q=No+existe').get_data(as_text=True)


def test_employee_discount_needs_owner_decision_and_cannot_affect_sales_early():
    client, app = app_client(); login(client, 'employee')
    response = client.post('/customers', data={
        'first_name':'Ana', 'last_name':'Pérez', 'discount_percent':'10',
    }, follow_redirects=True)
    assert 'pendiente hasta que lo apruebe una cuenta dueño' in response.get_data(as_text=True)
    with app.app_context():
        customer = Customer.query.one()
        customer_id = customer.id
        assert customer.discount_percent == 0 and customer.pending_discount_percent == 10
    assert client.post(f'/customers/{customer_id}/discount', data={'decision':'approve'}).status_code == 403
    sale_payload = {
        'customer_id':customer_id,
        'items':[{'product_id':1, 'presentation':'UNIDAD', 'quantity':1}],
        'payments':[{'method':'DEBITO', 'amount':''}],
    }
    assert client.post('/sales', json=sale_payload).status_code == 200
    with app.app_context():
        assert Sale.query.one().total == 100
        assert SaleItem.query.one().discount_percent == 0

    login(client, 'owner')
    html = client.get('/customers').get_data(as_text=True)
    assert 'Descuentos pendientes de aprobación' in html
    assert '10.00% pendiente de aprobación' in html
    assert client.post(f'/customers/{customer_id}/discount', data={'decision':'approve'}, follow_redirects=True).status_code == 200
    with app.app_context():
        customer = db.session.get(Customer, customer_id)
        assert customer.discount_percent == 10 and customer.pending_discount_percent is None
        assert AuditEvent.query.filter_by(action='SOLICITAR_DESCUENTO').one().user_id == 2
        assert AuditEvent.query.filter_by(action='APROBAR_DESCUENTO').one().user_id == 1
    login(client, 'employee')
    assert client.post('/sales', json=sale_payload).status_code == 200
    with app.app_context():
        assert Sale.query.order_by(Sale.id.desc()).first().total == 90

    client.post('/customers', data={'first_name':'Juan', 'last_name':'Ríos', 'discount_percent':'15'})
    with app.app_context():
        second_id = Customer.query.filter_by(first_name='Juan').one().id
    login(client, 'owner')
    client.post(f'/customers/{second_id}/discount', data={'decision':'reject'})
    with app.app_context():
        second = db.session.get(Customer, second_id)
        assert second.discount_percent == 0 and second.pending_discount_percent is None
        assert AuditEvent.query.filter_by(action='RECHAZAR_DESCUENTO').one().user_id == 1


def test_owner_created_customer_discount_is_available_immediately():
    client, app = app_client(); login(client, 'owner')
    response = client.post('/customers', data={
        'first_name':'Laura', 'last_name':'Gómez', 'discount_percent':'12.5',
    }, follow_redirects=True)
    assert 'Cliente agregado.' in response.get_data(as_text=True)
    with app.app_context():
        customer = Customer.query.one()
        assert customer.discount_percent == 12.5 and customer.pending_discount_percent is None


def test_blank_payment_amount_covers_total_for_every_method():
    client, app = app_client(); login(client, 'employee')
    for method in ['EFECTIVO', 'DEBITO', 'CREDITO', 'TRANSFERENCIA', 'QR']:
        response = client.post('/sales', json={
            'items':[{'product_id':1, 'presentation':'UNIDAD', 'quantity':1}],
            'payments':[{'method':method, 'amount':''}],
        })
        assert response.status_code == 200 and response.json['ok']
    with app.app_context():
        assert [(payment.method, payment.amount) for payment in Payment.query.order_by(Payment.id)] == [
            (method, 100) for method in ['EFECTIVO', 'DEBITO', 'CREDITO', 'TRANSFERENCIA', 'QR']
        ]


def test_mixed_payment_allows_one_blank_remainder_but_rejects_ambiguous_blanks():
    client, app = app_client(); login(client, 'employee')
    payload = {'items':[{'product_id':1, 'presentation':'UNIDAD', 'quantity':1}]}
    response = client.post('/sales', json={**payload, 'payments':[
        {'method':'DEBITO', 'amount':'40'}, {'method':'QR', 'amount':''},
    ]})
    assert response.status_code == 200
    with app.app_context():
        assert [(payment.method, payment.amount) for payment in Payment.query.order_by(Payment.id)] == [('DEBITO', 40), ('QR', 60)]
    response = client.post('/sales', json={**payload, 'payments':[
        {'method':'EFECTIVO', 'amount':''}, {'method':'QR', 'amount':''},
    ]})
    assert response.status_code == 400 and 'un solo importe vacío' in response.json['error']
    with app.app_context():
        assert Sale.query.count() == 1 and db.session.get(Product, 1).available_units == 4


def test_existing_customer_table_adds_pending_discount_without_losing_clients():
    from sqlalchemy import inspect, text
    from app import ensure_schema

    _, app = app_client()
    with app.app_context():
        db.session.add(Customer(name='Cliente anterior', discount_percent=5))
        db.session.commit()
        db.session.execute(text('ALTER TABLE customer DROP COLUMN pending_discount_percent'))
        db.session.commit()
        ensure_schema()
        columns = {column['name'] for column in inspect(db.engine).get_columns('customer')}
        assert 'pending_discount_percent' in columns
        customer = Customer.query.one()
        assert customer.name == 'Cliente anterior' and customer.discount_percent == 5
        assert customer.pending_discount_percent is None
