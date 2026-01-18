from pathlib import Path
import pandas as pd
from dbfread import DBF

BASE_DIR = Path(__file__).resolve().parent.parent
dbf_path = BASE_DIR / "data_raw" / "departamentoPolygon.dbf"

out_dir = BASE_DIR / "data_processed"
out_dir.mkdir(parents=True, exist_ok=True)
out_csv = out_dir / "municipios_georef_PBA.csv"

print("DBF path:", dbf_path)
print("Existe?", dbf_path.exists())

# 1) Probar encodings (UTF-8 primero)
encodings = ["utf-8", "cp1252", "latin1"]
last_err = None
df = None

for enc in encodings:
    try:
        table = DBF(str(dbf_path), load=True, encoding=enc)
        df = pd.DataFrame(iter(table))
        print("Encoding OK:", enc)
        break
    except Exception as e:
        last_err = e

if df is None:
    raise RuntimeError(f"No pude leer el DBF con encodings {encodings}. Último error: {last_err}")

print("Columnas disponibles:", list(df.columns))
print("Filas total:", len(df))

# 2) Normalizar id y nombre
# in1 a string y con ceros a la izquierda (5 dígitos)
df["in1_str"] = df["in1"].astype(str).str.strip().str.zfill(5)

# 3) Filtrar Buenos Aires
# Provincia BA = 06xxx (en este dataset)
df_pba = df[df["in1_str"].str.startswith("06")].copy()

# Si querés ser más estricto además:
# df_pba = df_pba[df_pba["gna"].astype(str).str.strip().str.lower() == "partido"].copy()

out = df_pba[["in1_str", "nam"]].copy()
out.columns = ["id_georef", "nombre"]

# 4) Guardar CSV en UTF-8 (Excel lo abre bien)
out.to_csv(out_csv, index=False, encoding="utf-8-sig")

print("OK -> CSV generado:", out_csv)
print("Filas PBA:", len(out))
print(out.head(10))
