import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from dispatch.config import database_url, settings
from dispatch.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url = database_url(os.getenv("MIGRATION_DATABASE_URL", settings.database_url))
options = {"target_metadata": Base.metadata, "compare_type": True}

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(241125801)")
            context.configure(connection=connection, **options)
            with context.begin_transaction():
                context.run_migrations()
    engine.dispose()
