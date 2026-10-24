from backend.database import engine
from sqlalchemy import text
print(engine.dialect.name)
