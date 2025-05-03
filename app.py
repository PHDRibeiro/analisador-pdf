from flask import Flask, render_template, request, redirect, url_for
import os
from werkzeug.utils import secure_filename
import pdfplumber
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Tipos de documentos disponíveis
TIPOS = ['Inicial', 'Informativo CAF', 'Informativo SPPREV', 'Informativo Extratão', 'Litispendência']

# --- Funções auxiliares (ficam acima das rotas que as usam) ---

def cortar_pdf(caminho_pdf, destino):
    reader = PdfReader(caminho_pdf)
    writer = PdfWriter()
    for i in range(min(2, len(reader.pages))):
        writer.add_page(reader.pages[i])
    with open(destino, 'wb') as f:
        writer.write(f)

def extrair_texto(caminho_pdf):
    texto = ""
    with pdfplumber.open(caminho_pdf) as pdf:
        for pagina in pdf.pages:
            texto += pagina.extract_text() + "\n"
    return texto

def processar_arquivos():
    pasta = 'uploads'
    arquivos = os.listdir(pasta)
    resultados = []

    for arquivo in arquivos:
        caminho = os.path.join(pasta, arquivo)

        if '__' not in arquivo:
            continue

        tipo, nome_original = arquivo.split('__', 1)
        tipo = tipo.replace("_", " ")
        caminho_para_processar = caminho

        if tipo not in ['Inicial', 'Litispendência']:
            caminho_temporario = os.path.join(pasta, f"_recorte_{nome_original}")
            cortar_pdf(caminho, caminho_temporario)
            caminho_para_processar = caminho_temporario

        texto = extrair_texto(caminho_para_processar)

        resultado = {
            "arquivo": nome_original,
            "tipo": tipo,
            "texto_resumido": texto[:200] + '...'
        }
        resultados.append(resultado)

        if caminho_para_processar != caminho:
            os.remove(caminho_para_processar)

    return resultados

# --- Rotas ---

@app.route('/')
def index():
    return render_template('index.html', tipos=TIPOS)

@app.route('/upload', methods=['POST'])
def upload():
    arquivos = request.files.getlist('pdfs')
    tipos = request.form.getlist('tipos[]')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    for idx, arquivo in enumerate(arquivos):
        if arquivo and tipos[idx]:
            tipo = tipos[idx].replace(" ", "_")
            nome_seguro = secure_filename(arquivo.filename)
            nome_final = f"{tipo}__{nome_seguro}"
            caminho = os.path.join(app.config['UPLOAD_FOLDER'], nome_final)
            arquivo.save(caminho)

    return redirect(url_for('index'))

@app.route('/analisar')
def analisar():
    resultados = processar_arquivos()
    return render_template('resultado.html', resultados=resultados)

# --- Execução ---
if __name__ == '__main__':
    app.run(debug=True)
