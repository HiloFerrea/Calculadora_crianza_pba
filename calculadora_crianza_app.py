import requests
import pandas as pd
from io import BytesIO
import re
from bs4 import BeautifulSoup
import streamlit as st


def formato_ar(numero):
    """
    Formato argentino:
    1234567.89 -> '1.234.568'
    (sin decimales para montos de dinero en este caso)
    """
    return f"{numero:,.0f}".replace(",", "@").replace(".", ",").replace("@", ".")


# ------------------------------------------------------------
# 1. DATOS INDEC – CBA GBA
# ------------------------------------------------------------
@st.cache_data
def obtener_cba_gba_indec():
    url = "https://www.indec.gob.ar/ftp/cuadros/sociedad/serie_cba_cbt.xls"
    resp = requests.get(url)
    resp.raise_for_status()

    df = pd.read_excel(BytesIO(resp.content), sheet_name=0, skiprows=5)
    df.columns = [str(c).strip() for c in df.columns]

    df = df.rename(columns={df.columns[0]: "Fecha", df.columns[1]: "CBA_GBA"})
    df = df.dropna(subset=["Fecha", "CBA_GBA"])
    df["Fecha"] = pd.to_datetime(df["Fecha"])
    df = df.sort_values("Fecha")

    ultimo = df.iloc[-1]
    return ultimo["Fecha"], float(ultimo["CBA_GBA"])


# ------------------------------------------------------------
# 1.1 DATOS INDEC – CANASTA DE CRIANZA (ByS / TC / Total)
# ------------------------------------------------------------

@st.cache_data
def obtener_canasta_crianza_indec():
    url = "https://www.indec.gob.ar/ftp/cuadros/sociedad/serie_canasta_crianza.xlsx"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # Leer con encabezados multinivel
    df = pd.read_excel(BytesIO(resp.content), sheet_name=0, header=[3, 4])

    # --- Aplanar nombres de columnas
    def col_to_str(col):
        if isinstance(col, tuple):
            partes = [
                str(x).strip()
                for x in col
                if str(x).strip().lower() not in ("nan", "none")
            ]
            return " | ".join(partes)
        return str(col).strip()

    df.columns = [col_to_str(c) for c in df.columns]

    # --- Tomar Año y Mes como primeras 2 columnas
    col_anio = df.columns[0]
    col_mes = df.columns[1]
    df = df.rename(columns={col_anio: "Año", col_mes: "Mes"})

    # --- Helper para encontrar columnas por contenido
    def find_col(contains_all):
        for c in df.columns:
            s = c.lower()
            if all(token.lower() in s for token in contains_all):
                return c
        raise KeyError(f"No se encontró columna que contenga: {contains_all}")

    # ------------------------------------------------------------
    # BUSCAR COLUMNAS ByS / TC / Total por grupo etario
    # (en el Excel los encabezados son: Bienes y servicios | Cuidado | Total)
    # ------------------------------------------------------------
    grupos = {
        "menor1": ["menor"],
        "1-3": ["1", "3"],
        "4-5": ["4", "5"],
        "6-12": ["6", "12"],
    }

    cols = {}
    for g, tok in grupos.items():
        cols[g] = {
            "ByS": find_col(tok + ["bienes"]),   # bienes y servicios
            "TC":  find_col(tok + ["cuidado"]),  # cuidado
            "Total": find_col(tok + ["total"]),
        }

    # Quedarnos con lo esencial (Año, Mes + columnas encontradas)
    keep = ["Año", "Mes"]
    for g in grupos.keys():
        keep += [cols[g]["ByS"], cols[g]["TC"], cols[g]["Total"]]

    df = df[keep].copy()

    # Renombrar a nombres "normales"
    ren = {"Año": "Año", "Mes": "Mes"}
    for g in grupos.keys():
        ren[cols[g]["ByS"]] = f"{g}_ByS"
        ren[cols[g]["TC"]] = f"{g}_TC"
        ren[cols[g]["Total"]] = f"{g}_Total"

    df = df.rename(columns=ren)

    # ------------------------------------------------------------
    # CONSTRUCCIÓN CORRECTA DE LA FECHA (AÑO SOLO EN ENERO → FFILL)
    # ------------------------------------------------------------
    meses = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "setiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12
    }

    df["Año"] = pd.to_numeric(df["Año"], errors="coerce")
    df["Año"] = df["Año"].ffill()

    df["Mes_num"] = (
        df["Mes"]
        .astype(str)
        .str.strip()
        .str.lower()
        .map(meses)
    )
    df["Mes_num"] = pd.to_numeric(df["Mes_num"], errors="coerce")

    df["Fecha"] = pd.to_datetime(
        dict(year=df["Año"], month=df["Mes_num"], day=1),
        errors="coerce"
    )

    # Numéricos (todas las columnas de canasta)
    cols_numericas = []
    for g in grupos.keys():
        cols_numericas += [f"{g}_ByS", f"{g}_TC", f"{g}_Total"]

    for c in cols_numericas:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Limpiar y ordenar
    df = df.dropna(subset=["Fecha"] + cols_numericas)
    df = df.sort_values("Fecha")

    if df.empty:
        raise ValueError(
            "INDEC: la tabla quedó vacía tras limpieza (cambió el formato o encabezados)."
        )

    ultimo = df.iloc[-1]

    return {
        "Fecha": ultimo["Fecha"],
        "menor1": {
            "ByS": float(ultimo["menor1_ByS"]),
            "TC": float(ultimo["menor1_TC"]),
            "Total": float(ultimo["menor1_Total"]),
        },
        "1-3": {
            "ByS": float(ultimo["1-3_ByS"]),
            "TC": float(ultimo["1-3_TC"]),
            "Total": float(ultimo["1-3_Total"]),
        },
        "4-5": {
            "ByS": float(ultimo["4-5_ByS"]),
            "TC": float(ultimo["4-5_TC"]),
            "Total": float(ultimo["4-5_Total"]),
        },
        "6-12": {
            "ByS": float(ultimo["6-12_ByS"]),
            "TC": float(ultimo["6-12_TC"]),
            "Total": float(ultimo["6-12_Total"]),
        },
    }

