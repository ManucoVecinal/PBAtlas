# -*- coding: utf-8 -*-
"""
MunicipiosPBA Atlas - Tablero de Comando Provincial
====================================================
Tablero interactivo con el mapa como eje central.
- Vista inicial: totales provinciales
- Al seleccionar municipio: datos del municipio + comparación provincial
"""

import json
import unicodedata
from typing import Dict, Any, Tuple, Optional, List

import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import altair as alt

from supabase import create_client


# ======================================================
# PAGE CONFIG
# ======================================================
st.set_page_config(
    page_title="MunicipiosPBA Atlas - Tablero Provincial",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Accept both top-level keys and [supabase] section in Streamlit secrets.
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "") or st.secrets.get("supabase", {}).get("url", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "") or st.secrets.get("supabase", {}).get("key", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)

# Optional debug: show which secret keys are visible (no values).
try:
    debug_flag = st.query_params.get("debug", "")
    if isinstance(debug_flag, list):
        debug_flag = debug_flag[0]
except Exception:
    debug_flag = ""
if str(debug_flag) == "1":
    st.sidebar.info(f"Secrets keys: {sorted(list(st.secrets.keys()))}")
    st.sidebar.info(
        "Supabase config: "
        f"url_len={len(str(SUPABASE_URL))}, "
        f"key_len={len(str(SUPABASE_KEY))}, "
        f"use={USE_SUPABASE}"
    )


# ======================================================
# SESSION STATE
# ======================================================
DEFAULT_STATE = {
    "municipio_sel": None,          # ID_Municipio seleccionado (None = vista provincial)
    "municipio_nombre": None,       # Nombre para display
    "municipio_georef": None,       # id_georef para el mapa
    "map_metric": "recursos_percibido",  # Métrica a visualizar
    "map_normalization": "absoluto",     # absoluto | per_capita | por_km2
    "filter_jurisdiccion": None,    # Filtro de jurisdicción seleccionada
    "filter_programa": None,        # Filtro de programa seleccionado
}

def init_session_state():
    for key, default in DEFAULT_STATE.items():
        if key not in st.session_state:
            st.session_state[key] = default

def reset_to_provincial():
    """Vuelve a vista provincial limpiando selección."""
    st.session_state.municipio_sel = None
    st.session_state.municipio_nombre = None
    st.session_state.municipio_georef = None
    st.session_state.filter_jurisdiccion = None
    st.session_state.filter_programa = None

def select_municipio(id_municipio: str, nombre: str, georef: str):
    """Selecciona un municipio y actualiza estado."""
    st.session_state.municipio_sel = id_municipio
    st.session_state.municipio_nombre = nombre
    st.session_state.municipio_georef = georef

init_session_state()


# ======================================================
# CONFIGURACIÓN DE MÉTRICAS DEL MAPA
# ======================================================
MAP_METRICS = {
    "recursos_percibido": {
        "label": "Recursos Percibidos",
        "column": "recursos_percibido",
        "format": "money",
        "colorscale": "Greens",
        "description": "Total de recursos percibidos"
    },
    "gastos_pagado": {
        "label": "Gastos Pagados",
        "column": "gastos_pagado",
        "format": "money",
        "colorscale": "Reds",
        "description": "Total de gastos pagados"
    },
    "balance_fiscal": {
        "label": "Balance Fiscal",
        "column": "balance_fiscal",
        "format": "money",
        "colorscale": "RdYlGn",
        "description": "Recursos - Gastos"
    },
    "tasa_ejecucion": {
        "label": "Tasa de Ejecución",
        "column": "tasa_ejecucion",
        "format": "percent",
        "colorscale": "YlOrRd",
        "description": "Devengado / Vigente"
    },
    "poblacion": {
        "label": "Población 2022",
        "column": "Muni_Poblacion_2022",
        "format": "number",
        "colorscale": "Blues",
        "description": "Población censo 2022"
    },
    "trabajadores": {
        "label": "Trabajadores Municipales",
        "column": "Muni_Cantidad_Trabajadores",
        "format": "number",
        "colorscale": "Purples",
        "description": "Personal municipal"
    }
}

NORMALIZATIONS = {
    "absoluto": {"label": "Valor Absoluto", "suffix": ""},
    "per_capita": {"label": "Per Cápita", "suffix": " / hab"},
    "por_km2": {"label": "Por km²", "suffix": " / km²"},
    "proyectado": {"label": "Valores Proyectados", "suffix": " (proy.)"}
}

# Factores de proyección según período del documento
FACTORES_PROYECCION = {
    "Q1": 4.0,
    "Q2": 2.0,
    "Q3": 1.5,
    "Q4": 1.0,
    "Anual": 1.0
}


def get_factor_proyeccion(doc_periodo: str) -> float:
    """Obtiene el factor de multiplicación para proyectar valores a fin de año."""
    if doc_periodo is None:
        return 1.0
    periodo = str(doc_periodo).strip().upper()
    # Normalizar variaciones comunes
    if periodo in ["Q1", "1Q", "1ER TRIMESTRE", "PRIMER TRIMESTRE", "1"]:
        return 4.0
    elif periodo in ["Q2", "2Q", "2DO TRIMESTRE", "SEGUNDO TRIMESTRE", "2"]:
        return 2.0
    elif periodo in ["Q3", "3Q", "3ER TRIMESTRE", "TERCER TRIMESTRE", "3"]:
        return 1.5
    elif periodo in ["Q4", "4Q", "4TO TRIMESTRE", "CUARTO TRIMESTRE", "4", "ANUAL"]:
        return 1.0
    # Intentar buscar en el diccionario original
    return FACTORES_PROYECCION.get(doc_periodo, 1.0)


# ======================================================
# UTILS
# ======================================================
def norm_georef(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = "".join(ch for ch in s if ch.isdigit())
    return s.zfill(5)


def fmt_num(x, digits: int = 0) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        v = float(x)
        if digits == 0:
            return f"{v:,.0f}".replace(",", ".")
        return f"{v:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(x)


def fmt_money_millions(x, digits: int = 1) -> str:
    try:
        v = float(x or 0)
        m = v / 1_000_000.0
        s = f"{m:.{digits}f}".replace(".", ",")
        return f"$ {s} M"
    except Exception:
        return "—"


def fmt_money_full(x) -> str:
    """Formatea montos con separador de miles (punto) y símbolo $."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        v = float(x)
        # Formatear con separador de miles (punto)
        formatted = f"{v:,.0f}".replace(",", ".")
        return f"$ {formatted}"
    except Exception:
        return "—"


def fmt_pct0(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        return f"{round(float(x) * 100):.0f}%".replace(".", ",")
    except Exception:
        return "—"


def parse_amount_to_float(x) -> float:
    if x is None:
        return 0.0
    s = str(x).strip()
    if s == "":
        return 0.0
    allowed = set("0123456789.,-")
    s = "".join(ch for ch in s if ch in allowed)
    if s.count(",") > 0 and s.count(".") > 0:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    elif s.count(",") > 0 and s.count(".") == 0:
        s = s.replace(",", ".")
    try:
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def norm_txt(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = " ".join(s.split())
    return s


# ======================================================
# KPIs Y MÉTRICAS CALCULADAS
# ======================================================
def calc_tasa_ejecucion(devengado: float, vigente: float) -> Optional[float]:
    if vigente is None or vigente == 0:
        return None
    return devengado / vigente


def calc_tasa_cobro(percibido: float, devengado: float) -> Optional[float]:
    if devengado is None or devengado == 0:
        return None
    return percibido / devengado


def calc_tasa_pago(pagado: float, devengado: float) -> Optional[float]:
    if devengado is None or devengado == 0:
        return None
    return pagado / devengado


def calc_balance_fiscal(recursos_percibido: float, gastos_pagado: float) -> float:
    return (recursos_percibido or 0) - (gastos_pagado or 0)


def calc_ratio_activo_pasivo(activo: float, pasivo: float) -> Optional[float]:
    if pasivo is None or pasivo == 0:
        return None
    return activo / pasivo


def calc_per_capita(monto: float, poblacion: int) -> Optional[float]:
    if poblacion is None or poblacion == 0:
        return None
    return monto / poblacion


def get_semaforo_ejecucion(tasa: Optional[float]) -> tuple:
    if tasa is None:
        return ("#888888", "⚪", "Sin datos")
    if tasa >= 0.8:
        return ("#28a745", "🟢", "Óptima")
    if tasa >= 0.5:
        return ("#ffc107", "🟡", "Media")
    return ("#dc3545", "🔴", "Baja")


def get_semaforo_balance(balance: float) -> tuple:
    if balance > 0:
        return ("#28a745", "🟢", "Superávit")
    if balance == 0:
        return ("#ffc107", "🟡", "Equilibrado")
    return ("#dc3545", "🔴", "Déficit")


def get_semaforo_ratio(ratio: Optional[float]) -> tuple:
    if ratio is None:
        return ("#888888", "⚪", "Sin datos")
    if ratio >= 1.5:
        return ("#28a745", "🟢", "Saludable")
    if ratio >= 1.0:
        return ("#ffc107", "🟡", "Ajustado")
    return ("#dc3545", "🔴", "Crítico")


# ======================================================
# GEOJSON LOADER
# ======================================================
@st.cache_data(show_spinner="Cargando geometrías...")
def load_pba_geojson(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        geo = json.load(f)

    feats = []
    for feat in geo.get("features", []):
        props = feat.get("properties") or {}
        raw = props.get("id_georef") or props.get("in1") or props.get("IN1") or props.get("id") or props.get("ID")
        gid = norm_georef(raw)
        if not gid.startswith("06"):
            continue
        props["id_georef"] = gid
        feat["properties"] = props
        feats.append(feat)

    geo["features"] = feats
    return geo


# ======================================================
# SUPABASE CLIENT
# ======================================================
def get_sb_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _raise_if_error(res):
    err = getattr(res, "error", None)
    if err:
        raise Exception(err)


# ======================================================
# FETCH: DATOS BASE
# ======================================================
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_municipios_base() -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene datos base de todos los municipios."""
    if not USE_SUPABASE:
        return pd.DataFrame(), "SUPABASE DESACTIVADO"
    try:
        sb = get_sb_client()
        cols = "ID_Municipio,id_georef,Muni_Nombre,Muni_Poblacion_2022,Muni_Superficie,Muni_Cantidad_Trabajadores"
        res = sb.table("bd_municipios").select(cols).limit(200).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty:
            df["id_georef"] = df["id_georef"].apply(norm_georef)
            for col in ["Muni_Poblacion_2022", "Muni_Superficie", "Muni_Cantidad_Trabajadores"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=300)
def fetch_documentos_count() -> Tuple[pd.DataFrame, Optional[str]]:
    """Cuenta documentos por municipio."""
    if not USE_SUPABASE:
        return pd.DataFrame(), "SUPABASE DESACTIVADO"
    try:
        sb = get_sb_client()
        res = sb.table("BD_DocumentosCargados").select("ID_Municipio").limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if df.empty:
            return pd.DataFrame(columns=["ID_Municipio", "documentos_cargados"]), None
        counts = df.groupby("ID_Municipio").size().reset_index(name="documentos_cargados")
        return counts, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=300)
