from flask import Flask, render_template, request, redirect, url_for
import os
from werkzeug.utils import secure_filename
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pandas as pd

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

def parse_inicial(texto):
    import re

    resultados = []

    # Pré-processamento: junta quebras de linha em nomes (mantém parágrafos)
    texto = texto.replace('\n', ' ')

    # Padrão robusto para encontrar pares Nome + CPF
    padrao = r'([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{5,}(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]+){1,5}),\s*RG\s*n[ºo]?\s*\d{1,3}[.\dXx-]*.*?CPF\s*n[ºo]?\s*(\d{3}\.\d{3}\.\d{3}-\d{2})'

    matches = re.findall(padrao, texto)

    for nome, cpf in matches:
        resultados.append({
            "nome": nome.strip(),
            "cpf": cpf.strip()
        })

    return resultados

def parse_extratao(texto):
    import re

    resultado = {
        "nome": "NOME NÃO ENCONTRADO",
        "cpf": "CPF NÃO ENCONTRADO"
    }

    # Remove quebras de linha internas
    texto = texto.replace('\n', ' ')

    # Padrão para encontrar CPF
    match_cpf = re.search(r'\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b', texto)
    if match_cpf:
        resultado["cpf"] = match_cpf.group(1)

    # Padrão para nome: depois da palavra "NOME" e antes de números
    match_nome = re.search(r'NOME\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{3,})\s+\d{11}', texto)
    if match_nome:
        resultado["nome"] = match_nome.group(1).strip()

    return [resultado]

def extrair_paginas_arquivo(nome_arquivo):
    import re
    match = re.search(r'pag[_ ]?(\d{2,5})\D+(\d{2,5})', nome_arquivo)
    if match:
        return f"{match.group(1)}–{match.group(2)}"
    return ""

def processar_arquivos():
    pasta = 'uploads'
    arquivos = os.listdir(pasta)

    dados_inicial = []
    extratao_encontrados = []

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

        if tipo == 'Inicial':
            dados_inicial.extend(parse_inicial(texto))
        elif tipo == 'Informativo Extratão':
            extratos = parse_extratao(texto)
            for dado in extratos:
                dado["arquivo"] = nome_original
                dado["paginas"] = extrair_paginas_arquivo(nome_original)
                extratao_encontrados.append(dado)

        if caminho_para_processar != caminho:
            os.remove(caminho_para_processar)

    # Cria DataFrame base com dados da Inicial
    df = pd.DataFrame(dados_inicial)

    # Adiciona numeração
    df.insert(0, 'ID', range(1, len(df) + 1))

    # Adiciona colunas dos informativos com valores padrão
    for inf in ['Extratão', 'CAF', 'SPPREV']:
        df[f'Informativo {inf}'] = 'Não'
        df[f'Página {inf}'] = ''

    # Cruzamento com dados do Extratão por CPF (normalizado)
    for idx, row in df.iterrows():
        cpf_df = str(row['cpf']).replace('.', '').replace('-', '').strip()
        for registro in extratao_encontrados:
            cpf_extra = str(registro['cpf']).replace('.', '').replace('-', '').strip()

            # Aqui está o print de comparação
            print(f"Comparando: {cpf_df} vs {cpf_extra}")

            if cpf_df == cpf_extra:
                df.at[idx, 'Informativo Extratão'] = 'Sim'
                df.at[idx, 'Página Extratão'] = registro.get('paginas', '')
                break

    # Padroniza capitalização das colunas
    df.columns = [col.capitalize() for col in df.columns]

    return df.to_dict(orient='records')

# --- Rotas ---

@app.route('/')
def index():
    return render_template('index.html', tipos=TIPOS)

@app.route('/upload', methods=['POST'])
def upload():
    arquivos = request.files.getlist('pdfs')
    tipo = request.form.get('tipo')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    for arquivo in arquivos:
        if arquivo and tipo:
            tipo_limpo = tipo.replace(" ", "_")
            nome_seguro = secure_filename(arquivo.filename)
            nome_final = f"{tipo_limpo}__{nome_seguro}"
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
