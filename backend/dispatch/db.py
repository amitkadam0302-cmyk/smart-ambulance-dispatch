from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dispatch.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.pool_size, max_overflow=0, pool_timeout=5,
    pool_recycle=180, pool_pre_ping=True, hide_parameters=True,
    connect_args={"connect_timeout": 5, "prepare_threshold": None},
)
Session = sessionmaker(engine, expire_on_commit=False)