# ------------------------------------------------------------
# 2. DATOS UPACP – 4° CATEGORÍA CON RETIRO
# ------------------------------------------------------------
def parse_monto(s):
    return float(s.replace(".", "").replace(",", "."))


@st.cache_data
def obtener_upacp():
    url = "https://upacp.org.ar/?page_id=26745"
    resp = requests.get(url)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    idx = text.find("CUARTA CATEGORIA")
    bloque = text[idx: idx + 1200]

    m = re.search(r"Hora:\s*\$?([\d\.,]+)\s*Mensual:\s*\$?([\d\.,]+)", bloque)
    valor_hora = parse_monto(m.group(1))
    mensual = parse_monto(m.group(2))

    return valor_hora, mensual


# ------------------------------------------------------------
# 3. METODOLOGÍA DE CRIANZA
# ------------------------------------------------------------
escala_bienes = {
    "menor1": 0.298,
    "1-3": 0.298,
    "4-5": 0.298,
    "6-11": 0.577,
    "12-17": 0.647,
}

horas_cuidado = {
    "menor1": 129,
    "1-3": 66,
    "4-5": 52,
    "6-11": 57,
    "12-17": 24,
}


def grupo_edad(e):
    if e < 1:
        return "menor1"
    if 1 <= e <= 3:
        return "1-3"
    if 4 <= e <= 5:
        return "4-5"
    if 6 <= e <= 11:
        return "6-11"
    if 12 <= e <= 17:
        return "12-17"
    return None


