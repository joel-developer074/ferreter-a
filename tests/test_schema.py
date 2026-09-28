"""Verifica que la migración preserve los datos y pueda revertirse ante errores."""

import pytest
from sqlalchemy import create_engine

from app.schema import make_subcategories_independent


def legacy_engine(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE category (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE subcategory (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, category_id INTEGER NOT NULL REFERENCES category(id), UNIQUE(name, category_id))")
        connection.exec_driver_sql("CREATE TABLE product (id INTEGER PRIMARY KEY, category_id INTEGER REFERENCES category(id), subcategory_id INTEGER REFERENCES subcategory(id), stock INTEGER)")
        connection.exec_driver_sql("INSERT INTO category VALUES (1, 'Herramientas'), (2, 'Fijaciones')")
        connection.exec_driver_sql("INSERT INTO subcategory VALUES (3, 'General', 1), (7, 'General', 2)")
        connection.exec_driver_sql("INSERT INTO product VALUES (1, 1, 3, 25), (2, 2, 7, 8)")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    return engine


def test_migration_preserves_ids_duplicate_names_and_product_assignments(tmp_path):
    engine = legacy_engine(tmp_path / "legacy.db")
    try:
        make_subcategories_independent(engine)
        make_subcategories_independent(engine)
        with engine.begin() as connection:
            assert connection.exec_driver_sql("SELECT * FROM subcategory ORDER BY id").all() == [(3, 'General'), (7, 'General')]
            assert connection.exec_driver_sql("SELECT * FROM product ORDER BY id").all() == [(1, 1, 3, 25), (2, 2, 7, 8)]
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            connection.exec_driver_sql("INSERT INTO subcategory (name) VALUES ('Nueva')")
            connection.exec_driver_sql("UPDATE product SET category_id=NULL WHERE category_id=1")
            connection.exec_driver_sql("DELETE FROM category WHERE id=1")
            assert connection.exec_driver_sql("SELECT name FROM subcategory WHERE id=3").scalar() == 'General'
    finally:
        engine.dispose()


def test_migration_rolls_back_schema_and_restores_foreign_keys_on_failure(tmp_path):
    engine = legacy_engine(tmp_path / "invalid.db")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("UPDATE product SET subcategory_id=999 WHERE id=1")
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        with pytest.raises(RuntimeError, match="referencias inválidas"):
            make_subcategories_independent(engine)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT * FROM subcategory ORDER BY id").all() == [(3, 'General', 1), (7, 'General', 2)]
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='subcategory_independent'").first() is None
    finally:
        engine.dispose()
