from .db import connect, migrate

if __name__ == "__main__":
    c = connect()
    print(f"schema version {migrate(c)}")
    c.close()