def fetch_metricas_por_municipio() -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene métricas agregadas por municipio para el mapa."""
    if not USE_SUPABASE:
        return pd.DataFrame(), "SUPABASE DESACTIVADO"
    try:
        sb = get_sb_client()

        # Obtener todos los documentos con su municipio Y período
        res_docs = sb.table("BD_DocumentosCargados").select("ID_DocumentoCargado,ID_Municipio,Doc_Periodo").limit(50000).execute()
        _raise_if_error(res_docs)
        df_docs = pd.DataFrame(res_docs.data or [])

        if df_docs.empty:
            return pd.DataFrame(), None

        doc_to_muni = dict(zip(df_docs["ID_DocumentoCargado"].astype(str), df_docs["ID_Municipio"].astype(str)))
        doc_to_periodo = dict(zip(df_docs["ID_DocumentoCargado"].astype(str), df_docs["Doc_Periodo"]))
        doc_ids = list(doc_to_muni.keys())

        # Obtener recursos agregados (incluir Rec_Nombre para cálculo correcto)
        res_rec = sb.table("bd_recursos").select("ID_DocumentoCargado,Rec_Nombre,Rec_Vigente,Rec_Devengado,Rec_Percibido").limit(100000).execute()
        _raise_if_error(res_rec)
        df_rec = pd.DataFrame(res_rec.data or [])

        # Obtener gastos agregados
        res_gas = sb.table("bd_gastos").select("ID_DocumentoCargado,Gasto_Vigente,Gasto_Devengado,Gasto_Pagado").limit(100000).execute()
        _raise_if_error(res_gas)
        df_gas = pd.DataFrame(res_gas.data or [])

        # Procesar recursos con fórmula especial para Total Percibido
        # Fórmula: (suma de categorías principales / 2) + Extrapresupuestario
        metrics = {}
        if not df_rec.empty:
            df_rec["ID_Municipio"] = df_rec["ID_DocumentoCargado"].astype(str).map(doc_to_muni)
            df_rec["Doc_Periodo"] = df_rec["ID_DocumentoCargado"].astype(str).map(doc_to_periodo)
            df_rec["Factor_Proy"] = df_rec["Doc_Periodo"].apply(get_factor_proyeccion)

            for col in ["Rec_Vigente", "Rec_Devengado", "Rec_Percibido"]:
                if col in df_rec.columns:
                    df_rec[col] = pd.to_numeric(df_rec[col], errors="coerce").fillna(0)

            # Calcular valores proyectados (Rec_Devengado y Rec_Percibido se proyectan)
            df_rec["Rec_Devengado_Proy"] = df_rec["Rec_Devengado"] * df_rec["Factor_Proy"]
            df_rec["Rec_Percibido_Proy"] = df_rec["Rec_Percibido"] * df_rec["Factor_Proy"]

            # Normalizar Rec_Nombre para manejar variaciones
            df_rec["Rec_Nombre_Norm"] = df_rec["Rec_Nombre"].str.strip().str.lower()

            # Categorías principales (se dividen por 2)
            categorias_principales = [
                "recursos de capital",
                "ingresos corrientes",
                "fuentes financieras",
                "de libre disponibilidad",
                "afectados"
            ]

            # Extrapresupuestario (se suma completo)
            categorias_extra = ["extrapresupuestario", "extrapresupuestarios"]

            # Separar registros por tipo
            df_principales = df_rec[df_rec["Rec_Nombre_Norm"].isin(categorias_principales)]
            df_extra = df_rec[df_rec["Rec_Nombre_Norm"].isin(categorias_extra)]

            # Agregar por municipio - categorías principales
            if not df_principales.empty:
                principales_agg = df_principales.groupby("ID_Municipio").agg({
                    "Rec_Vigente": "sum",
                    "Rec_Devengado": "sum",
                    "Rec_Percibido": "sum",
                    "Rec_Devengado_Proy": "sum",
                    "Rec_Percibido_Proy": "sum"
                }).reset_index()
            else:
                principales_agg = pd.DataFrame(columns=["ID_Municipio", "Rec_Vigente", "Rec_Devengado", "Rec_Percibido", "Rec_Devengado_Proy", "Rec_Percibido_Proy"])

            # Agregar por municipio - extrapresupuestario
            if not df_extra.empty:
                extra_agg = df_extra.groupby("ID_Municipio").agg({
                    "Rec_Vigente": "sum",
                    "Rec_Devengado": "sum",
                    "Rec_Percibido": "sum",
                    "Rec_Devengado_Proy": "sum",
                    "Rec_Percibido_Proy": "sum"
                }).reset_index()
                extra_agg.columns = ["ID_Municipio", "Extra_Vigente", "Extra_Devengado", "Extra_Percibido", "Extra_Devengado_Proy", "Extra_Percibido_Proy"]
            else:
                extra_agg = pd.DataFrame(columns=["ID_Municipio", "Extra_Vigente", "Extra_Devengado", "Extra_Percibido", "Extra_Devengado_Proy", "Extra_Percibido_Proy"])

            # Combinar y aplicar fórmula
            rec_agg = principales_agg.merge(extra_agg, on="ID_Municipio", how="outer").fillna(0)

            # Fórmula: (principales / 2) + extra - Valores actuales
            rec_agg["recursos_vigente"] = (rec_agg["Rec_Vigente"] / 2) + rec_agg.get("Extra_Vigente", 0)
            rec_agg["recursos_devengado"] = (rec_agg["Rec_Devengado"] / 2) + rec_agg.get("Extra_Devengado", 0)
            rec_agg["recursos_percibido"] = (rec_agg["Rec_Percibido"] / 2) + rec_agg.get("Extra_Percibido", 0)

            # Fórmula: (principales / 2) + extra - Valores proyectados
            rec_agg["recursos_devengado_proy"] = (rec_agg["Rec_Devengado_Proy"] / 2) + rec_agg.get("Extra_Devengado_Proy", 0)
            rec_agg["recursos_percibido_proy"] = (rec_agg["Rec_Percibido_Proy"] / 2) + rec_agg.get("Extra_Percibido_Proy", 0)

            rec_agg = rec_agg[["ID_Municipio", "recursos_vigente", "recursos_devengado", "recursos_percibido", "recursos_devengado_proy", "recursos_percibido_proy"]]

            for _, row in rec_agg.iterrows():
                mid = row["ID_Municipio"]
                metrics[mid] = metrics.get(mid, {})
                # Excluir ID_Municipio del dict para evitar duplicados
                row_dict = {k: v for k, v in row.to_dict().items() if k != "ID_Municipio"}
                metrics[mid].update(row_dict)

        # Procesar gastos
        if not df_gas.empty:
            df_gas["ID_Municipio"] = df_gas["ID_DocumentoCargado"].astype(str).map(doc_to_muni)
            df_gas["Doc_Periodo"] = df_gas["ID_DocumentoCargado"].astype(str).map(doc_to_periodo)
            df_gas["Factor_Proy"] = df_gas["Doc_Periodo"].apply(get_factor_proyeccion)

            for col in ["Gasto_Vigente", "Gasto_Devengado", "Gasto_Pagado"]:
                if col in df_gas.columns:
                    df_gas[col] = pd.to_numeric(df_gas[col], errors="coerce").fillna(0)

            # Calcular valores proyectados
            df_gas["Gasto_Devengado_Proy"] = df_gas["Gasto_Devengado"] * df_gas["Factor_Proy"]
            df_gas["Gasto_Pagado_Proy"] = df_gas["Gasto_Pagado"] * df_gas["Factor_Proy"]

            gas_agg = df_gas.groupby("ID_Municipio").agg({
                "Gasto_Vigente": "sum",
                "Gasto_Devengado": "sum",
                "Gasto_Pagado": "sum",
                "Gasto_Devengado_Proy": "sum",
                "Gasto_Pagado_Proy": "sum"
            }).reset_index()
            gas_agg.columns = ["ID_Municipio", "gastos_vigente", "gastos_devengado", "gastos_pagado", "gastos_devengado_proy", "gastos_pagado_proy"]

            for _, row in gas_agg.iterrows():
                mid = row["ID_Municipio"]
                metrics[mid] = metrics.get(mid, {})
                # Excluir ID_Municipio del dict para evitar duplicados
                row_dict = {k: v for k, v in row.to_dict().items() if k != "ID_Municipio"}
                metrics[mid].update(row_dict)

        # Convertir a DataFrame
        df_metrics = pd.DataFrame.from_dict(metrics, orient="index").reset_index()
        df_metrics.columns = ["ID_Municipio"] + list(df_metrics.columns[1:])

        # Calcular métricas derivadas
        if "recursos_percibido" in df_metrics.columns and "gastos_pagado" in df_metrics.columns:
            df_metrics["balance_fiscal"] = df_metrics["recursos_percibido"].fillna(0) - df_metrics["gastos_pagado"].fillna(0)

        # Balance fiscal proyectado
        if "recursos_percibido_proy" in df_metrics.columns and "gastos_pagado_proy" in df_metrics.columns:
            df_metrics["balance_fiscal_proy"] = df_metrics["recursos_percibido_proy"].fillna(0) - df_metrics["gastos_pagado_proy"].fillna(0)

        if "gastos_devengado" in df_metrics.columns and "gastos_vigente" in df_metrics.columns:
            df_metrics["tasa_ejecucion"] = df_metrics.apply(
                lambda r: r["gastos_devengado"] / r["gastos_vigente"] if r["gastos_vigente"] > 0 else None,
                axis=1
            )

        # Tasa de ejecución proyectada
        if "gastos_devengado_proy" in df_metrics.columns and "gastos_vigente" in df_metrics.columns:
            df_metrics["tasa_ejecucion_proy"] = df_metrics.apply(
                lambda r: r["gastos_devengado_proy"] / r["gastos_vigente"] if r["gastos_vigente"] > 0 else None,
                axis=1
            )

        return df_metrics, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_documentos_muni(id_municipio: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene documentos de un municipio específico."""
    if not USE_SUPABASE or not id_municipio:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin municipio"
    try:
        sb = get_sb_client()
        cols = "ID_DocumentoCargado,Doc_Nombre,Doc_Tipo,Doc_Periodo,Doc_Anio,Doc_FechaCarga"
        res = (
            sb.table("BD_DocumentosCargados")
            .select(cols)
            .eq("ID_Municipio", id_municipio)
            .order("Doc_FechaCarga", desc=True)
            .limit(100)
            .execute()
        )
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty and "Doc_FechaCarga" in df.columns:
            df["Doc_FechaCarga"] = pd.to_datetime(df["Doc_FechaCarga"], errors="coerce")
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_gastos(doc_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    if not USE_SUPABASE or not doc_id:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin doc"
    try:
        sb = get_sb_client()
        cols = "Gasto_Objeto,Gasto_Categoria,Gasto_Vigente,Gasto_Preventivo,Gasto_Compromiso,Gasto_Devengado,Gasto_Pagado"
        res = sb.table("bd_gastos").select(cols).eq("ID_DocumentoCargado", doc_id).limit(50000).execute()
        _raise_if_error(res)
        return pd.DataFrame(res.data or []), None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_recursos(doc_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    if not USE_SUPABASE or not doc_id:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin doc"
    try:
        sb = get_sb_client()
        cols = "Rec_Nombre,Rec_Categoria,Rec_Vigente,Rec_Devengado,Rec_Percibido,Rec_Tipo"
        res = sb.table("bd_recursos").select(cols).eq("ID_DocumentoCargado", doc_id).limit(50000).execute()
        _raise_if_error(res)
        return pd.DataFrame(res.data or []), None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=300)
def fetch_jurisdicciones_provinciales() -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene todas las jurisdicciones con conteo de programas."""
    if not USE_SUPABASE:
        return pd.DataFrame(), "SUPABASE DESACTIVADO"
    try:
        sb = get_sb_client()
        # Obtener jurisdicciones
        res_juri = sb.table("bd_jurisdiccion").select("ID_Jurisdiccion,ID_DocumentoCargado,Juri_Codigo,Juri_Nombre").limit(50000).execute()
        _raise_if_error(res_juri)
        df_juri = pd.DataFrame(res_juri.data or [])

        if df_juri.empty:
            return pd.DataFrame(), None

        # Obtener programas para contar por jurisdicción
        res_prog = sb.table("bd_programas").select("ID_Programa,ID_Jurisdiccion,Prog_Codigo,Prog_Nombre,Prog_Vigente,Prog_Devengado,Prog_Pagado,Prog_TieneMetas").limit(100000).execute()
        _raise_if_error(res_prog)
        df_prog = pd.DataFrame(res_prog.data or [])

        # Contar programas por jurisdicción
        if not df_prog.empty:
            prog_count = df_prog.groupby("ID_Jurisdiccion").size().reset_index(name="cantidad_programas")
            prog_sums = df_prog.groupby("ID_Jurisdiccion").agg({
                "Prog_Vigente": "sum",
                "Prog_Devengado": "sum",
                "Prog_Pagado": "sum"
            }).reset_index()
            prog_sums.columns = ["ID_Jurisdiccion", "total_vigente", "total_devengado", "total_pagado"]

            df_juri = df_juri.merge(prog_count, on="ID_Jurisdiccion", how="left")
            df_juri = df_juri.merge(prog_sums, on="ID_Jurisdiccion", how="left")
            df_juri["cantidad_programas"] = df_juri["cantidad_programas"].fillna(0).astype(int)
            for col in ["total_vigente", "total_devengado", "total_pagado"]:
                df_juri[col] = pd.to_numeric(df_juri[col], errors="coerce").fillna(0)

        return df_juri, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=300)
def fetch_programas_provinciales() -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene todos los programas con conteo de metas."""
    if not USE_SUPABASE:
        return pd.DataFrame(), "SUPABASE DESACTIVADO"
    try:
        sb = get_sb_client()
        # Obtener programas
        res_prog = sb.table("bd_programas").select("ID_Programa,ID_Jurisdiccion,Prog_Codigo,Prog_Nombre,Prog_Vigente,Prog_Devengado,Prog_Pagado,Prog_TieneMetas").limit(100000).execute()
        _raise_if_error(res_prog)
        df_prog = pd.DataFrame(res_prog.data or [])

        if df_prog.empty:
            return pd.DataFrame(), None

        # Convertir valores numéricos
        for col in ["Prog_Vigente", "Prog_Devengado", "Prog_Pagado"]:
            if col in df_prog.columns:
                df_prog[col] = pd.to_numeric(df_prog[col], errors="coerce").fillna(0)

        # Obtener metas para contar por programa
        res_metas = sb.table("bd_metas").select("ID_Meta,ID_Programa,Meta_Nombre").limit(100000).execute()
        _raise_if_error(res_metas)
        df_metas = pd.DataFrame(res_metas.data or [])

        # Contar metas por programa
        if not df_metas.empty:
            metas_count = df_metas.groupby("ID_Programa").size().reset_index(name="cantidad_metas")
            df_prog = df_prog.merge(metas_count, on="ID_Programa", how="left")
            df_prog["cantidad_metas"] = df_prog["cantidad_metas"].fillna(0).astype(int)
        else:
            df_prog["cantidad_metas"] = 0

        return df_prog, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=300)