def costo_crianza(edades, cba_gba, hora_upacp, mensual_upacp, icg=3.14, ae=1.7):
    gasto_ref = cba_gba * icg * ae
    valor_hora_24_mas = round(mensual_upacp / (6 * 30.5))

    costos = []
    for edad in edades:
        g = grupo_edad(edad)
        if g is None:
            continue

        bienes = escala_bienes[g] * gasto_ref

        if g == "menor1":
            valor_hora = valor_hora_24_mas
        else:
            valor_hora = hora_upacp

        tiempo = horas_cuidado[g] * valor_hora
        total_ind = bienes + tiempo
        costos.append((edad, g, bienes, tiempo, total_ind))

    # Ordenar por costo individual
    costos.sort(key=lambda x: x[4], reverse=True)

    factores = [1.0, 0.7] + [0.5] * 10

    total = 0
    detalles = []
    for i, (edad, g, bienes, tiempo, total_ind) in enumerate(costos):
        factor = factores[i]
        ajustado = round(total_ind * factor)
        total += ajustado

        detalles.append({
            "Edad": edad,
            "Grupo": g,
            "Bienes": round(bienes),
            "Tiempo": round(tiempo),
            "Total individual": round(total_ind),
            "Factor escala": factor,
            "Costo ajustado": ajustado
        })

    return total, detalles

def costos_individuales_por_grupo(cba_gba, hora_upacp, mensual_upacp, icg=3.14, ae=1.7):
    """
    Devuelve costo individual (sin escala) por grupo etario de TU metodología:
    bienes + tiempo, para un (1) niño/a en ese grupo.
    """
    gasto_ref = cba_gba * icg * ae
    valor_hora_24_mas = round(mensual_upacp / (6 * 30.5))

    costos = {}
    for g in escala_bienes.keys():
        bienes = escala_bienes[g] * gasto_ref

        if g == "menor1":
            valor_hora = valor_hora_24_mas
        else:
            valor_hora = hora_upacp

        tiempo = horas_cuidado[g] * valor_hora
        total_ind = round(bienes + tiempo)

        costos[g] = {
            "Bienes": round(bienes),
            "Tiempo": round(tiempo),
            "Total": total_ind
        }

    return costos



if "calc_done" not in st.session_state:
    st.session_state.calc_done = False



# ------------------------------------------------------------
# 4. INTERFAZ STREAMLIT
# ------------------------------------------------------------

st.markdown("""
<h1 style='text-align: center;'>Calculadora del costo de la crianza</h1>
<h3 style='text-align: center;'>Provincia de Buenos Aires</h3>
""", unsafe_allow_html=True)


texto_introduccion = """
<div style='text-align: justify'>
Esta herramienta estima el costo mensual de la crianza de niñas, niños y adolescentes (NNyA) en la provincia de Buenos Aires.
La metodología utilizada valora tanto los <b>bienes y servicios</b> (ByS) necesarios para su desarrollo como el <b>tiempo de cuidado</b>,
(TC) a partir de información proveniente de fuentes oficiales y de las remuneraciones vigentes del trabajo doméstico.

Esta herramienta integra datos de:
<ul>
<li><b>INDEC - </b> Instituto Nacional de Estadística y Censos</li>
<li><b>UPACP - </b> Unión Personal Auxiliar de Casas Particulares</li>
</ul>
</div>
"""

st.markdown(texto_introduccion, unsafe_allow_html=True)


st.markdown("<h2 style='text-align: center;'>Ingresá las edades de los niños/as</h2>", unsafe_allow_html=True)

n = st.number_input("Cantidad de hijos/as", min_value=0, max_value=10, value=1, step=1)

edades = []
for i in range(n):
    e = st.number_input(f"Edad del hijo/a {i+1}", min_value=0.0, max_value=17.0, step=1.0)
    edades.append(e)

clicked = st.button("Calcular")

