<!-- Guía de ejecución, pruebas y operación de FerreSoft. -->
# FerreSoft

Primera versión operativa para una ferretería única: productos, stock, ventas por ventanilla, pagos, remitos internos, anulación de tickets y resumen de ventas.

## Ejecutar

En PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

Abrir `http://127.0.0.1:5000`.

Usuarios iniciales (cambiarlos antes de producción):

- `dueno` / `Cambiar123!`
- `empleado` / `Empleado123!`

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Categorías y subcategorías

Las subcategorías se crean y editan solo por nombre, sin elegir una categoría. Al crear o editar un producto se puede combinar cualquier categoría con cualquier subcategoría, o elegir solo una de las dos. Eliminar una categoría conserva las subcategorías de los productos; eliminar una subcategoría conserva sus categorías.

En el formulario de productos, el nombre ofrece un desplegable de nombres existentes que se filtra al escribir y también permite ingresar nombres nuevos. Un nombre puede repetirse si cambia la categoría o la subcategoría. Las opciones «Sin categoría» y «Sin subcategoría» cuentan como valores concretos: no se admite otra alta o edición que repita los tres campos, aunque estén vacíos ambos selectores. La validación ignora mayúsculas y espacios repetidos, incluye productos inactivos y conserva el código único de cada producto.

Al iniciar, las instalaciones SQLite anteriores se actualizan automáticamente conservando los IDs, los nombres de las subcategorías y las asignaciones de los productos. Los nombres repetidos que ya existan se conservan; las nuevas altas no admiten nombres repetidos. Para otras bases de datos, la migración debe quitar la columna `subcategory.category_id` y sus restricciones antes de ejecutar esta versión.

## Stock mínimo

Si el producto se vende por caja, el stock mínimo se ingresa en cajas y se compara con las cajas cerradas disponibles. Si se vende solo por unidad, se ingresa y controla en unidades. El indicador del panel y el resaltado en las listas se activan al llegar al mínimo o quedar por debajo. La cantidad ingresada se conserva al cambiar de presentación y pasa a expresarse en la opción elegida.

## Operación programada

La instalación incluye dos scripts listos para registrar con el Programador de tareas de Windows:

```powershell
.\.venv\Scripts\python.exe .\scripts\daily_backup.py
.\.venv\Scripts\python.exe .\scripts\monthly_sales_summary.py
```

El primero crea una copia diaria en `backups\`; el segundo deja el resumen mensual en `instance\resumenes\`. Para el despliegue con MySQL se debe configurar un backup consistente del servidor de base de datos y almacenamiento externo.

## Producción

La configuración local usa SQLite para facilitar el inicio. Para MySQL, definir `DATABASE_URL` usando un controlador MySQL compatible y ejecutar migraciones antes de producción. También debe configurarse una `SECRET_KEY` real, HTTPS, rate limiting, CSRF y el backup programado por el servidor.

Los remitos emitidos por esta versión son internos y no reemplazan comprobantes fiscales.

## Lector de códigos de barras

Usar un lector USB o Bluetooth configurado como teclado (HID), con sufijo Enter.

- En Nueva venta, usar el único buscador para escribir el nombre o escanear el código con Enter: cada lectura agrega una unidad y las lecturas repetidas aumentan la cantidad. Se utiliza únicamente el campo Código. Para cajas, seleccionar el producto manualmente.
- En Productos, el buscador queda seleccionado al abrir la pantalla: escanear busca por código; la siguiente lectura reemplaza la búsqueda anterior.
- En Nuevo producto, el campo Código recibe la lectura directamente, conservando ceros iniciales. Enter no guarda el formulario.
- Si se está escribiendo en otro campo, hacer clic en el campo de lectura antes de escanear.

El campo adicional Código de barras fue retirado. Las bases existentes conservan su columna antigua sin utilizarla, para resguardar los datos; los códigos actuales de los productos no cambian.

## Imprimir etiquetas de productos

En Productos, el botón **Imprimir código** debajo del código abre la etiqueta con el nombre y el código de barras. Usar **Imprimir etiqueta**, escala 100% y desactivar encabezados y pies de página del navegador.

Se usa el campo Código existente, conservando ceros iniciales, letras y números; no se modifica el producto. La generación se realiza localmente con [python-barcode](https://python-barcode.readthedocs.io/en/stable/supported-formats.html) en formato Code 128. Instalar las dependencias de `requirements.txt` al actualizar otra instalación. Los códigos con tildes u otros caracteres no admitidos muestran un aviso y no generan una etiqueta incorrecta.