def fetch_metas_provinciales() -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene todas las metas."""
    if not USE_SUPABASE:
        return pd.DataFrame(), "SUPABASE DESACTIVADO"
    try:
        sb = get_sb_client()
        res = sb.table("bd_metas").select("ID_Meta,ID_Programa,Meta_Nombre,Meta_Unidad,Meta_Anual,Meta_Parcial,Meta_Ejecutado").limit(100000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])

        if not df.empty:
            for col in ["Meta_Anual", "Meta_Parcial", "Meta_Ejecutado"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_tesoreria(doc_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene movimientos de tesorería para un documento."""
    if not USE_SUPABASE or not doc_id:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin doc"
    try:
        sb = get_sb_client()
        res = sb.table("bd_movimientosTesoreria").select("MovTes_TipoResumido,MovTes_Importe").eq("ID_DocumentoCargado", doc_id).limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty and "MovTes_Importe" in df.columns:
            df["MovTes_Importe"] = pd.to_numeric(df["MovTes_Importe"], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_cuentas(doc_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene cuentas para un documento."""
    if not USE_SUPABASE or not doc_id:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin doc"
    try:
        sb = get_sb_client()
        res = sb.table("bd_cuentas").select("Cuenta_Nombre,Cuenta_Importe").eq("ID_DocumentoCargado", doc_id).limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty and "Cuenta_Importe" in df.columns:
            df["Cuenta_Importe"] = pd.to_numeric(df["Cuenta_Importe"], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_situacion_patrimonial(doc_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene situación patrimonial para un documento."""
    if not USE_SUPABASE or not doc_id:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin doc"
    try:
        sb = get_sb_client()
        res = sb.table("bd_situacionpatrimonial").select("SitPat_Tipo,SitPat_Saldo,SitPat_Nombre").eq("ID_DocumentoCargado", doc_id).limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty and "SitPat_Saldo" in df.columns:
            df["SitPat_Saldo"] = pd.to_numeric(df["SitPat_Saldo"], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_jurisdicciones_doc(doc_id: str) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene jurisdicciones para un documento específico."""
    if not USE_SUPABASE or not doc_id:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin doc"
    try:
        sb = get_sb_client()
        res = sb.table("bd_jurisdiccion").select("ID_Jurisdiccion,Juri_Codigo,Juri_Nombre").eq("ID_DocumentoCargado", doc_id).limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_programas_doc(juri_ids: tuple) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene programas para una lista de jurisdicciones."""
    if not USE_SUPABASE or not juri_ids:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin jurisdicciones"
    try:
        sb = get_sb_client()
        res = sb.table("bd_programas").select("ID_Programa,ID_Jurisdiccion,Prog_Codigo,Prog_Nombre,Prog_Vigente,Prog_Devengado,Prog_Pagado,Prog_TieneMetas").in_("ID_Jurisdiccion", list(juri_ids)).limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty:
            for col in ["Prog_Vigente", "Prog_Devengado", "Prog_Pagado"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


@st.cache_data(show_spinner=False, ttl=60)
def fetch_metas_doc(prog_ids: tuple) -> Tuple[pd.DataFrame, Optional[str]]:
    """Obtiene metas para una lista de programas."""
    if not USE_SUPABASE or not prog_ids:
        return pd.DataFrame(), "SUPABASE DESACTIVADO o sin programas"
    try:
        sb = get_sb_client()
        res = sb.table("bd_metas").select("ID_Meta,ID_Programa,Meta_Nombre,Meta_Unidad,Meta_Anual,Meta_Parcial,Meta_Ejecutado").in_("ID_Programa", list(prog_ids)).limit(50000).execute()
        _raise_if_error(res)
        df = pd.DataFrame(res.data or [])
        if not df.empty:
            for col in ["Meta_Anual", "Meta_Parcial", "Meta_Ejecutado"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df, None
    except Exception as e:
        return pd.DataFrame(), repr(e)


# ======================================================
# MAPA PLOTLY CHOROPLETH
# ======================================================
def create_choropleth_map(
    geo: Dict,
    df_metrics: pd.DataFrame,
    df_base: pd.DataFrame,
    metric: str = "recursos_percibido",
    normalization: str = "absoluto",
    selected_georef: Optional[str] = None
) -> go.Figure:
    """Crea mapa choropleth interactivo de municipios PBA."""

    metric_config = MAP_METRICS.get(metric, MAP_METRICS["recursos_percibido"])

    # Crear lookup de métricas por ID_Municipio
    metrics_lookup = {}
    if df_metrics is not None and not df_metrics.empty:
        metrics_lookup = df_metrics.set_index("ID_Municipio").to_dict(orient="index")

    # Crear lookup de base por id_georef
    base_lookup = {}
    georef_to_muni_id = {}
    docs_lookup = {}
    if df_base is not None and not df_base.empty:
        base_lookup = df_base.set_index("id_georef").to_dict(orient="index")
        georef_to_muni_id = dict(zip(df_base["id_georef"].astype(str), df_base["ID_Municipio"].astype(str)))
        if "documentos_cargados" in df_base.columns:
            docs_lookup = dict(zip(df_base["id_georef"].astype(str), df_base["documentos_cargados"]))

    # Preparar datos para el mapa
    map_data = []

    for i, feat in enumerate(geo.get("features", [])):
        props = feat.get("properties", {})
        gid = props.get("id_georef", "")

        # Obtener datos base
        base_data = base_lookup.get(gid, {})
        muni_id = georef_to_muni_id.get(gid)
        muni_nombre = base_data.get("Muni_Nombre") or props.get("Muni_Nombre") or "—"
        poblacion = float(base_data.get("Muni_Poblacion_2022", 0) or 0)
        superficie = float(base_data.get("Muni_Superficie", 0) or 0)
        tiene_datos = int(docs_lookup.get(gid, 0)) > 0

        # Obtener métricas
        muni_metrics = metrics_lookup.get(muni_id, {}) if muni_id else {}

        # Obtener valor de la métrica
        col = metric_config["column"]

        # Si es modo proyectado, usar columna proyectada si existe
        if normalization == "proyectado":
            col_proy = col + "_proy"
            # Las métricas que tienen versión proyectada
            if col_proy in muni_metrics:
                col = col_proy

        if col in ["Muni_Poblacion_2022", "Muni_Cantidad_Trabajadores"]:
            raw_value = float(base_data.get(col, 0) or 0)
        else:
            raw_value = float(muni_metrics.get(col, 0) or 0)

        # Aplicar normalización
        if normalization == "per_capita" and poblacion > 0:
            display_value = raw_value / poblacion
        elif normalization == "por_km2" and superficie > 0:
            display_value = raw_value / superficie
        elif normalization == "proyectado":
            display_value = raw_value  # Ya está proyectado
        else:
            display_value = raw_value

        map_data.append({
            "idx": i,
            "id_georef": gid,
            "Muni_Nombre": muni_nombre,
            "value": display_value,
            "tiene_datos": tiene_datos,
            "is_selected": gid == selected_georef,
            "poblacion": poblacion
        })

    df_map = pd.DataFrame(map_data)

    # Escala de colores: Rojo (alto) a Azul (bajo)
    # RdBu va de rojo a azul, pero queremos rojo=alto, así que usamos RdBu_r invertido
    # O mejor: usamos RdYlBu_r que va de rojo (alto) -> amarillo -> azul (bajo)
    colorscale = "RdYlBu_r"  # Rojo=alto, Azul=bajo

    # Crear figura con choropleth
    fig = go.Figure()

    # Agregar capa de polígonos coloreados por valor
    for i, feat in enumerate(geo.get("features", [])):
        props = feat.get("properties", {})
        gid = props.get("id_georef", "")

        row = df_map[df_map["id_georef"] == gid]
        if row.empty:
            continue

        row_data = row.iloc[0]
        value = row_data["value"]
        nombre = row_data["Muni_Nombre"]
        tiene_datos = row_data["tiene_datos"]
        is_selected = row_data["is_selected"]

        # Obtener coordenadas del polígono
        geom = feat.get("geometry", {})
        geom_type = geom.get("type", "")
        coords = geom.get("coordinates", [])

        if not coords:
            continue

        # Extraer polígonos (manejar Polygon y MultiPolygon)
        polygons = []
        if geom_type == "Polygon":
            polygons = [coords[0]]  # Exterior ring
        elif geom_type == "MultiPolygon":
            for poly in coords:
                polygons.append(poly[0])  # Exterior ring de cada polígono

        for poly_coords in polygons:
            lons = [p[0] for p in poly_coords]
            lats = [p[1] for p in poly_coords]

            # Determinar color
            if is_selected:
                fillcolor = "rgba(255, 215, 0, 0.8)"  # Dorado para seleccionado
                linecolor = "gold"
                linewidth = 3
            elif not tiene_datos:
                fillcolor = "rgba(200, 200, 200, 0.5)"  # Gris para sin datos
                linecolor = "gray"
                linewidth = 1
            else:
                # Color basado en valor - normalizar al rango
                fillcolor = None  # Se asignará después
                linecolor = "white"
                linewidth = 1

            fig.add_trace(go.Scattermapbox(
                lon=lons,
                lat=lats,
                mode="lines",
                fill="toself",
                fillcolor=fillcolor if fillcolor else "rgba(100, 100, 100, 0.5)",
                line=dict(color=linecolor, width=linewidth),
                name=nombre,
                hoverinfo="text",
                hovertext=f"<b>{nombre}</b><br>Valor: {value:,.0f}" if value else f"<b>{nombre}</b><br>Sin datos",
                showlegend=False
            ))

    # Calcular centroides para los valores coloreados
    min_val = df_map[df_map["tiene_datos"]]["value"].min() if len(df_map[df_map["tiene_datos"]]) > 0 else 0
    max_val = df_map[df_map["tiene_datos"]]["value"].max() if len(df_map[df_map["tiene_datos"]]) > 0 else 1

    # Agregar puntos coloreados en los centroides para mostrar la escala de colores
    centroids_lat = []
    centroids_lon = []
    centroids_val = []
    centroids_text = []

    for i, feat in enumerate(geo.get("features", [])):
        props = feat.get("properties", {})
        gid = props.get("id_georef", "")

        row = df_map[df_map["id_georef"] == gid]
        if row.empty:
            continue

        row_data = row.iloc[0]
        if not row_data["tiene_datos"]:
            continue

        # Calcular centroide
        geom = feat.get("geometry", {})
        geom_type = geom.get("type", "")
        coords = geom.get("coordinates", [])

        if geom_type == "Polygon":
            pts = coords[0]
        elif geom_type == "MultiPolygon":
            pts = coords[0][0]
        else:
            continue

        if pts:
            center_lon = sum(p[0] for p in pts) / len(pts)
            center_lat = sum(p[1] for p in pts) / len(pts)

            centroids_lat.append(center_lat)
            centroids_lon.append(center_lon)
            centroids_val.append(row_data["value"])
            centroids_text.append(f"<b>{row_data['Muni_Nombre']}</b><br>Valor: {row_data['value']:,.0f}")

    # Agregar capa de puntos coloreados por valor
    if centroids_val:
        fig.add_trace(go.Scattermapbox(
            lat=centroids_lat,
            lon=centroids_lon,
            mode="markers",
            marker=dict(
                size=15,
                color=centroids_val,
                colorscale=colorscale,
                cmin=min_val,
                cmax=max_val,
                colorbar=dict(
                    title=metric_config["label"] + NORMALIZATIONS[normalization]["suffix"],
                    tickformat=".2s" if metric_config["format"] == "money" else ".1%" if metric_config["format"] == "percent" else ","
                ),
                opacity=0.9
            ),
            text=centroids_text,
            hoverinfo="text",
            name="Valores",
            showlegend=False
        ))

    # Marcador especial para municipio seleccionado
    if selected_georef:
        sel_row = df_map[df_map["id_georef"] == selected_georef]
        if not sel_row.empty:
            # Buscar el centroide del seleccionado
            for feat in geo.get("features", []):
                if feat.get("properties", {}).get("id_georef") == selected_georef:
                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if geom.get("type") == "Polygon":
                        pts = coords[0]
                    elif geom.get("type") == "MultiPolygon":
                        pts = coords[0][0]
                    else:
                        pts = []

                    if pts:
                        center_lon = sum(p[0] for p in pts) / len(pts)
                        center_lat = sum(p[1] for p in pts) / len(pts)

                        fig.add_trace(go.Scattermapbox(
                            lat=[center_lat],
                            lon=[center_lon],
                            mode="markers",
                            marker=dict(size=25, color="gold", symbol="star"),
                            name=sel_row.iloc[0]["Muni_Nombre"],
                            hoverinfo="name",
                            showlegend=False
                        ))
                    break

    # Configurar layout del mapa
    fig.update_layout(
        mapbox=dict(
            style="carto-positron",
            center={"lat": -36.5, "lon": -59.5},
            zoom=5.5
        ),
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=550,
        showlegend=False
    )

    return fig


# ======================================================
# COMPONENTES UI
# ======================================================
def render_sidebar_controls(df_base: pd.DataFrame):
    """Renderiza controles en el sidebar."""
    init_session_state()  # Asegurar estado
    st.sidebar.title("Configuración")

    # Selector de métrica
    st.sidebar.subheader("Métrica del Mapa")
    metric_options = {v["label"]: k for k, v in MAP_METRICS.items()}
    current_label = MAP_METRICS[st.session_state.get("map_metric", "recursos_percibido")]["label"]
    selected_label = st.sidebar.selectbox(
        "Visualizar:",
        options=list(metric_options.keys()),
        index=list(metric_options.keys()).index(current_label) if current_label in metric_options else 0
    )
    st.session_state.map_metric = metric_options[selected_label]

    # Descripción de la métrica
    st.sidebar.caption(MAP_METRICS[st.session_state.map_metric]["description"])

    # Selector de normalización
    st.sidebar.subheader("Normalización")
    norm_options = {v["label"]: k for k, v in NORMALIZATIONS.items()}
    current_norm_label = NORMALIZATIONS[st.session_state.get("map_normalization", "absoluto")]["label"]
    selected_norm = st.sidebar.radio(
        "Expresar valores:",
        options=list(norm_options.keys()),
        index=list(norm_options.keys()).index(current_norm_label) if current_norm_label in norm_options else 0
    )
    st.session_state.map_normalization = norm_options[selected_norm]

    st.sidebar.divider()

    # Selector de municipio
    st.sidebar.subheader("Seleccionar Municipio")

    if df_base is not None and not df_base.empty:
        # Crear lista con indicadores de color según si tienen datos cargados
        # 🟢 = tiene documentos cargados, ⚪ = sin datos
        df_sorted = df_base.sort_values("Muni_Nombre")

        opciones_display = ["— Vista Provincial —"]
        opciones_nombre = ["— Vista Provincial —"]

        for _, row in df_sorted.iterrows():
            nombre = row["Muni_Nombre"]
            tiene_datos = int(row.get("documentos_cargados", 0)) > 0
            indicador = "🟢" if tiene_datos else "⚪"
            opciones_display.append(f"{indicador} {nombre}")
            opciones_nombre.append(nombre)

        # Encontrar índice actual
        current_idx = 0
        if st.session_state.municipio_nombre and st.session_state.municipio_nombre in opciones_nombre:
            current_idx = opciones_nombre.index(st.session_state.municipio_nombre)

        selected_display = st.sidebar.selectbox(
            "Municipio:",
            options=opciones_display,
            index=current_idx
        )

        # Leyenda
        st.sidebar.caption("🟢 Con datos cargados  ⚪ Sin datos")

        # Obtener el nombre real (sin el indicador)
        selected_idx = opciones_display.index(selected_display)
        selected_name = opciones_nombre[selected_idx]

        if selected_name == "— Vista Provincial —":
            if st.session_state.municipio_sel is not None:
                reset_to_provincial()
                st.rerun()
        else:
            row = df_base[df_base["Muni_Nombre"] == selected_name]
            if not row.empty:
                r = row.iloc[0]
                new_id = str(r["ID_Municipio"])
                new_georef = str(r["id_georef"])
                if st.session_state.municipio_sel != new_id:
                    select_municipio(new_id, selected_name, new_georef)
                    st.rerun()

    # Botón para volver a provincial
    if st.session_state.municipio_sel is not None:
        st.sidebar.divider()
        if st.sidebar.button("↩ Volver a Vista Provincial", type="primary", use_container_width=True):
            reset_to_provincial()
            st.rerun()


def render_provincial_kpis(df_base: pd.DataFrame, df_metrics: pd.DataFrame, df_docs_count: pd.DataFrame):
    """Renderiza KPIs de la vista provincial."""
    st.subheader("📊 Indicadores Provinciales")

    # Calcular totales provinciales
    total_poblacion = df_base["Muni_Poblacion_2022"].sum() if "Muni_Poblacion_2022" in df_base.columns else 0
    total_superficie = df_base["Muni_Superficie"].sum() if "Muni_Superficie" in df_base.columns else 0
    total_trabajadores = df_base["Muni_Cantidad_Trabajadores"].sum() if "Muni_Cantidad_Trabajadores" in df_base.columns else 0
    total_municipios = len(df_base)

    # Métricas fiscales provinciales
    total_recursos = df_metrics["recursos_percibido"].sum() if "recursos_percibido" in df_metrics.columns else 0
    total_gastos = df_metrics["gastos_pagado"].sum() if "gastos_pagado" in df_metrics.columns else 0
    total_balance = total_recursos - total_gastos

    # Tasa de ejecución promedio ponderada
    total_devengado = df_metrics["gastos_devengado"].sum() if "gastos_devengado" in df_metrics.columns else 0
    total_vigente = df_metrics["gastos_vigente"].sum() if "gastos_vigente" in df_metrics.columns else 0
    tasa_ejecucion_prov = total_devengado / total_vigente if total_vigente > 0 else None

    # Municipios con datos
    munis_con_docs = df_docs_count["ID_Municipio"].nunique() if not df_docs_count.empty else 0

    # Fila 1: KPIs principales
    cols = st.columns(5)

    with cols[0]:
        st.metric(
            "Total Recursos",
            fmt_money_millions(total_recursos),
            help="Suma de recursos percibidos de todos los municipios"
        )

    with cols[1]:
        st.metric(
            "Total Gastos",
            fmt_money_millions(total_gastos),
            help="Suma de gastos pagados de todos los municipios"
        )

    with cols[2]:
        color, emoji, estado = get_semaforo_balance(total_balance)
        st.metric(
            f"{emoji} Balance Provincial",
            fmt_money_millions(total_balance),
            delta=estado,
            delta_color="off"
        )

    with cols[3]:
        color, emoji, estado = get_semaforo_ejecucion(tasa_ejecucion_prov)
        st.metric(
            f"{emoji} Tasa Ejecución",
            fmt_pct0(tasa_ejecucion_prov),
            delta=estado,
            delta_color="off"
        )

    with cols[4]:
        st.metric(
            "Municipios con Datos",
            f"{munis_con_docs} / {total_municipios}",
            help="Municipios que tienen al menos un documento cargado"
        )

    # Fila 2: Datos demográficos
    st.caption("**Datos Provinciales Agregados**")
    cols2 = st.columns(4)

    with cols2[0]:
        st.metric("Población Total", fmt_num(total_poblacion))

    with cols2[1]:
        st.metric("Superficie Total", f"{fmt_num(total_superficie)} km²")

    with cols2[2]:
        st.metric("Trabajadores Municipales", fmt_num(total_trabajadores))

    with cols2[3]:
        gasto_per_capita = total_gastos / total_poblacion if total_poblacion > 0 else 0
        st.metric("Gasto Per Cápita Prov.", f"$ {fmt_num(gasto_per_capita)}")


def render_municipio_kpis(
    df_base: pd.DataFrame,
    df_metrics: pd.DataFrame,
    muni_id: str,
    muni_nombre: str
):
    """Renderiza KPIs del municipio seleccionado con comparación provincial."""
    st.subheader(f"📊 Indicadores de {muni_nombre}")

    # Obtener datos del municipio
    row_base = df_base[df_base["ID_Municipio"].astype(str) == str(muni_id)]
    row_metrics = df_metrics[df_metrics["ID_Municipio"].astype(str) == str(muni_id)] if not df_metrics.empty else pd.DataFrame()

    if row_base.empty:
        st.warning("No se encontraron datos base del municipio.")
        return

    muni_data = row_base.iloc[0].to_dict()
    muni_metrics = row_metrics.iloc[0].to_dict() if not row_metrics.empty else {}

    # Totales provinciales para comparación
    total_recursos_prov = df_metrics["recursos_percibido"].sum() if "recursos_percibido" in df_metrics.columns else 0
    total_gastos_prov = df_metrics["gastos_pagado"].sum() if "gastos_pagado" in df_metrics.columns else 0
    total_poblacion_prov = df_base["Muni_Poblacion_2022"].sum() if "Muni_Poblacion_2022" in df_base.columns else 0

    # Datos del municipio
    poblacion_muni = float(muni_data.get("Muni_Poblacion_2022", 0) or 0)
    superficie_muni = float(muni_data.get("Muni_Superficie", 0) or 0)
    recursos_muni = float(muni_metrics.get("recursos_percibido", 0) or 0)
    gastos_muni = float(muni_metrics.get("gastos_pagado", 0) or 0)
    balance_muni = recursos_muni - gastos_muni

    # Tasas
    tasa_ejec_muni = muni_metrics.get("tasa_ejecucion")

    # Porcentajes del total provincial
    pct_recursos = (recursos_muni / total_recursos_prov * 100) if total_recursos_prov > 0 else 0
    pct_gastos = (gastos_muni / total_gastos_prov * 100) if total_gastos_prov > 0 else 0
    pct_poblacion = (poblacion_muni / total_poblacion_prov * 100) if total_poblacion_prov > 0 else 0

    # Fila 1: KPIs principales con comparación
    cols = st.columns(5)

    with cols[0]:
        st.metric(
            "Recursos Percibidos",
            fmt_money_millions(recursos_muni),
            delta=f"{pct_recursos:.1f}% del total prov.",
            delta_color="off"
        )

    with cols[1]:
        st.metric(
            "Gastos Pagados",
            fmt_money_millions(gastos_muni),
            delta=f"{pct_gastos:.1f}% del total prov.",
            delta_color="off"
        )

    with cols[2]:
        color, emoji, estado = get_semaforo_balance(balance_muni)
        st.metric(
            f"{emoji} Balance Fiscal",
            fmt_money_millions(balance_muni),
            delta=estado,
            delta_color="off"
        )

    with cols[3]:
        color, emoji, estado = get_semaforo_ejecucion(tasa_ejec_muni)
        st.metric(
            f"{emoji} Tasa Ejecución",
            fmt_pct0(tasa_ejec_muni) if tasa_ejec_muni else "—",
            delta=estado,
            delta_color="off"
        )

    with cols[4]:
        st.metric(
            "Población",
            fmt_num(poblacion_muni),
            delta=f"{pct_poblacion:.1f}% de PBA",
            delta_color="off"
        )

    # Fila 2: Per cápita
    st.caption("**Indicadores Per Cápita**")
    cols2 = st.columns(4)

    gasto_pc = gastos_muni / poblacion_muni if poblacion_muni > 0 else 0
    recursos_pc = recursos_muni / poblacion_muni if poblacion_muni > 0 else 0

    # Promedio provincial per cápita
    gasto_pc_prov = total_gastos_prov / total_poblacion_prov if total_poblacion_prov > 0 else 0
    recursos_pc_prov = total_recursos_prov / total_poblacion_prov if total_poblacion_prov > 0 else 0

    with cols2[0]:
        diff = ((gasto_pc / gasto_pc_prov) - 1) * 100 if gasto_pc_prov > 0 else 0
        st.metric(
            "Gasto Per Cápita",
            f"$ {fmt_num(gasto_pc)}",
            delta=f"{diff:+.1f}% vs prom. prov." if gasto_pc_prov > 0 else None,
            delta_color="inverse"
        )

    with cols2[1]:
        diff = ((recursos_pc / recursos_pc_prov) - 1) * 100 if recursos_pc_prov > 0 else 0
        st.metric(
            "Recursos Per Cápita",
            f"$ {fmt_num(recursos_pc)}",
            delta=f"{diff:+.1f}% vs prom. prov." if recursos_pc_prov > 0 else None
        )

    with cols2[2]:
        st.metric("Superficie", f"{fmt_num(superficie_muni)} km²")

    with cols2[3]:
        trabajadores = float(muni_data.get("Muni_Cantidad_Trabajadores", 0) or 0)
        trab_per_1000 = (trabajadores / poblacion_muni * 1000) if poblacion_muni > 0 else 0
        st.metric("Trabajadores / 1000 hab", f"{trab_per_1000:.1f}")


def render_provincial_charts(df_base: pd.DataFrame, df_metrics: pd.DataFrame):
    """Renderiza gráficos para vista provincial."""
    if df_metrics.empty:
        st.info("No hay datos de métricas disponibles para graficar.")
        return

    # Merge con nombres
    df_chart = df_metrics.merge(
        df_base[["ID_Municipio", "Muni_Nombre", "Muni_Poblacion_2022", "Muni_Superficie"]],
        on="ID_Municipio",
        how="left"
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Top Municipios", "Distribución", "Comparativas", "Recursos vs Gastos", "Jurisdicciones y Programas"])

    with tab1:
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Top 10 por Recursos Percibidos**")
            if "recursos_percibido" in df_chart.columns:
                top_rec = df_chart.nlargest(10, "recursos_percibido")
                fig = px.bar(
                    top_rec,
                    x="recursos_percibido",
                    y="Muni_Nombre",
                    orientation="h",
                    color="recursos_percibido",
                    color_continuous_scale="Greens"
                )
                fig.update_layout(
                    showlegend=False,
                    coloraxis_showscale=False,
                    yaxis={"categoryorder": "total ascending"},
                    height=350,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                fig.update_xaxes(title="")
                fig.update_yaxes(title="")
                st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("**Top 10 por Gastos Pagados**")
            if "gastos_pagado" in df_chart.columns:
                top_gas = df_chart.nlargest(10, "gastos_pagado")
                fig = px.bar(
                    top_gas,
                    x="gastos_pagado",
                    y="Muni_Nombre",
                    orientation="h",
                    color="gastos_pagado",
                    color_continuous_scale="Reds"
                )
                fig.update_layout(
                    showlegend=False,
                    coloraxis_showscale=False,
                    yaxis={"categoryorder": "total ascending"},
                    height=350,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                fig.update_xaxes(title="")
                fig.update_yaxes(title="")
                st.plotly_chart(fig, use_container_width=True)

    with tab2:
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Distribución de Recursos Per Cápita**")
            if "recursos_percibido" in df_chart.columns and "Muni_Poblacion_2022" in df_chart.columns:
                df_chart["recursos_pc"] = df_chart.apply(
                    lambda r: r["recursos_percibido"] / r["Muni_Poblacion_2022"]
                    if r["Muni_Poblacion_2022"] > 0 else 0, axis=1
                )
                fig = px.histogram(
                    df_chart[df_chart["recursos_pc"] > 0],
                    x="recursos_pc",
                    nbins=20,
                    color_discrete_sequence=["#2ecc71"]
                )
                fig.update_layout(
                    height=300,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                fig.update_xaxes(title="$ per cápita")
                fig.update_yaxes(title="Cantidad de municipios")
                st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("**Distribución de Balance Fiscal**")
            if "balance_fiscal" in df_chart.columns:
                fig = px.histogram(
                    df_chart,
                    x="balance_fiscal",
                    nbins=20,
                    color_discrete_sequence=["#3498db"]
                )
                fig.update_layout(
                    height=300,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                fig.update_xaxes(title="Balance Fiscal ($)")
                fig.update_yaxes(title="Cantidad de municipios")
                st.plotly_chart(fig, use_container_width=True)

    with tab3:
        st.markdown("**Recursos vs Gastos por Municipio**")
        if "recursos_percibido" in df_chart.columns and "gastos_pagado" in df_chart.columns:
            fig = px.scatter(
                df_chart,
                x="recursos_percibido",
                y="gastos_pagado",
                size="Muni_Poblacion_2022",
                hover_name="Muni_Nombre",
                color="balance_fiscal" if "balance_fiscal" in df_chart.columns else None,
                color_continuous_scale="RdYlGn",
                size_max=50
            )
            # Línea de equilibrio
            max_val = max(df_chart["recursos_percibido"].max(), df_chart["gastos_pagado"].max())
            fig.add_trace(go.Scatter(
                x=[0, max_val],
                y=[0, max_val],
                mode="lines",
                line=dict(dash="dash", color="gray"),
                name="Equilibrio"
            ))
            fig.update_layout(
                height=400,
                margin={"l": 0, "r": 0, "t": 0, "b": 0}
            )
            fig.update_xaxes(title="Recursos Percibidos ($)")
            fig.update_yaxes(title="Gastos Pagados ($)")
            st.plotly_chart(fig, use_container_width=True)

    with tab4:
        st.markdown("**Comparación Recursos Percibidos vs Gastos Pagados**")
        if "recursos_percibido" in df_chart.columns and "gastos_pagado" in df_chart.columns:
            # Preparar datos para gráfico de barras agrupadas
            df_compare = df_chart[["Muni_Nombre", "recursos_percibido", "gastos_pagado"]].copy()
            df_compare = df_compare.dropna()
            df_compare["diferencia"] = df_compare["recursos_percibido"] - df_compare["gastos_pagado"]

            # Ordenar por diferencia (superávit a déficit)
            df_compare = df_compare.sort_values("diferencia", ascending=False)

            # Selector de cantidad de municipios a mostrar
            n_munis = st.slider("Cantidad de municipios a mostrar:", min_value=10, max_value=min(50, len(df_compare)), value=20, key="slider_recursos_gastos")

            # Tomar los top N
            df_top = df_compare.head(n_munis)

            # Transformar a formato largo para barras agrupadas
            df_melted = pd.melt(
                df_top,
                id_vars=["Muni_Nombre", "diferencia"],
                value_vars=["recursos_percibido", "gastos_pagado"],
                var_name="Tipo",
                value_name="Monto"
            )
            df_melted["Tipo"] = df_melted["Tipo"].map({
                "recursos_percibido": "Recursos Percibidos",
                "gastos_pagado": "Gastos Pagados"
            })

            # Crear gráfico de barras agrupadas
            fig = px.bar(
                df_melted,
                x="Muni_Nombre",
                y="Monto",
                color="Tipo",
                barmode="group",
                color_discrete_map={
                    "Recursos Percibidos": "#2ecc71",
                    "Gastos Pagados": "#e74c3c"
                },
                hover_data={"Monto": ":,.0f"}
            )

            fig.update_layout(
                height=500,
                margin={"l": 0, "r": 0, "t": 30, "b": 100},
                xaxis_tickangle=-45,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                )
            )
            fig.update_xaxes(title="")
            fig.update_yaxes(title="Monto ($)", tickformat=",")
            st.plotly_chart(fig, use_container_width=True)

            # Mostrar tabla resumen
            st.markdown("**Resumen de diferencias (Recursos - Gastos)**")
            col1, col2, col3 = st.columns(3)

            superavit = df_compare[df_compare["diferencia"] > 0]
            deficit = df_compare[df_compare["diferencia"] < 0]
            equilibrio = df_compare[df_compare["diferencia"] == 0]

            with col1:
                st.metric(
                    "🟢 Municipios con Superávit",
                    len(superavit),
                    f"Total: {fmt_money_millions(superavit['diferencia'].sum())}"
                )
            with col2:
                st.metric(
                    "🔴 Municipios con Déficit",
                    len(deficit),
                    f"Total: {fmt_money_millions(deficit['diferencia'].sum())}"
                )
            with col3:
                st.metric(
                    "🟡 Municipios en Equilibrio",
                    len(equilibrio)
                )

    with tab5:
        st.markdown("**Explorador de Jurisdicciones, Programas y Metas**")

        # Cargar datos
        df_jurisdicciones, err_juri = fetch_jurisdicciones_provinciales()
        df_programas, err_prog = fetch_programas_provinciales()
        df_metas, err_metas = fetch_metas_provinciales()

        if err_juri or df_jurisdicciones.empty:
            st.warning("No hay datos de jurisdicciones disponibles.")
        else:
            # Agregar jurisdicciones por nombre (sumando todos los documentos)
            df_juri_agg = df_jurisdicciones.groupby("Juri_Nombre").agg({
                "ID_Jurisdiccion": "first",  # Mantener un ID para referencia
                "cantidad_programas": "sum",
                "total_vigente": "sum",
                "total_devengado": "sum",
                "total_pagado": "sum"
            }).reset_index()

            # Crear 3 columnas para las tablas
            col_juri, col_prog, col_metas = st.columns(3)

            with col_juri:
                st.markdown("##### 📁 Jurisdicciones")
                st.caption("Selecciona una jurisdicción para filtrar")

                # Crear tabla de jurisdicciones
                df_juri_display = df_juri_agg[["Juri_Nombre", "cantidad_programas", "total_pagado"]].copy()
                df_juri_display.columns = ["Jurisdicción", "Programas", "Total Pagado"]
                df_juri_display["Total Pagado"] = df_juri_display["Total Pagado"].apply(lambda x: f"$ {x/1_000_000:,.1f} M")
                df_juri_display = df_juri_display.sort_values("Programas", ascending=False)

                # Selector de jurisdicción
                juri_options = ["— Todas —"] + df_juri_agg["Juri_Nombre"].sort_values().tolist()
                selected_juri = st.selectbox(
                    "Filtrar por jurisdicción:",
                    options=juri_options,
                    key="select_jurisdiccion"
                )

                # Mostrar tabla
                st.dataframe(
                    df_juri_display,
                    use_container_width=True,
                    height=400,
                    hide_index=True
                )

                # Métricas resumen
                st.metric("Total Jurisdicciones", len(df_juri_agg))

            with col_prog:
                st.markdown("##### 📋 Programas")

                # Inicializar variables
                selected_prog_id = None
                selected_prog = "— Todos —"
                df_prog_filtered = pd.DataFrame()

                # Verificar si hay datos de programas
                if df_programas is None or df_programas.empty:
                    st.info("No hay datos de programas disponibles.")
                else:
                    # Filtrar programas según jurisdicción seleccionada
                    if selected_juri != "— Todas —":
                        # Obtener IDs de jurisdicción que coinciden con el nombre
                        juri_ids = df_jurisdicciones[df_jurisdicciones["Juri_Nombre"] == selected_juri]["ID_Jurisdiccion"].tolist()
                        df_prog_filtered = df_programas[df_programas["ID_Jurisdiccion"].isin(juri_ids)]
                        st.caption(f"Filtrado por: **{selected_juri}**")
                    else:
                        df_prog_filtered = df_programas
                        st.caption("Mostrando todos los programas")

                    if df_prog_filtered.empty:
                        st.info("No hay programas para esta selección.")
                    else:
                        # Agregar nombre de jurisdicción a programas
                        juri_names = df_jurisdicciones[["ID_Jurisdiccion", "Juri_Nombre"]].drop_duplicates()
                        df_prog_display = df_prog_filtered.merge(juri_names, on="ID_Jurisdiccion", how="left")

                        # Selector de programa
                        prog_options = ["— Todos —"] + df_prog_filtered["Prog_Nombre"].dropna().unique().tolist()
                        selected_prog = st.selectbox(
                            "Filtrar por programa:",
                            options=prog_options,
                            key="select_programa"
                        )

                        if selected_prog != "— Todos —":
                            matching_progs = df_prog_filtered[df_prog_filtered["Prog_Nombre"] == selected_prog]
                            selected_prog_id = matching_progs["ID_Programa"].iloc[0] if len(matching_progs) > 0 else None

                        # Preparar display
                        df_prog_show = df_prog_display[["Prog_Nombre", "Juri_Nombre", "cantidad_metas", "Prog_Pagado"]].copy()
                        df_prog_show.columns = ["Programa", "Jurisdicción", "Metas", "Pagado"]
                        df_prog_show["Pagado"] = df_prog_show["Pagado"].apply(lambda x: f"$ {x/1_000_000:,.1f} M" if x >= 1_000_000 else f"$ {x:,.0f}")

                        st.dataframe(
                            df_prog_show,
                            use_container_width=True,
                            height=400,
                            hide_index=True
                        )

                        st.metric("Total Programas", len(df_prog_filtered))

            with col_metas:
                st.markdown("##### 🎯 Metas")

                if df_metas is None or df_metas.empty:
                    st.info("No hay metas disponibles.")
                else:
                    # Filtrar metas según programa y/o jurisdicción
                    if selected_prog_id is not None:
                        df_metas_filtered = df_metas[df_metas["ID_Programa"] == selected_prog_id]
                        st.caption(f"Filtrado por: **{selected_prog}**")
                    elif selected_juri != "— Todas —":
                        # Filtrar por todos los programas de la jurisdicción
                        prog_ids = df_prog_filtered["ID_Programa"].tolist()
                        df_metas_filtered = df_metas[df_metas["ID_Programa"].isin(prog_ids)]
                        st.caption(f"Metas de: **{selected_juri}**")
                    else:
                        df_metas_filtered = df_metas
                        st.caption("Mostrando todas las metas")

                    if df_metas_filtered.empty:
                        st.info("No hay metas para esta selección.")
                    else:
                        # Agregar nombre de programa
                        if df_programas is not None and not df_programas.empty:
                            prog_names = df_programas[["ID_Programa", "Prog_Nombre"]].drop_duplicates()
                            df_metas_display = df_metas_filtered.merge(prog_names, on="ID_Programa", how="left")
                        else:
                            df_metas_display = df_metas_filtered.copy()
                            df_metas_display["Prog_Nombre"] = "—"

                        # Calcular % ejecución
                        df_metas_display["Ejec_%"] = df_metas_display.apply(
                            lambda r: f"{(r['Meta_Ejecutado'] / r['Meta_Anual'] * 100):.0f}%" if r["Meta_Anual"] > 0 else "—",
                            axis=1
                        )

                        df_metas_show = df_metas_display[["Meta_Nombre", "Prog_Nombre", "Meta_Unidad", "Meta_Anual", "Meta_Ejecutado", "Ejec_%"]].copy()
                        df_metas_show.columns = ["Meta", "Programa", "Unidad", "Anual", "Ejecutado", "% Ejec."]

                        st.dataframe(
                            df_metas_show,
                            use_container_width=True,
                            height=400,
                            hide_index=True
                        )

                        st.metric("Total Metas", len(df_metas_filtered))

            # Gráfico resumen al final
            st.divider()
            st.markdown("**Distribución de Programas por Jurisdicción**")

            # Top 15 jurisdicciones por cantidad de programas
            df_top_juri = df_juri_agg.nlargest(15, "cantidad_programas")

            if not df_top_juri.empty:
                fig = px.bar(
                    df_top_juri,
                    x="cantidad_programas",
                    y="Juri_Nombre",
                    orientation="h",
                    color="cantidad_programas",
                    color_continuous_scale="Blues",
                    labels={"cantidad_programas": "Cantidad de Programas", "Juri_Nombre": ""}
                )
                fig.update_layout(
                    showlegend=False,
                    coloraxis_showscale=False,
                    yaxis={"categoryorder": "total ascending"},
                    height=400,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                st.plotly_chart(fig, use_container_width=True)


def render_resumen_general(
    doc_id: str,
    df_gastos: pd.DataFrame,
    df_recursos: pd.DataFrame,
    muni_nombre: str,
    doc_periodo: str = "Anual"
):
    """Renderiza el resumen general - Layout ultra compacto con filtros."""
    init_session_state()
    es_proyectado = st.session_state.get("map_normalization", "absoluto") == "proyectado"
    factor_proy = get_factor_proyeccion(doc_periodo) if es_proyectado else 1.0

    # Preparar datos
    for c in ["Gasto_Vigente", "Gasto_Preventivo", "Gasto_Compromiso", "Gasto_Devengado", "Gasto_Pagado"]:
        if c in df_gastos.columns:
            df_gastos[c] = pd.to_numeric(df_gastos[c], errors="coerce").fillna(0)
    for c in ["Rec_Vigente", "Rec_Devengado", "Rec_Percibido"]:
        if c in df_recursos.columns:
            df_recursos[c] = pd.to_numeric(df_recursos[c], errors="coerce").fillna(0)

    # ===== CARGAR DATOS PARA FILTROS =====
    df_juri, _ = fetch_jurisdicciones_doc(doc_id)
    df_prog = pd.DataFrame()
    df_metas = pd.DataFrame()

    if not df_juri.empty:
        juri_ids = tuple(df_juri["ID_Jurisdiccion"].tolist())
        df_prog, _ = fetch_programas_doc(juri_ids)

    # ===== FILTROS (fila superior con borde) =====
    st.markdown("**🔍 Filtros:**")
    fc1, fc2, fc3, fc4 = st.columns([1, 1, 1, 1])
    with fc1:
        juri_options = ["Todas"] + (df_juri["Juri_Nombre"].tolist() if not df_juri.empty else [])
        sel_juri = st.selectbox("🏛️ Jurisdicción", juri_options, key="filter_juri_select")
        if sel_juri != "Todas":
            st.session_state.filter_jurisdiccion = sel_juri
        else:
            st.session_state.filter_jurisdiccion = None

    with fc2:
        # Filtrar programas por jurisdicción seleccionada
        if st.session_state.filter_jurisdiccion and not df_prog.empty and not df_juri.empty:
            juri_row = df_juri[df_juri["Juri_Nombre"] == st.session_state.filter_jurisdiccion]
            if not juri_row.empty:
                juri_id = juri_row.iloc[0]["ID_Jurisdiccion"]
                df_prog_filtered = df_prog[df_prog["ID_Jurisdiccion"] == juri_id]
            else:
                df_prog_filtered = df_prog
        else:
            df_prog_filtered = df_prog

        prog_options = ["Todos"] + (df_prog_filtered["Prog_Nombre"].tolist() if not df_prog_filtered.empty else [])
        sel_prog = st.selectbox("📋 Programa", prog_options, key="filter_prog_select")
        if sel_prog != "Todos":
            st.session_state.filter_programa = sel_prog
        else:
            st.session_state.filter_programa = None

    # Mostrar totales en filtros
    with fc3:
        if not df_prog.empty and "Prog_Vigente" in df_prog.columns:
            tot_vig = df_prog["Prog_Vigente"].sum()
            st.metric("Total Vigente", f"${int(tot_vig/1e6)}M")
    with fc4:
        if not df_prog.empty and "Prog_Devengado" in df_prog.columns:
            tot_dev = df_prog["Prog_Devengado"].sum()
            st.metric("Total Devengado", f"${int(tot_dev/1e6)}M")

    # Calcular totales
    if "Rec_Nombre" in df_recursos.columns:
        df_recursos["Rec_Nombre_Norm"] = df_recursos["Rec_Nombre"].str.strip().str.lower()
        cats_prin = ["recursos de capital", "ingresos corrientes", "fuentes financieras", "de libre disponibilidad", "afectados"]
        cats_extra = ["extrapresupuestario", "extrapresupuestarios"]
        df_prin = df_recursos[df_recursos["Rec_Nombre_Norm"].isin(cats_prin)]
        df_ext = df_recursos[df_recursos["Rec_Nombre_Norm"].isin(cats_extra)]
        total_rec_vig = (df_prin["Rec_Vigente"].sum() / 2) + df_ext["Rec_Vigente"].sum()
        total_rec_per = ((df_prin["Rec_Percibido"].sum() / 2) + df_ext["Rec_Percibido"].sum()) * (factor_proy if es_proyectado else 1)
    else:
        total_rec_vig = df_recursos["Rec_Vigente"].sum() if "Rec_Vigente" in df_recursos.columns else 0
        total_rec_per = (df_recursos["Rec_Percibido"].sum() if "Rec_Percibido" in df_recursos.columns else 0) * (factor_proy if es_proyectado else 1)

    total_gas_vig = df_gastos["Gasto_Vigente"].sum() if "Gasto_Vigente" in df_gastos.columns else 0
    total_gas_pag = (df_gastos["Gasto_Pagado"].sum() if "Gasto_Pagado" in df_gastos.columns else 0) * (factor_proy if es_proyectado else 1)
    diff_vig = total_rec_vig - total_gas_vig
    diff_eje = total_rec_per - total_gas_pag

    # ===== FILA 1: KPIs (4 cols) + Gráficos comparativos (2 cols) =====
    c1, c2, c3, c4, c5, c6 = st.columns([1, 1, 1, 1, 1.5, 1.5])

    with c1:
        pct = int(total_rec_per / total_rec_vig * 100) if total_rec_vig > 0 else 0
        st.metric("💰 Rec Vig", fmt_money_full(total_rec_vig))
        st.metric("Percibido", fmt_money_full(total_rec_per), delta=f"{pct}%")

    with c2:
        pct = int(total_gas_pag / total_gas_vig * 100) if total_gas_vig > 0 else 0
        st.metric("💸 Gas Vig", fmt_money_full(total_gas_vig))
        st.metric("Pagado", fmt_money_full(total_gas_pag), delta=f"{pct}%")

    with c3:
        st.metric("⚖️ Bal Pres", fmt_money_full(diff_vig), delta="+" if diff_vig >= 0 else "-", delta_color="normal" if diff_vig >= 0 else "inverse")
        st.metric("Bal Finan", fmt_money_full(diff_eje), delta="+" if diff_eje >= 0 else "-", delta_color="normal" if diff_eje >= 0 else "inverse")

    with c4:
        if es_proyectado and factor_proy != 1.0:
            st.caption(f"📊 Proy x{factor_proy}")
        st.caption(f"Rec: {int(total_rec_per/1e6)}M" if total_rec_per > 0 else "")
        st.caption(f"Gas: {int(total_gas_pag/1e6)}M" if total_gas_pag > 0 else "")

    with c5:
        df_c = pd.DataFrame({"X": ["Rec", "Gas", "Dif"], "V": [total_rec_vig, total_gas_vig, diff_vig]})
        fig = px.bar(df_c, x="X", y="V", color="X", color_discrete_sequence=["#2ecc71", "#e74c3c", "#3498db" if diff_vig >= 0 else "#e67e22"])
        fig.update_layout(height=120, margin={"l": 0, "r": 0, "t": 20, "b": 0}, showlegend=False, title="Vigente", title_font_size=11)
        st.plotly_chart(fig, use_container_width=True)

    with c6:
        df_c = pd.DataFrame({"X": ["Per", "Pag", "Dif"], "V": [total_rec_per, total_gas_pag, diff_eje]})
        fig = px.bar(df_c, x="X", y="V", color="X", color_discrete_sequence=["#27ae60", "#c0392b", "#3498db" if diff_eje >= 0 else "#e67e22"])
        fig.update_layout(height=120, margin={"l": 0, "r": 0, "t": 20, "b": 0}, showlegend=False, title="Ejecutado", title_font_size=11)
        st.plotly_chart(fig, use_container_width=True)

    # ===== FILA 2: Tortas Recursos (2) + Torta Gastos (1) + Tesorería + Sit Patrimonial =====
    st.markdown("---")
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])

    # Tortas de recursos
    if "Rec_Nombre" in df_recursos.columns:
        df_recursos["Rec_Nombre_Norm"] = df_recursos["Rec_Nombre"].str.strip().str.lower()
        with c1:
            st.caption("💰 Rec x Tipo")
            cats = ["ingresos corrientes", "recursos de capital", "fuentes financieras"]
            df_t = df_recursos[df_recursos["Rec_Nombre_Norm"].isin(cats)].groupby("Rec_Nombre")["Rec_Percibido"].sum().reset_index()
            if es_proyectado: df_t["Rec_Percibido"] *= factor_proy
            if not df_t.empty and df_t["Rec_Percibido"].sum() > 0:
                fig = px.pie(df_t, values="Rec_Percibido", names="Rec_Nombre", hole=0.6)
                fig.update_layout(height=120, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False)
                fig.update_traces(texttemplate="%{percent:.0%}", textfont_size=9)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("—")

        with c2:
            st.caption("💰 Rec x Disp")
            cats = ["de libre disponibilidad", "afectados"]
            df_t = df_recursos[df_recursos["Rec_Nombre_Norm"].isin(cats)].groupby("Rec_Nombre")["Rec_Percibido"].sum().reset_index()
            if es_proyectado: df_t["Rec_Percibido"] *= factor_proy
            if not df_t.empty and df_t["Rec_Percibido"].sum() > 0:
                fig = px.pie(df_t, values="Rec_Percibido", names="Rec_Nombre", hole=0.6, color_discrete_sequence=["#f39c12", "#1abc9c"])
                fig.update_layout(height=120, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False)
                fig.update_traces(texttemplate="%{percent:.0%}", textfont_size=9)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("—")

    # Torta de Gastos por Objeto
    with c3:
        st.caption("💸 Gas x Objeto")
        if "Gasto_Objeto" in df_gastos.columns and "Gasto_Pagado" in df_gastos.columns:
            df_go = df_gastos.groupby("Gasto_Objeto")["Gasto_Pagado"].sum().reset_index()
            if es_proyectado: df_go["Gasto_Pagado"] *= factor_proy
            df_go = df_go[df_go["Gasto_Pagado"] > 0].nlargest(6, "Gasto_Pagado")
            if not df_go.empty:
                fig = px.pie(df_go, values="Gasto_Pagado", names="Gasto_Objeto", hole=0.6, color_discrete_sequence=px.colors.sequential.Reds_r)
                fig.update_layout(height=120, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False)
                fig.update_traces(texttemplate="%{percent:.0%}", textfont_size=9)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("—")
        else:
            st.caption("—")

    # Tesorería
    with c4:
        st.caption("🏦 Tesorería")
        df_tes, _ = fetch_tesoreria(doc_id)
        if not df_tes.empty:
            agg = df_tes.groupby("MovTes_TipoResumido")["MovTes_Importe"].sum().to_dict()
            si = agg.get("Saldo Inicial", agg.get("SALDO INICIAL", 0))
            ing = agg.get("Ingreso", agg.get("Ingresos", agg.get("INGRESO", agg.get("INGRESOS", 0))))
            egr = agg.get("Egreso", agg.get("Egresos", agg.get("EGRESO", agg.get("EGRESOS", 0))))
            sf = si + ing - egr  # Saldo Final
            dif = sf - si  # Diferencia SF - SI
            df_c = pd.DataFrame({"C": ["SI", "Ing", "Egr", "SF", "Dif"], "V": [si, ing, egr, sf, dif]})
            colors = ["#3498db", "#2ecc71", "#e74c3c", "#9b59b6" if sf >= 0 else "#e67e22", "#1abc9c" if dif >= 0 else "#e67e22"]
            fig = px.bar(df_c, x="C", y="V", color="C", color_discrete_sequence=colors)
            fig.update_layout(height=120, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("—")

    # Situación Patrimonial
    with c5:
        st.caption("📈 Sit. Patrimonial")
        df_sp, _ = fetch_situacion_patrimonial(doc_id)
        if not df_sp.empty:
            df_sp["Tipo"] = df_sp["SitPat_Tipo"].str.strip().str.upper()
            df_sp["Nombre"] = df_sp["SitPat_Nombre"].str.strip().str.upper() if "SitPat_Nombre" in df_sp.columns else ""

            # Buscar ACTIVO CORRIENTE y NO CORRIENTE en Tipo o Nombre
            act_corr = df_sp[df_sp["Tipo"] == "ACTIVO CORRIENTE"]["SitPat_Saldo"].sum()
            act_no_corr = df_sp[df_sp["Tipo"] == "ACTIVO NO CORRIENTE"]["SitPat_Saldo"].sum()

            # Si no hay, buscar en Nombre dentro de registros tipo ACTIVO
            if act_corr == 0 and act_no_corr == 0:
                df_act = df_sp[df_sp["Tipo"] == "ACTIVO"]
                act_corr = df_act[df_act["Nombre"].str.contains("CORRIENTE", na=False) & ~df_act["Nombre"].str.contains("NO CORRIENTE", na=False)]["SitPat_Saldo"].sum()
                act_no_corr = df_act[df_act["Nombre"].str.contains("NO CORRIENTE", na=False)]["SitPat_Saldo"].sum()

            # Si aún no hay desglose, usar total
            if act_corr == 0 and act_no_corr == 0:
                act_total = df_sp[df_sp["Tipo"].str.contains("ACTIVO", na=False)]["SitPat_Saldo"].sum()
                act_corr = act_total * 0.6
                act_no_corr = act_total * 0.4

            # Pasivo y Patrimonio - mantener signo original (negativos van hacia abajo)
            pasivo = df_sp[df_sp["Tipo"].str.contains("PASIVO", na=False)]["SitPat_Saldo"].sum()
            patrimonio = df_sp[df_sp["Tipo"].str.contains("PATRIMONIO", na=False)]["SitPat_Saldo"].sum()

            fig = go.Figure()
            # Activos (positivos, hacia arriba)
            fig.add_trace(go.Bar(name="Act Corr", x=["Activo"], y=[act_corr], marker_color="#27ae60", text=[f"{int(act_corr/1e6)}M"], textposition="inside"))
            fig.add_trace(go.Bar(name="Act No Corr", x=["Activo"], y=[act_no_corr], marker_color="#2ecc71", text=[f"{int(act_no_corr/1e6)}M"], textposition="inside"))
            # Pasivo y Patrimonio (negativos van hacia abajo)
            fig.add_trace(go.Bar(name="Pasivo", x=["Pas+Pat"], y=[pasivo], marker_color="#e74c3c", text=[f"{int(pasivo/1e6)}M"], textposition="inside"))
            fig.add_trace(go.Bar(name="Patrimonio", x=["Pas+Pat"], y=[patrimonio], marker_color="#3498db", text=[f"{int(patrimonio/1e6)}M"], textposition="inside"))
            fig.update_layout(barmode="relative", height=120, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False)
            fig.add_hline(y=0, line_color="gray", line_width=1)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("—")

    # ===== FILA 3: Cuentas =====
    st.markdown("---")
    cc1, cc2 = st.columns([1, 1])
    with cc1:
        st.caption("📒 Cuentas")
        df_ctas, _ = fetch_cuentas(doc_id)
        if not df_ctas.empty:
            tot = df_ctas["Cuenta_Importe"].sum()
            df_ctas = df_ctas.sort_values("Cuenta_Importe", ascending=False)
            df_ctas["%"] = df_ctas["Cuenta_Importe"].apply(lambda x: f"{int(x/tot*100)}%" if tot > 0 else "—")
            df_ctas["$"] = df_ctas["Cuenta_Importe"].apply(lambda x: f"{int(x/1e6)}M" if x >= 1e6 else f"{int(x/1e3)}K")
            st.dataframe(df_ctas[["Cuenta_Nombre", "$", "%"]].rename(columns={"Cuenta_Nombre": "Cuenta"}), use_container_width=True, hide_index=True, height=150)
        else:
            st.caption("—")

    # ===== FILA 4: Jurisdicciones, Programas, Metas (expandidas con filtros) =====
    st.markdown("---")
    st.caption("🏛️ Jurisdicciones / Programas / Metas")

    # Aplicar filtros
    df_juri_display = df_juri.copy() if not df_juri.empty else pd.DataFrame()
    df_prog_display = df_prog.copy() if not df_prog.empty else pd.DataFrame()

    if st.session_state.filter_jurisdiccion and not df_juri_display.empty:
        df_juri_display = df_juri_display[df_juri_display["Juri_Nombre"] == st.session_state.filter_jurisdiccion]
        if not df_prog_display.empty:
            juri_ids_filtered = df_juri_display["ID_Jurisdiccion"].tolist()
            df_prog_display = df_prog_display[df_prog_display["ID_Jurisdiccion"].isin(juri_ids_filtered)]

    if st.session_state.filter_programa and not df_prog_display.empty:
        df_prog_display = df_prog_display[df_prog_display["Prog_Nombre"] == st.session_state.filter_programa]

    tc1, tc2, tc3 = st.columns([1, 1.5, 1])

    with tc1:
        st.markdown("**Jurisdicciones**")
        if not df_juri_display.empty:
            if not df_prog.empty:
                agg = df_prog.groupby("ID_Jurisdiccion").agg({"Prog_Vigente": "sum", "Prog_Devengado": "sum", "Prog_Pagado": "sum"}).reset_index()
                df_juri_display = df_juri_display.merge(agg, on="ID_Jurisdiccion", how="left").fillna(0)
                total_vig_juri = df_juri_display["Prog_Vigente"].sum()
                df_juri_display["%Eje"] = df_juri_display.apply(lambda r: f"{int(r['Prog_Pagado']/r['Prog_Vigente']*100)}%" if r["Prog_Vigente"] > 0 else "—", axis=1)
                df_juri_display["%Peso"] = df_juri_display.apply(lambda r: f"{int(r['Prog_Vigente']/total_vig_juri*100)}%" if total_vig_juri > 0 else "—", axis=1)
                df_juri_display["Vig"] = df_juri_display["Prog_Vigente"].apply(lambda x: f"{int(x/1e6)}M" if x >= 1e6 else f"{int(x/1e3)}K")
                st.dataframe(df_juri_display[["Juri_Nombre", "Vig", "%Eje", "%Peso"]].rename(columns={"Juri_Nombre": "Jurisdicción"}), use_container_width=True, hide_index=True, height=200)
            else:
                st.dataframe(df_juri_display[["Juri_Nombre"]].rename(columns={"Juri_Nombre": "Jurisdicción"}), use_container_width=True, hide_index=True, height=200)
        else:
            st.caption("—")

    with tc2:
        st.markdown("**Programas**")
        if not df_prog_display.empty:
            total_vig_prog = df_prog_display["Prog_Vigente"].sum()
            df_prog_display["Vig"] = df_prog_display["Prog_Vigente"].apply(lambda x: f"{int(x/1e6)}M" if x >= 1e6 else f"{int(x/1e3)}K")
            df_prog_display["Dev"] = df_prog_display["Prog_Devengado"].apply(lambda x: f"{int(x/1e6)}M" if x >= 1e6 else f"{int(x/1e3)}K") if "Prog_Devengado" in df_prog_display.columns else "—"
            df_prog_display["%D/V"] = df_prog_display.apply(lambda r: f"{int(r['Prog_Devengado']/r['Prog_Vigente']*100)}%" if r["Prog_Vigente"] > 0 and "Prog_Devengado" in df_prog_display.columns else "—", axis=1)
            df_prog_display["%Peso"] = df_prog_display.apply(lambda r: f"{int(r['Prog_Vigente']/total_vig_prog*100)}%" if total_vig_prog > 0 else "—", axis=1)
            cols_prog = ["Prog_Nombre", "Vig", "Dev", "%D/V", "%Peso"]
            st.dataframe(df_prog_display[cols_prog].rename(columns={"Prog_Nombre": "Programa"}), use_container_width=True, hide_index=True, height=200)
        else:
            st.caption("—")

    with tc3:
        st.markdown("**Metas**")
        if not df_prog_display.empty:
            prog_ids_filtered = tuple(df_prog_display["ID_Programa"].tolist())
            df_metas, _ = fetch_metas_doc(prog_ids_filtered)
        if not df_metas.empty:
            cols_show = ["Meta_Nombre", "Meta_Anual", "Meta_Parcial", "Meta_Ejecutado"]
            cols_exist = [c for c in cols_show if c in df_metas.columns]
            df_m = df_metas[cols_exist].copy()
            col_rename = {"Meta_Nombre": "Meta", "Meta_Anual": "Anual", "Meta_Parcial": "Parcial", "Meta_Ejecutado": "Ejec"}
            df_m.columns = [col_rename.get(c, c) for c in cols_exist]
            st.dataframe(df_m, use_container_width=True, hide_index=True, height=200)
        else:
            st.caption("—")


def render_detalle_tablas(doc_id: str, df_gastos: pd.DataFrame, df_recursos: pd.DataFrame):
    """Renderiza el detalle completo de cada tabla importante."""
    init_session_state()

    st.markdown("### 📋 Detalle de Tablas")

    # Cargar todos los datos
    df_juri, _ = fetch_jurisdicciones_doc(doc_id)
    df_prog = pd.DataFrame()
    df_metas = pd.DataFrame()
    if not df_juri.empty:
        juri_ids = tuple(df_juri["ID_Jurisdiccion"].tolist())
        df_prog, _ = fetch_programas_doc(juri_ids)
        if not df_prog.empty:
            prog_ids = tuple(df_prog["ID_Programa"].tolist())
            df_metas, _ = fetch_metas_doc(prog_ids)

    df_tes, _ = fetch_tesoreria(doc_id)
    df_ctas, _ = fetch_cuentas(doc_id)
    df_sp, _ = fetch_situacion_patrimonial(doc_id)

    # Tabs para cada tabla
    tabs = st.tabs(["🏛️ Jurisdicciones", "📋 Programas", "🎯 Metas", "🏦 Tesorería", "📒 Cuentas", "📈 Sit. Patrimonial", "💸 Gastos", "💰 Recursos"])

    # Tab Jurisdicciones
    with tabs[0]:
        st.subheader("Jurisdicciones")
        if not df_juri.empty:
            if not df_prog.empty:
                agg = df_prog.groupby("ID_Jurisdiccion").agg({
                    "Prog_Vigente": "sum",
                    "Prog_Devengado": "sum",
                    "Prog_Pagado": "sum"
                }).reset_index()
                df_juri_full = df_juri.merge(agg, on="ID_Jurisdiccion", how="left").fillna(0)
                total_vig = df_juri_full["Prog_Vigente"].sum()
                df_juri_full["Vigente"] = df_juri_full["Prog_Vigente"].apply(lambda x: f"${x:,.0f}")
                df_juri_full["Devengado"] = df_juri_full["Prog_Devengado"].apply(lambda x: f"${x:,.0f}")
                df_juri_full["Pagado"] = df_juri_full["Prog_Pagado"].apply(lambda x: f"${x:,.0f}")
                df_juri_full["%Ejecución"] = df_juri_full.apply(lambda r: f"{int(r['Prog_Pagado']/r['Prog_Vigente']*100)}%" if r["Prog_Vigente"] > 0 else "—", axis=1)
                df_juri_full["%Peso"] = df_juri_full.apply(lambda r: f"{int(r['Prog_Vigente']/total_vig*100)}%" if total_vig > 0 else "—", axis=1)
                cols = ["Juri_Nombre", "Vigente", "Devengado", "Pagado", "%Ejecución", "%Peso"]
                st.dataframe(df_juri_full[cols].rename(columns={"Juri_Nombre": "Jurisdicción"}), use_container_width=True, hide_index=True)
                st.caption(f"**Total Vigente:** ${total_vig:,.0f}")
            else:
                st.dataframe(df_juri, use_container_width=True, hide_index=True)
        else:
            st.info("No hay datos de jurisdicciones")

    # Tab Programas
    with tabs[1]:
        st.subheader("Programas")
        if not df_prog.empty:
            total_vig = df_prog["Prog_Vigente"].sum()
            df_prog_full = df_prog.copy()
            df_prog_full["Vigente"] = df_prog_full["Prog_Vigente"].apply(lambda x: f"${x:,.0f}")
            df_prog_full["Devengado"] = df_prog_full["Prog_Devengado"].apply(lambda x: f"${x:,.0f}") if "Prog_Devengado" in df_prog_full.columns else "—"
            df_prog_full["Pagado"] = df_prog_full["Prog_Pagado"].apply(lambda x: f"${x:,.0f}") if "Prog_Pagado" in df_prog_full.columns else "—"
            df_prog_full["%D/V"] = df_prog_full.apply(lambda r: f"{int(r['Prog_Devengado']/r['Prog_Vigente']*100)}%" if r["Prog_Vigente"] > 0 else "—", axis=1)
            df_prog_full["%Peso"] = df_prog_full.apply(lambda r: f"{int(r['Prog_Vigente']/total_vig*100)}%" if total_vig > 0 else "—", axis=1)
            cols = ["Prog_Nombre", "Vigente", "Devengado", "Pagado", "%D/V", "%Peso"]
            st.dataframe(df_prog_full[cols].rename(columns={"Prog_Nombre": "Programa"}), use_container_width=True, hide_index=True)
            st.caption(f"**Total Vigente:** ${total_vig:,.0f}")
        else:
            st.info("No hay datos de programas")

    # Tab Metas
    with tabs[2]:
        st.subheader("Metas")
        if not df_metas.empty:
            cols_show = ["Meta_Nombre", "Meta_Unidad", "Meta_Anual", "Meta_Parcial", "Meta_Ejecutado"]
            cols_exist = [c for c in cols_show if c in df_metas.columns]
            st.dataframe(df_metas[cols_exist], use_container_width=True, hide_index=True)
        else:
            st.info("No hay datos de metas")

    # Tab Tesorería
    with tabs[3]:
        st.subheader("Movimientos de Tesorería")
        if not df_tes.empty:
            cols_show = ["MovTes_TipoResumido", "MovTes_Tipo", "MovTes_Nombre", "MovTes_Importe"]
            cols_exist = [c for c in cols_show if c in df_tes.columns]
            df_tes_display = df_tes[cols_exist].copy()
            if "MovTes_Importe" in df_tes_display.columns:
                df_tes_display["Importe"] = df_tes_display["MovTes_Importe"].apply(lambda x: f"${x:,.0f}")
                df_tes_display = df_tes_display.drop(columns=["MovTes_Importe"])
            st.dataframe(df_tes_display, use_container_width=True, hide_index=True)
            # Resumen
            agg = df_tes.groupby("MovTes_TipoResumido")["MovTes_Importe"].sum()
            st.markdown("**Resumen:**")
            for tipo, val in agg.items():
                st.caption(f"- {tipo}: ${val:,.0f}")
        else:
            st.info("No hay datos de tesorería")

    # Tab Cuentas
    with tabs[4]:
        st.subheader("Cuentas")
        if not df_ctas.empty:
            tot = df_ctas["Cuenta_Importe"].sum()
            df_ctas_display = df_ctas.copy()
            df_ctas_display["Importe"] = df_ctas_display["Cuenta_Importe"].apply(lambda x: f"${x:,.0f}")
            df_ctas_display["%Peso"] = df_ctas_display["Cuenta_Importe"].apply(lambda x: f"{int(x/tot*100)}%" if tot > 0 else "—")
            cols = ["Cuenta_Nombre", "Importe", "%Peso"]
            st.dataframe(df_ctas_display[cols].rename(columns={"Cuenta_Nombre": "Cuenta"}), use_container_width=True, hide_index=True)
            st.caption(f"**Total:** ${tot:,.0f}")
        else:
            st.info("No hay datos de cuentas")

    # Tab Situación Patrimonial
    with tabs[5]:
        st.subheader("Situación Patrimonial")
        if not df_sp.empty:
            df_sp_display = df_sp.copy()
            df_sp_display["Saldo"] = df_sp_display["SitPat_Saldo"].apply(lambda x: f"${x:,.0f}")
            cols_show = ["SitPat_Tipo", "SitPat_Nombre", "Saldo"]
            cols_exist = [c for c in cols_show if c in df_sp_display.columns]
            st.dataframe(df_sp_display[cols_exist].rename(columns={"SitPat_Tipo": "Tipo", "SitPat_Nombre": "Nombre"}), use_container_width=True, hide_index=True)
            # Resumen por tipo
            agg = df_sp.groupby("SitPat_Tipo")["SitPat_Saldo"].sum()
            st.markdown("**Resumen por Tipo:**")
            for tipo, val in agg.items():
                st.caption(f"- {tipo}: ${val:,.0f}")
        else:
            st.info("No hay datos de situación patrimonial")

    # Tab Gastos
    with tabs[6]:
        st.subheader("Gastos Detallados")
        if not df_gastos.empty:
            for c in ["Gasto_Vigente", "Gasto_Devengado", "Gasto_Pagado"]:
                if c in df_gastos.columns:
                    df_gastos[c] = pd.to_numeric(df_gastos[c], errors="coerce").fillna(0)
            df_g = df_gastos.copy()
            df_g["Vigente"] = df_g["Gasto_Vigente"].apply(lambda x: f"${x:,.0f}") if "Gasto_Vigente" in df_g.columns else "—"
            df_g["Devengado"] = df_g["Gasto_Devengado"].apply(lambda x: f"${x:,.0f}") if "Gasto_Devengado" in df_g.columns else "—"
            df_g["Pagado"] = df_g["Gasto_Pagado"].apply(lambda x: f"${x:,.0f}") if "Gasto_Pagado" in df_g.columns else "—"
            cols_show = ["Gasto_Categoria", "Gasto_Objeto", "Vigente", "Devengado", "Pagado"]
            cols_exist = [c for c in cols_show if c in df_g.columns]
            st.dataframe(df_g[cols_exist].rename(columns={"Gasto_Categoria": "Categoría", "Gasto_Objeto": "Objeto"}), use_container_width=True, hide_index=True)
        else:
            st.info("No hay datos de gastos")

    # Tab Recursos
    with tabs[7]:
        st.subheader("Recursos Detallados")
        if not df_recursos.empty:
            for c in ["Rec_Vigente", "Rec_Devengado", "Rec_Percibido"]:
                if c in df_recursos.columns:
                    df_recursos[c] = pd.to_numeric(df_recursos[c], errors="coerce").fillna(0)
            df_r = df_recursos.copy()
            df_r["Vigente"] = df_r["Rec_Vigente"].apply(lambda x: f"${x:,.0f}") if "Rec_Vigente" in df_r.columns else "—"
            df_r["Devengado"] = df_r["Rec_Devengado"].apply(lambda x: f"${x:,.0f}") if "Rec_Devengado" in df_r.columns else "—"
            df_r["Percibido"] = df_r["Rec_Percibido"].apply(lambda x: f"${x:,.0f}") if "Rec_Percibido" in df_r.columns else "—"
            cols_show = ["Rec_Nombre", "Vigente", "Devengado", "Percibido"]
            cols_exist = [c for c in cols_show if c in df_r.columns]
            st.dataframe(df_r[cols_exist].rename(columns={"Rec_Nombre": "Recurso"}), use_container_width=True, hide_index=True)
        else:
            st.info("No hay datos de recursos")


def render_municipio_gastos(
    df_gastos: pd.DataFrame,
    muni_nombre: str,
    doc_periodo: str = "Anual"
):
    """Renderiza gráficos de gastos para el municipio seleccionado."""
    es_proyectado = st.session_state.get("map_normalization", "absoluto") == "proyectado"
    factor_proy = get_factor_proyeccion(doc_periodo) if es_proyectado else 1.0

    if es_proyectado and factor_proy != 1.0:
        st.info(f"📊 **Modo Proyectado**: Valores multiplicados por {factor_proy} (período: {doc_periodo})")

    if df_gastos.empty:
        st.info("No hay datos de gastos para este documento.")
        return

    col1, col2 = st.columns(2)

    # Preparar datos
    for c in ["Gasto_Vigente", "Gasto_Preventivo", "Gasto_Compromiso", "Gasto_Devengado", "Gasto_Pagado"]:
        if c in df_gastos.columns:
            df_gastos[c] = pd.to_numeric(df_gastos[c], errors="coerce").fillna(0)

    with col1:
        st.markdown("**Gastos por Categoría**")
        if "Gasto_Categoria" in df_gastos.columns and "Gasto_Pagado" in df_gastos.columns:
            cat_agg = df_gastos.groupby("Gasto_Categoria")["Gasto_Pagado"].sum().reset_index()
            if es_proyectado:
                cat_agg["Gasto_Pagado"] = cat_agg["Gasto_Pagado"] * factor_proy
            cat_agg = cat_agg[cat_agg["Gasto_Pagado"] > 0]
            if not cat_agg.empty:
                fig = px.pie(
                    cat_agg,
                    values="Gasto_Pagado",
                    names="Gasto_Categoria",
                    hole=0.4
                )
                fig.update_layout(height=300, margin={"l": 0, "r": 0, "t": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**Objetos de Gasto**")
        if "Gasto_Objeto" in df_gastos.columns and "Gasto_Pagado" in df_gastos.columns:
            obj_agg = df_gastos.groupby("Gasto_Objeto")["Gasto_Pagado"].sum().reset_index()
            if es_proyectado:
                obj_agg["Gasto_Pagado"] = obj_agg["Gasto_Pagado"] * factor_proy
            obj_agg = obj_agg.nlargest(10, "Gasto_Pagado")
            if not obj_agg.empty:
                fig = px.pie(
                    obj_agg,
                    values="Gasto_Pagado",
                    names="Gasto_Objeto",
                    hole=0.4,
                    color_discrete_sequence=px.colors.sequential.Reds_r
                )
                fig.update_layout(
                    height=300,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                st.plotly_chart(fig, use_container_width=True)

    # Totales
    sufijo = " (proy.)" if es_proyectado else ""
    st.markdown(f"**Totales{sufijo}**")
    tcols = st.columns(5)

    # Calcular totales (aplicar factor solo a columnas proyectables)
    total_vigente = df_gastos["Gasto_Vigente"].sum() if "Gasto_Vigente" in df_gastos.columns else 0
    total_preventivo = (df_gastos["Gasto_Preventivo"].sum() if "Gasto_Preventivo" in df_gastos.columns else 0) * (factor_proy if es_proyectado else 1)
    total_compromiso = (df_gastos["Gasto_Compromiso"].sum() if "Gasto_Compromiso" in df_gastos.columns else 0) * (factor_proy if es_proyectado else 1)
    total_devengado = (df_gastos["Gasto_Devengado"].sum() if "Gasto_Devengado" in df_gastos.columns else 0) * (factor_proy if es_proyectado else 1)
    total_pagado = (df_gastos["Gasto_Pagado"].sum() if "Gasto_Pagado" in df_gastos.columns else 0) * (factor_proy if es_proyectado else 1)

    with tcols[0]:
        st.metric("Vigente", fmt_money_full(total_vigente))
    with tcols[1]:
        st.metric("Preventivo", fmt_money_full(total_preventivo))
    with tcols[2]:
        st.metric("Compromiso", fmt_money_full(total_compromiso))
    with tcols[3]:
        st.metric("Devengado", fmt_money_full(total_devengado))
    with tcols[4]:
        st.metric("Pagado", fmt_money_full(total_pagado))


def render_municipio_recursos(
    df_recursos: pd.DataFrame,
    muni_nombre: str,
    doc_periodo: str = "Anual"
):
    """Renderiza gráficos de recursos para el municipio seleccionado."""
    es_proyectado = st.session_state.get("map_normalization", "absoluto") == "proyectado"
    factor_proy = get_factor_proyeccion(doc_periodo) if es_proyectado else 1.0

    if es_proyectado and factor_proy != 1.0:
        st.info(f"📊 **Modo Proyectado**: Valores multiplicados por {factor_proy} (período: {doc_periodo})")

    if df_recursos.empty:
        st.info("No hay datos de recursos para este documento.")
        return

    col1, col2 = st.columns(2)

    # Preparar datos
    for c in ["Rec_Vigente", "Rec_Devengado", "Rec_Percibido"]:
        if c in df_recursos.columns:
            df_recursos[c] = pd.to_numeric(df_recursos[c], errors="coerce").fillna(0)

    with col1:
        st.markdown("**Recursos por Tipo**")
        if "Rec_Tipo" in df_recursos.columns and "Rec_Percibido" in df_recursos.columns:
            tipo_agg = df_recursos.groupby("Rec_Tipo")["Rec_Percibido"].sum().reset_index()
            if es_proyectado:
                tipo_agg["Rec_Percibido"] = tipo_agg["Rec_Percibido"] * factor_proy
            tipo_agg = tipo_agg[tipo_agg["Rec_Percibido"] > 0]
            if not tipo_agg.empty:
                fig = px.pie(
                    tipo_agg,
                    values="Rec_Percibido",
                    names="Rec_Tipo",
                    hole=0.4,
                    color_discrete_sequence=px.colors.qualitative.Set2
                )
                fig.update_layout(height=300, margin={"l": 0, "r": 0, "t": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**Top 10 Categorías de Recursos**")
        if "Rec_Categoria" in df_recursos.columns and "Rec_Percibido" in df_recursos.columns:
            cat_agg = df_recursos.groupby("Rec_Categoria")["Rec_Percibido"].sum().reset_index()
            if es_proyectado:
                cat_agg["Rec_Percibido"] = cat_agg["Rec_Percibido"] * factor_proy
            cat_agg = cat_agg.nlargest(10, "Rec_Percibido")
            if not cat_agg.empty:
                fig = px.bar(
                    cat_agg,
                    x="Rec_Percibido",
                    y="Rec_Categoria",
                    orientation="h",
                    color="Rec_Percibido",
                    color_continuous_scale="Greens"
                )
                fig.update_layout(
                    showlegend=False,
                    coloraxis_showscale=False,
                    yaxis={"categoryorder": "total ascending"},
                    height=300,
                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                )
                st.plotly_chart(fig, use_container_width=True)

    # Totales con fórmula especial
    # Fórmula: (categorías principales / 2) + Extrapresupuestario
    sufijo = " (proy.)" if es_proyectado else ""
    st.markdown(f"**Totales{sufijo}**")

    # Calcular totales según fórmula
    if "Rec_Nombre" in df_recursos.columns:
        df_recursos["Rec_Nombre_Norm"] = df_recursos["Rec_Nombre"].str.strip().str.lower()

        categorias_principales = [
            "recursos de capital",
            "ingresos corrientes",
            "fuentes financieras",
            "de libre disponibilidad",
            "afectados"
        ]
        categorias_extra = ["extrapresupuestario", "extrapresupuestarios"]

        df_principales = df_recursos[df_recursos["Rec_Nombre_Norm"].isin(categorias_principales)]
        df_extra = df_recursos[df_recursos["Rec_Nombre_Norm"].isin(categorias_extra)]

        # Vigente (no se proyecta)
        total_vigente = (df_principales["Rec_Vigente"].sum() / 2) + df_extra["Rec_Vigente"].sum()
        # Devengado (se proyecta)
        total_devengado = ((df_principales["Rec_Devengado"].sum() / 2) + df_extra["Rec_Devengado"].sum()) * (factor_proy if es_proyectado else 1)
        # Percibido (se proyecta)
        total_percibido = ((df_principales["Rec_Percibido"].sum() / 2) + df_extra["Rec_Percibido"].sum()) * (factor_proy if es_proyectado else 1)
    else:
        # Fallback si no hay Rec_Nombre
        total_vigente = df_recursos["Rec_Vigente"].sum() if "Rec_Vigente" in df_recursos.columns else 0
        total_devengado = (df_recursos["Rec_Devengado"].sum() if "Rec_Devengado" in df_recursos.columns else 0) * (factor_proy if es_proyectado else 1)
        total_percibido = (df_recursos["Rec_Percibido"].sum() if "Rec_Percibido" in df_recursos.columns else 0) * (factor_proy if es_proyectado else 1)

    tcols = st.columns(3)
    with tcols[0]:
        st.metric("Vigente", fmt_money_full(total_vigente))
    with tcols[1]:
        st.metric("Devengado", fmt_money_full(total_devengado))
    with tcols[2]:
        st.metric("Percibido", fmt_money_full(total_percibido))


# ======================================================
# MAIN
# ======================================================
def main():
    # Asegurar inicialización del estado
    init_session_state()

    # Título dinámico
    if st.session_state.get("municipio_sel"):
        st.title(f"📍 {st.session_state.municipio_nombre}")
        st.caption("Tablero de Comando Municipal")
    else:
        st.title("🗺️ MunicipiosPBA Atlas")
        st.caption("Tablero de Comando Provincial - Provincia de Buenos Aires")

    # Cargar datos base
    with st.spinner("Cargando datos..."):
        geo = load_pba_geojson("data/geo/pba_municipios_optimized.geojson")
        df_base, err_base = fetch_municipios_base()
        df_docs_count, err_docs = fetch_documentos_count()
        df_metrics, err_metrics = fetch_metricas_por_municipio()

    # Verificar conexión
    if err_base:
        st.error(f"Error conectando a Supabase: {err_base}")
        st.info("Verifica la configuración en .streamlit/secrets.toml")
        return

    if df_base.empty:
        st.warning("No se encontraron datos de municipios en la base de datos.")
        return

    # Merge documentos count con base
    if not df_docs_count.empty:
        df_base = df_base.merge(df_docs_count, on="ID_Municipio", how="left")
        df_base["documentos_cargados"] = df_base["documentos_cargados"].fillna(0).astype(int)
    else:
        df_base["documentos_cargados"] = 0

    # Sidebar
    render_sidebar_controls(df_base)

    # Layout principal
    col_map, col_info = st.columns([0.7, 0.3])

    with col_map:
        fig_map = create_choropleth_map(
            geo=geo,
            df_metrics=df_metrics,
            df_base=df_base,
            metric=st.session_state.map_metric,
            normalization=st.session_state.map_normalization,
            selected_georef=st.session_state.municipio_georef
        )
        st.plotly_chart(fig_map, use_container_width=True, key="main_map")

    with col_info:
        st.markdown("### Contexto")

        metric_config = MAP_METRICS[st.session_state.map_metric]
        norm_config = NORMALIZATIONS[st.session_state.map_normalization]

        st.info(f"**Visualizando:** {metric_config['label']} ({norm_config['label']})")

        if st.session_state.municipio_sel:
            # Info del municipio seleccionado
            row = df_base[df_base["ID_Municipio"].astype(str) == str(st.session_state.municipio_sel)]
            if not row.empty:
                r = row.iloc[0]
                st.markdown(f"**Población:** {fmt_num(r.get('Muni_Poblacion_2022', 0))}")
                st.markdown(f"**Superficie:** {fmt_num(r.get('Muni_Superficie', 0))} km²")
                st.markdown(f"**Documentos:** {int(r.get('documentos_cargados', 0))}")
        else:
            # Info provincial
            st.markdown(f"**Municipios:** {len(df_base)}")
            st.markdown(f"**Con datos:** {df_base[df_base['documentos_cargados'] > 0].shape[0]}")
            st.markdown(f"**Población total:** {fmt_num(df_base['Muni_Poblacion_2022'].sum())}")

    st.divider()

    # Contenido según modo
    if st.session_state.municipio_sel is None:
        # === VISTA PROVINCIAL ===
        render_provincial_kpis(df_base, df_metrics, df_docs_count)
        st.divider()
        render_provincial_charts(df_base, df_metrics)
    else:
        # === VISTA MUNICIPIO ===
        render_municipio_kpis(
            df_base,
            df_metrics,
            st.session_state.municipio_sel,
            st.session_state.municipio_nombre
        )

        st.divider()

        # Selector de documento
        df_docs, _ = fetch_documentos_muni(st.session_state.municipio_sel)

        if df_docs.empty:
            st.warning("Este municipio no tiene documentos cargados.")
        else:
            st.subheader("📄 Documentos Disponibles")

            # Crear labels para documentos
            df_docs["__label"] = df_docs.apply(
                lambda r: f"{r.get('Doc_Nombre', '')} - {r.get('Doc_Tipo', '')} {r.get('Doc_Periodo', '')} {r.get('Doc_Anio', '')}",
                axis=1
            )

            doc_sel = st.selectbox(
                "Seleccionar documento:",
                options=df_docs["__label"].tolist()
            )

            if doc_sel:
                doc_row = df_docs[df_docs["__label"] == doc_sel].iloc[0]
                doc_id = str(doc_row["ID_DocumentoCargado"])
                doc_periodo = doc_row.get("Doc_Periodo", "Anual")

                # Cargar datos del documento
                df_gastos, _ = fetch_gastos(doc_id)
                df_recursos, _ = fetch_recursos(doc_id)

                st.divider()

                # Tabs para municipio: Resumen General, Detalle, Gastos, Recursos
                tab_resumen, tab_detalle, tab_gastos, tab_recursos = st.tabs(["📊 Resumen General", "📋 Detalle", "💸 Gastos", "💰 Recursos"])

                with tab_resumen:
                    render_resumen_general(doc_id, df_gastos.copy(), df_recursos.copy(), st.session_state.municipio_nombre, doc_periodo)

                with tab_detalle:
                    render_detalle_tablas(doc_id, df_gastos.copy(), df_recursos.copy())

                with tab_gastos:
                    render_municipio_gastos(df_gastos.copy(), st.session_state.municipio_nombre, doc_periodo)

                with tab_recursos:
                    render_municipio_recursos(df_recursos.copy(), st.session_state.municipio_nombre, doc_periodo)


if __name__ == "__main__":
    main()
