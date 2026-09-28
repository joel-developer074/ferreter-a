"""Migraciones de la base SQLite instalada."""


def make_subcategories_independent(engine):
    """Quita la categoría de las subcategorías, conservando sus IDs y nombres."""
    if engine.dialect.name != "sqlite":
        return

    connection = engine.raw_connection()
    cursor = connection.cursor()
    foreign_keys = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
    try:
        columns = {column[1] for column in cursor.execute("PRAGMA table_info(subcategory)")}
        if "category_id" not in columns:
            return

        # SQLite requiere reconstruir la tabla para quitar la columna y sus restricciones.
        # La transacción explícita permite revertir también los cambios de estructura.
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("CREATE TABLE subcategory_independent (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL)")
        cursor.execute("INSERT INTO subcategory_independent (id, name) SELECT id, name FROM subcategory")
        cursor.execute("DROP TABLE subcategory")
        cursor.execute("ALTER TABLE subcategory_independent RENAME TO subcategory")
        if cursor.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("No se pudo migrar las subcategorías: hay referencias inválidas en la base de datos.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.execute(f"PRAGMA foreign_keys={foreign_keys}")
        cursor.close()
        connection.close()
