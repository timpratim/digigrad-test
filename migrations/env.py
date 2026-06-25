"""Alembic migration environment for gradphone (Postgres deployments).

Targets the SQLAlchemy metadata defined in gradphone.db, and reads the DB URL
from the environment via db.sync_url() — so the same DATABASE_* / DATABASE_URL
config the app uses drives migrations too. Online (sync engine) mode only.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from gradphone import db as db_mod

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime DB URL (env-driven) so we never hardcode credentials.
config.set_main_option("sqlalchemy.url", db_mod.sync_url())

target_metadata = db_mod.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
