from flask import Flask, render_template, jsonify, request, send_file
import requests, re, io
import fitz
import pandas as pd
from datetime import date

app = Flask(__name__)

KEYWORDS = {
    "DECRETO": ["DECRETO", "DECRETO N°", "DECRETO Nº"],
    "LEY": ["LEY", "LEY N°", "LEY Nº", "LEY PROVINCIAL"],
    "RESOLUCION": ["RESOLUCIÓN", "RESOLUCION", "RES. N°", "RESOLUCION STJ"],
    "ACORDADA": ["ACORDADA", "ACORDADA STJ"],
    "SUCESORIO": ["SUCESORIO", "SUCESORIOS", "SUCESIÓN", "SUCESION", "HEREDERO", "HEREDEROS", "DECLARATORIA DE HEREDEROS"],
    "HONORARIOS": ["HONORARIOS", "REGULACIÓN DE HONORARIOS", "REGULACION", "UMA", "JUS"],
    "CONCURSO": ["CONCURSO", "LLAMADO A CONCURSO", "CONVOCATORIA"],
}

TODAS_PALABRAS = []
for lista in KEYWORDS.values():
    TODAS_PALABRAS.extend(lista)
TODAS_PALABRAS = list(set(TODAS_PALABRAS))

ultima_busqueda_df = pd.DataFrame()

def lista_boletines(limit=30):
    try:
        r = requests.get("https://boletinoficial.rionegro.gov.ar/", timeout=10)
        nums = re.findall(r'(\d{4})\.pdf', r.text)
        unicos = sorted(list(set(map(int, nums))), reverse=True)
        if unicos:
            return unicos[:limit]
    except:
        pass
    return list(range(6540, 6540-limit, -1))

def ultimo_boletin_rn():
    return lista_boletines(1)[0]

def limpiar_frase(t):
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'^[\.\,\;\:\-\–\•\s]+', '', t)
    t = re.sub(r'^[\.\,\;\:]+', '', t)
    if t:
        t = t[0].upper() + t[1:]
    return t

def detectar_tipo_y_fuero(texto, palabra_encontrada):
    t = texto.upper()
    tipo = "OTRO"
    for k, palabras in KEYWORDS.items():
        if palabra_encontrada.upper() in [p.upper() for p in palabras]:
            tipo = k
            break
    m = re.search(r'(DECRETO|LEY|RESOLUCI[OÓ]N|ACORDADA)\s*(?:N°|Nº|Nro\.?)?\s*(\d+/\d+|\d+)', t)
    numero_norma = m.group(0).strip() if m else ""
    if "PENAL" in t or "DELITO" in t: fuero = "Penal"
    elif "LABORAL" in t or "TRABAJO" in t: fuero = "Laboral"
    elif "FAMILIA" in t or "DIVORCIO" in t or "ALIMENTOS" in t: fuero = "Administrativo / Familia"
    else: fuero = "Civil / Comercial"
    return tipo, fuero, numero_norma

