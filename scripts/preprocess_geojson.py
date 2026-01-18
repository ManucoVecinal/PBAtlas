# -*- coding: utf-8 -*-
"""
Script para preprocesar el GeoJSON de municipios.
Filtra solo PBA, simplifica geometrias y reduce tamaño.
"""

import json
import os
from pathlib import Path

def norm_georef(x) -> str:
    """Normaliza id_georef a 5 digitos."""
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = "".join(ch for ch in s if ch.isdigit())
    return s.zfill(5)


def preprocess_geojson(input_path: str, output_path: str, simplify: bool = True):
    """
    Preprocesa GeoJSON filtrando solo municipios de PBA.

    Args:
        input_path: Ruta al GeoJSON original
        output_path: Ruta donde guardar el GeoJSON optimizado
        simplify: Si True, intenta simplificar geometrias (requiere shapely)
    """
    print(f"Leyendo GeoJSON original: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        geo = json.load(f)

    original_count = len(geo.get("features", []))
    print(f"Features originales: {original_count}")

    # Filtrar solo municipios de PBA (id_georef empieza con "06")
    pba_features = []
    for feat in geo.get("features", []):
        props = feat.get("properties") or {}

        # Buscar el id en diferentes campos posibles
        raw_id = (props.get("in1") or props.get("IN1") or
                  props.get("id") or props.get("ID") or
                  props.get("nam") or "")

        gid = norm_georef(raw_id)

        if not gid.startswith("06"):
            continue

        # Normalizar propiedades
        props["id_georef"] = gid
        props["Muni_Nombre"] = props.get("nam") or props.get("NAM") or props.get("nombre") or ""

        # Mantener solo propiedades necesarias
        feat["properties"] = {
            "id_georef": props["id_georef"],
            "Muni_Nombre": props["Muni_Nombre"],
        }

        pba_features.append(feat)

    print(f"Features PBA: {len(pba_features)}")

    # Simplificar geometrias si es posible
    if simplify:
        try:
            from shapely.geometry import shape, mapping
            from shapely import simplify as shapely_simplify

            print("Simplificando geometrias...")
            for feat in pba_features:
                geom = shape(feat["geometry"])
                # Tolerance de 0.001 grados ~ 100m
                simplified = shapely_simplify(geom, tolerance=0.001, preserve_topology=True)
                feat["geometry"] = mapping(simplified)
            print("Geometrias simplificadas correctamente")
        except ImportError:
            print("Shapely no disponible, geometrias sin simplificar")

    # Crear nuevo GeoJSON
    output_geo = {
        "type": "FeatureCollection",
        "features": pba_features
    }

    # Guardar
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_geo, f, ensure_ascii=False)

    # Reportar tamaños
    input_size = os.path.getsize(input_path) / (1024 * 1024)
    output_size = os.path.getsize(output_path) / (1024 * 1024)

    print(f"\nResultado:")
    print(f"  Tamaño original: {input_size:.1f} MB")
    print(f"  Tamaño optimizado: {output_size:.1f} MB")
    print(f"  Reduccion: {(1 - output_size/input_size) * 100:.1f}%")
    print(f"\nArchivo guardado en: {output_path}")


if __name__ == "__main__":
    base_dir = Path(__file__).parent.parent

    input_path = base_dir / "data" / "geo" / "MunicipiosArgentina.geojson"
    output_path = base_dir / "data" / "geo" / "pba_municipios_optimized.geojson"

    if not input_path.exists():
        print(f"ERROR: No se encuentra el archivo: {input_path}")
        exit(1)

    preprocess_geojson(str(input_path), str(output_path), simplify=True)
