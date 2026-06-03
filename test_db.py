import psycopg2

try:
    conn = psycopg2.connect(
        host="localhost",
        database="satellite_metadata",
        user="pipeline_admin",
        password="pipeline_pass",
        port=5432
    )

    print("CONNECTED SUCCESSFULLY")
    conn.close()

except Exception as e:
    print("FAILED:")
    print(type(e).__name__)
    print(e)