if clicked:
    if n == 0:
        st.warning("Ingresá al menos un niño/a.")
    else:
        # -----------------------------
        # CALCULAR 1 VEZ Y GUARDAR
        # -----------------------------
        fecha_cba, cba_gba = obtener_cba_gba_indec()
        valor_hora, salario_mensual = obtener_upacp()
        total, detalle = costo_crianza(edades, cba_gba, valor_hora, salario_mensual)

        # comparación INDEC: calcular 1 vez y guardar también
        indec = obtener_canasta_crianza_indec()
        costos_pba = costos_individuales_por_grupo(cba_gba, valor_hora, salario_mensual)

        # detectar tramos (1 vez)
        has_menor1 = any(e < 1 for e in edades)
        has_1_3    = any(1 <= e <= 3 for e in edades)
        has_4_5    = any(4 <= e <= 5 for e in edades)
        has_6_11   = any(6 <= e <= 11 for e in edades)
        has_12_17  = any(12 <= e <= 17 for e in edades)

        filas = []

        def agregar_fila(label, g_indec, g_pba, comparable=True):
            if comparable:
                indec_bys = float(indec[g_indec]["ByS"])
                indec_tc  = float(indec[g_indec]["TC"])
                indec_tot = float(indec[g_indec]["Total"])
            else:
                indec_bys = indec_tc = indec_tot = None

            pba_bys = float(costos_pba[g_pba]["Bienes"])
            pba_tc  = float(costos_pba[g_pba]["Tiempo"])
            pba_tot = float(costos_pba[g_pba]["Total"])

            filas.append({
                "Grupo": label,
                "INDEC_ByS": indec_bys,   "PBA_ByS": pba_bys,
                "INDEC_TC": indec_tc,     "PBA_TC": pba_tc,
                "INDEC_Total": indec_tot, "PBA_Total": pba_tot,
            })

        if has_menor1:
            agregar_fila("INDEC - < 1", "menor1", "menor1", True)
        if has_1_3:
            agregar_fila("INDEC - 1 a 3", "1-3", "1-3", True)
        if has_4_5:
            agregar_fila("INDEC - 4 a 5", "4-5", "4-5", True)
        if has_6_11:
            agregar_fila("INDEC - 6 a 12 (vs PBA 6 a 11)", "6-12", "6-11", True)
        if has_12_17:
            agregar_fila("PBA - 12 a 17 (sin equivalente INDEC)", None, "12-17", False)

        base = pd.DataFrame(filas) if filas else pd.DataFrame()

        st.session_state.calc_done = True
        st.session_state.result = {
            "fecha_cba": fecha_cba,
            "cba_gba": cba_gba,
            "valor_hora": valor_hora,
            "salario_mensual": salario_mensual,
            "total": total,
            "detalle": detalle,
            "indec": indec,
            "base": base,
        }

