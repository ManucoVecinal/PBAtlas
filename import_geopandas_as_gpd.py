from pathlib import Path
import pandas as pd
from dbfread import DBF

BASE_DIR = Path(__file__).resolve().parent.parent
dbf_path = BASE_DIR / "data_raw" / "departamentoPolygon.dbf"
out_csv  = BASE_DIR / "data_processed" / "municipios_georef.csv"

table = DBF(str(dbf_path), load=True, encoding="latin1")  # si ves caracteres raros, probamos utf-8
df = pd.DataFrame(iter(table))

print("Columnas disponibles:", list(df.columns))

# Ajustá estos nombres si en tu DBF aparecen con mayúsculas (IN1/NAM)
col_id = "in1" if "in1" in df.columns else "IN1"
col_nm = "nam" if "nam" in df.columns else "NAM"

out = df[[col_id, col_nm]].copy()
out.columns = ["id_georef", "nombre"]
out.to_csv(out_csv, index=False, encoding="utf-8")

print("OK ->", out_csv)
print("Filas:", len(out))


print("DBF path:", dbf_path)
print("Existe?", dbf_path.exists())