def procesar_un_pdf(numero, palabras_extra=[]):
    palabras = TODAS_PALABRAS + [p.strip() for p in palabras_extra if p.strip()]
    url = f"https://rionegro.gov.ar/download/boletin/{numero}.pdf"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        return pd.DataFrame()
    open("temp.pdf","wb").write(r.content)
    doc = fitz.open("temp.pdf")
    filas = []
    for i, page in enumerate(doc):
        texto_pagina = page.get_text()
        for p in palabras:
            for m in re.finditer(re.escape(p.strip()), texto_pagina, re.IGNORECASE):
                inicio = texto_pagina.rfind("\n", 0, m.start())
                if inicio == -1: inicio = max(0, m.start()-200)
                fin1 = texto_pagina.find(".", m.end())
                fin2 = texto_pagina.find(".", fin1+1) if fin1!=-1 else -1
                fin = fin2 if fin2!=-1 else (fin1 if fin1!=-1 else min(len(texto_pagina), m.end()+300))
                snippet_crudo = texto_pagina[inicio:fin+1]
                snippet = limpiar_frase(snippet_crudo)
                if len(snippet) < 20 or "Firmado Digitalmente" in snippet:
                    continue
                tipo, fuero, numero_norma = detectar_tipo_y_fuero(snippet, p)
                if p.strip() in palabras_extra:
                    tipo = "CLIENTE"
                if numero_norma:
                    titulo = numero_norma.title()
                    resumen = snippet
                else:
                    primera_frase = snippet.split(".")[0].strip()
                    if len(primera_frase) > 80:
                        titulo = " ".join(primera_frase.split()[:9]) + "..."
                    else:
                        titulo = primera_frase
                    resumen = snippet
                if titulo.lower() in resumen.lower()[:80]:
                    resumen = resumen[len(titulo):].strip()
                    resumen = limpiar_frase(resumen)
                filas.append({
                    "numero": numero, "pagina": i+1,
                    "tipo": tipo, "palabra": p.upper(),
                    "numero_norma": numero_norma,
                    "titulo": limpiar_frase(titulo),
                    "resumen": limpiar_frase(resumen)[:320],
                    "link_pdf": f"{url}#page={i+1}", "fuero": fuero
                })
                break
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(subset=["numero","pagina","palabra"]).sort_values(["numero","pagina"])
    return df

@app.route("/")
def home():
    lista = lista_boletines(30)
    return render_template("index.html", ultimo=lista[0], lista=lista)

@app.route("/api/buscar")
def api_buscar():
    global ultima_busqueda_df
    numero = int(request.args.get("numero", ultimo_boletin_rn()))
    clientes = request.args.get("clientes","").split(",")
    df = procesar_un_pdf(numero, clientes)
    ultima_busqueda_df = df.copy()
    return jsonify({
        "numero": numero, "total": len(df),
        "resumen_por_tipo": df.groupby("tipo").size().to_dict() if not df.empty else {},
        "resumen_por_fuero": df.groupby("fuero").size().to_dict() if not df.empty else {},
        "resultados": df.to_dict(orient="records") if not df.empty else []
    })

@app.route("/api/buscar_rango")
def api_buscar_rango():
    global ultima_busqueda_df
    desde = int(request.args.get("desde", ultimo_boletin_rn()-5))
    hasta = int(request.args.get("hasta", ultimo_boletin_rn()))
    clientes = request.args.get("clientes","").split(",")
    todos = []
    for n in range(min(desde,hasta), max(desde,hasta)+1):
        df = procesar_un_pdf(n, clientes)
        if not df.empty: todos.append(df)
    if todos:
        df_final = pd.concat(todos, ignore_index=True)
        ultima_busqueda_df = df_final.copy()
        return jsonify({"total": len(df_final), "resultados": df_final.to_dict(orient="records")})
    return jsonify({"total": 0, "resultados": []})

@app.route("/api/filtrar")
def api_filtrar():
    tipo = request.args.get("tipo","Todos")
    fuero = request.args.get("fuero","Todos")
    buscar = request.args.get("buscar","").lower()
    df = ultima_busqueda_df.copy()
    if df.empty:
        return jsonify({"total": 0, "resultados": []})
    if tipo != "Todos":
        df = df[df["tipo"] == tipo]
    if fuero != "Todos":
        df = df[df["fuero"] == fuero]
    if buscar:
        df = df[df["resumen"].str.lower().str.contains(buscar, na=False) | df["titulo"].str.lower().str.contains(buscar, na=False) | df["numero_norma"].str.lower().str.contains(buscar, na=False)]
    return jsonify({"total": len(df), "resultados": df.to_dict(orient="records")})

@app.route("/api/excel")
def api_excel():
    output = io.BytesIO()
    ultima_busqueda_df.to_excel(output, index=False, sheet_name="Boletin")
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="boletin.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
@app.route("/sw.js")
def sw():
    return send_file("static/sw.js", mimetype="application/javascript")

@app.route("/static/manifest.json")
def manifest():
    return send_file("static/manifest.json", mimetype="application/manifest+json")
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)