# ------------------------------------------------------------
# MOSTRAR RESULTADOS (NO RECALCULA)
# ------------------------------------------------------------
if st.session_state.calc_done:
    r = st.session_state.result

    fecha_cba = r["fecha_cba"]
    cba_gba = r["cba_gba"]
    valor_hora = r["valor_hora"]
    salario_mensual = r["salario_mensual"]
    total = r["total"]
    detalle = r["detalle"]
    indec = r["indec"]
    base = r["base"]

    st.markdown("<h2 style='text-align: center;'>Datos utilizados</h2>", unsafe_allow_html=True)

    valor_hora_24_mas = round(salario_mensual / (6 * 30.5))

    st.markdown("""
    <style>
    .box {
        padding: 15px;
        border-radius: 10px;
        background-color: #f7f7f7;
        border: 1px solid #cccccc;
        margin-bottom: 15px;
        width: 70%;
        margin-left: auto;
        margin-right: auto;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="box">
    <h4 style="text-align:center;">INDEC</h4>
    <b>Canasta Básica Alimentaria (CBA) de la región GBA</b><br>
    <small><i>Descarga completa</i></small><br>
    <b>Región:</b> Gran Buenos Aires (GBA) <br>
    <b>Último período disponible:</b> {fecha_cba.strftime('%Y-%m')} <br>
    <b>CBA adulto equivalente:</b> ${formato_ar(cba_gba)}
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="box">
      <h4 style="text-align:center;">UPACP</h4>
      <b>Escala salarial de la 4° categoría con retiro*</b><br>
      <small><i>Descarga completa</i></small><br>
      <b>Valor hora (&lt; 24 hs semanales):</b> ${formato_ar(valor_hora)}<br>
      <b>Valor hora (≥ 24 hs semanales**):</b> ${formato_ar(valor_hora_24_mas)}<br>
      <p><small>*Resolución vigente</small> <br>
      <small>**Sueldo mensual / (6 días × 30,5 h promedio)</small>
    </div>
    """, unsafe_allow_html=True)

    st.success(f"**Costo total mensual del hogar: ${formato_ar(total)}**")

    df_detalle = pd.DataFrame(detalle)
    df_detalle["Edad"] = df_detalle["Edad"].astype(int)
    df_detalle["Factor escala"] = df_detalle["Factor escala"].round(1)

    fila_total = {
        "Edad": "",
        "Grupo": "Total hogar",
        "Bienes": df_detalle["Bienes"].sum(),
        "Tiempo": df_detalle["Tiempo"].sum(),
        "Total individual": df_detalle["Total individual"].sum(),
        "Factor escala": "",
        "Costo ajustado": df_detalle["Costo ajustado"].sum()
    }

    df_detalle = pd.concat([df_detalle, pd.DataFrame([fila_total])], ignore_index=True)
    df_mostrar = df_detalle.copy().rename(columns={"Bienes": "ByS", "Tiempo": "TC"})
    columnas_dinero = ["ByS", "TC", "Total individual", "Costo ajustado"]

    for col in columnas_dinero:
        df_mostrar[col] = df_mostrar[col].apply(lambda x: formato_ar(x) if isinstance(x, (int, float)) else x)

    df_mostrar["Factor escala"] = df_mostrar["Factor escala"].apply(
        lambda x: f"{x:.1f}".replace(".", ",") if isinstance(x, (int, float)) else ""
    )

    def resaltar_total(row):
        return ["font-weight: bold; background-color: #e0e0e0"] * len(row) if row["Grupo"] == "Total hogar" else [""] * len(row)

    st.subheader("Detalle por niño/a")
    st.dataframe(
        df_mostrar.style
        .set_properties(**{"text-align": "right"}, subset=columnas_dinero + ["Factor escala"])
        .apply(resaltar_total, axis=1),
        use_container_width=True
    )

    st.markdown(
        """
        <p style="text-align: justify; font-size: 0.8rem; color: rgba(49, 51, 63, 0.6);">
        El modelo estima el costo mensual diferenciado por edad y aplica factores de economía de escala.
        El NNyA con mayor costo recibe un factor igual a 1, mientras que los restantes reciben factores de 0,7 y 0,5.
        Este criterio normativo permite reconocer plenamente los gastos de mayor incidencia y asegurar
        una estimación proporcional de los costos asociados al resto de los integrantes.
    </p>
    """,
    unsafe_allow_html=True
)


    
    st.subheader("Comparación con INDEC")

    if base.empty:
        st.info("No hay tramos para mostrar según las edades ingresadas.")
    else:

        st.caption(
            f"El Instituto Nacional de Estadística y Censos (INDEC) difunde mensualmente la valorización de la canasta de crianza para la primera infancia, la niñez y la adolescencia, elaborada a partir de los lineamientos metodológicos desarrollados por la Dirección Nacional de Economía, Igualdad y Género del Ministerio de Economía y UNICEF (2023)." )
        st.caption(
            f"Con el fin de contextualizar los resultados y aportar una perspectiva más amplia, se incluyen a continuación las estimaciones de los cotos a partir de esta metodología.")
        st.caption(
            f"Ambos enfoques permiten contrastar supuestos y criterios, enriqueciendo el análisis y favoreciendo comparaciones." )

        st.markdown(
            f"""
            <p style='text-align: justify;'>
                <b><small>Nota:</b> último período disponible {pd.to_datetime(indec['Fecha']).strftime('%Y-%m')}. 
                Se muestran únicamente los tramos presentes en las edades ingresadas. 
                Para 6–12 (INDEC) se contrasta con 6–11 (PBA).
        </p>
        """,
        unsafe_allow_html=True
        )

      
        def tabla_corta(df, col_indec, col_pba, titulo):
            t = df[["Grupo", col_indec, col_pba]].copy()
            t.columns = ["Grupo", "INDEC ($/mes)", "PBA ($/mes)"]
            t["Diferencia ($)"] = t["PBA ($/mes)"] - t["INDEC ($/mes)"]
            t["Diferencia (%)"] = (t["Diferencia ($)"] / t["INDEC ($/mes)"]) * 100

            mask_no_indec = t["INDEC ($/mes)"].isna()
            t.loc[mask_no_indec, ["Diferencia ($)", "Diferencia (%)"]] = None

            show = t.copy()
            for c in ["INDEC ($/mes)", "PBA ($/mes)", "Diferencia ($)"]:
                show[c] = show[c].apply(lambda x: f"${formato_ar(x)}" if pd.notna(x) else "")

            show["Diferencia (%)"] = show["Diferencia (%)"].apply(
                lambda x: f"{x:.1f}%".replace(".", ",") if pd.notna(x) else ""
            )

            st.markdown(f"**{titulo}**")
            st.dataframe(show, use_container_width=True)

        tabla_corta(base, "INDEC_Total", "PBA_Total", "Canasta Total (ByS + TC)")

        ver_desagregado = st.checkbox("Ver desagregación (ByS y TC)", value=False)
        if ver_desagregado:
            tabla_corta(base, "INDEC_ByS", "PBA_ByS", "Canasta de Bienes y Servicios (ByS)")
            tabla_corta(base, "INDEC_TC", "PBA_TC", "Canasta de Tiempo de Cuidado (TC)")

        # ------------------------------------------------------------
        # MATERIALES DE REFERENCIA (AHORA: SOLO DESPUÉS DEL CÁLCULO)
        # ------------------------------------------------------------
        st.write("### Materiales de referencia")

        st.markdown(
            "<p style='text-align: justify;'><b>Metodología PBA</p>",
            unsafe_allow_html=True
        )

        st.markdown(
            "- [Estimación del costo de la crianza en la provincia de Buenos Aires. Informe metodológico](https://drive.google.com/file/d/1DO4iKByfFdBD-c1EWJ7vfkamEJwbGOeg/view?usp=drive_link)"
        )

        st.markdown(
            "<p style='text-align: justify;'><b>Metodología INDEC</p>",
            unsafe_allow_html=True
        )

        st.markdown(
            "- [Costo de consumos y cuidados de la primera infancia, la niñez y la adolescencia. Una aproximación metodológica](https://www.argentina.gob.ar/sites/default/files/2023/06/metodologia_costo_de_consumos_y_cuidados.pdf)"
        )

        st.markdown(
            "- [Estimación del costo en tiempo de cuidados de niñas y niños (UNICEF – DNEIyG)](https://www.argentina.gob.ar/sites/default/files/2023/06/unicef_dneig_06-23_estimacion_del_costo_en_tiempo_de_cuidados.pdf)"
        )

        st.markdown(
           """
               <div style="
                    border: 1px solid #ccc;
                    padding: 12px 20px;
                    border-radius: 10px;
                    background-color: #f9f9f9;
                    font-size: 0.9em;
                    text-align: center;
                    max-width: 600px;
                    margin: auto;
                ">
                    <small>Herramienta desarrollada por <strong>Hilario Ferrea</strong><br>
                    <strong>Departamento de Analisis de las Estadisticas Sociales<br>
                    <p>Dirección Provincial de Estadística - Ministerio de Economía de la Provincia de Buenos Aires -<br>
                    <strong>Contacto:</strong> hiloferrea@gmail.com — hferrea@estadistica.ec.gba.gov.ar
                </div>
                """,
                unsafe_allow_html=True
        )
