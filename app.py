from flask import Flask, render_template, request, redirect, url_for
import os
from werkzeug.utils import secure_filename
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pandas as pd
import unicodedata

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Tipos de documentos disponíveis
TIPOS = ['Inicial', 'Informativo CAF', 'Informativo SPPREV', 'Informativo Extratão', 'Litispendência']

# --- Funções auxiliares ---

def normalizar_nome(nome):
    nome = nome.upper()
    nome = unicodedata.normalize('NFKD', nome).encode('ASCII', 'ignore').decode('utf-8')
    return nome.strip()

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
    texto = texto.replace('\n', ' ')
    padrao = r'([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{5,}(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]+){1,5}),\s*RG\s*n[\u00bao]?\s*(\d{1,3}[.\dXx-]*)\s*e\s*CPF\s*n[\u00bao]?\s*(\d{3}\.\d{3}\.\d{3}-\d{2})'
    matches = re.findall(padrao, texto)
    for nome, rg, cpf in matches:
        resultados.append({"nome": nome.strip(), "rg": rg.strip(), "cpf": cpf.strip()})
    return resultados

def parse_extratao(texto):
    import re
    resultado = {"nome": "NOME NÃO ENCONTRADO", "cpf": "CPF NÃO ENCONTRADO"}
    texto = texto.replace('\n', ' ')
    match_cpf = re.search(r'\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b', texto)
    if match_cpf:
        resultado["cpf"] = match_cpf.group(1)
    match_nome = re.search(r'NOME\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{3,})\s+\d{11}', texto)
    if match_nome:
        resultado["nome"] = match_nome.group(1).strip()
    return [resultado]

def parse_caf(texto):
    import re

    resultado = {
        "nome": "NOME NÃO ENCONTRADO",
        "rg": "RG NÃO ENCONTRADO"
    }

    texto = texto.replace('\n', ' ')

    # Pega o nome do autor
    match_nome = re.search(r'Autor\s*:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{3,})', texto)
    if match_nome:
        resultado["nome"] = match_nome.group(1).strip()

    # Pega o RG (após "RG:" ou "RG :" com 8 a 11 dígitos)
    match_rg = re.search(r'RG\s*[:\s]?\s*(\d{8,11})', texto)
    if match_rg:
        resultado["rg"] = match_rg.group(1).lstrip('0')  # remove zeros à esquerda

    return [resultado]

def extrair_paginas_arquivo(nome_arquivo):
    import re
    match = re.search(r'pag[_ ]?(\d{2,5})\D+(\d{2,5})', nome_arquivo)
    return f"{match.group(1)}–{match.group(2)}" if match else ""

def processar_arquivos():
    pasta = 'uploads'
    arquivos = os.listdir(pasta)

    dados_inicial = []
    extratao_encontrados = []
    caf_encontrados = []

    for arquivo in arquivos:
        caminho = os.path.join(pasta, arquivo)

        if '__' not in arquivo:
            continue

        tipo, nome_original = arquivo.split('__', 1)
        tipo = tipo.replace("_", " ")
        caminho_para_processar = caminho
        # Se não for Inicial ou Litispendência, recorta as 2 primeiras páginas para agilizar
        if tipo not in ['Inicial', 'Litispendência']:
            print(f"Cortando as 2 primeiras páginas de: {nome_original}")
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
        elif tipo == 'Informativo CAF':
            registros = parse_caf(texto)
            for dado in registros:
                dado["arquivo"] = nome_original
                dado["paginas"] = extrair_paginas_arquivo(nome_original)
                caf_encontrados.append(dado)

    # Cria DataFrame base com dados da Inicial
    df = pd.DataFrame(dados_inicial)
    df.insert(0, 'ID', range(1, len(df) + 1))

    for inf in ['Extratão', 'CAF', 'SPPREV']:
        df[f'Informativo {inf}'] = 'Não'
        df[f'Página {inf}'] = ''

    # Cruzamento com Extratão (por CPF)
    for idx, row in df.iterrows():
        cpf_df = str(row['cpf']).replace('.', '').replace('-', '').strip()
        for registro in extratao_encontrados:
            cpf_extra = str(registro['cpf']).replace('.', '').replace('-', '').strip()
            if cpf_df == cpf_extra:
                df.at[idx, 'Informativo Extratão'] = 'Sim'
                df.at[idx, 'Página Extratão'] = registro.get('paginas', '')
                break

    # Cruzamento com CAF por RG (com tolerância) ou, em último caso, por nome
    for idx, row in df.iterrows():
        rg_df = str(row.get('rg', '')).replace('.', '').replace('-', '').upper().lstrip('0')
        nome_df = normalizar_nome(row.get('nome', ''))

        encontrou = False

        for registro in caf_encontrados:
            rg_caf = str(registro['rg']).replace('.', '').replace('-', '').upper().lstrip('0')
            print(f"Comparando RGs → Inicial: {rg_df} | CAF: {rg_caf}")

            if (
                rg_df[:6] == rg_caf[:6] or
                rg_df[:7] == rg_caf[:7] or
                rg_df[:8] == rg_caf[:8]
            ):
                df.at[idx, 'Informativo CAF'] = 'Sim'
                df.at[idx, 'Página CAF'] = registro.get('paginas', '')
                encontrou = True
                break

        if not encontrou:
            for registro in caf_encontrados:
                nome_caf = normalizar_nome(registro.get('nome', ''))
                print(f"Cruzando por nome: {nome_df} vs {nome_caf}")

                if nome_df in nome_caf or nome_caf in nome_df:
                    df.at[idx, 'Informativo CAF'] = 'Sim'
                    df.at[idx, 'Página CAF'] = registro.get('paginas', '')
                    break


    # Padroniza capitalização das colunas
    df.columns = [col.capitalize() for col in df.columns]
    return df.to_dict(orient='records')

@app.route('/')
def index():
    arquivos = os.listdir(app.config['UPLOAD_FOLDER']) if os.path.exists(app.config['UPLOAD_FOLDER']) else []
    tipos_encontrados = [arq.split('__', 1)[0].replace("_", " ") for arq in arquivos if '__' in arq]
    pode_analisar = 'Inicial' in tipos_encontrados and any(t in tipos_encontrados for t in ['Informativo Extratão', 'Informativo CAF', 'Informativo SPPREV'])
    return render_template('index.html', tipos=TIPOS, pode_analisar=pode_analisar, arquivos=arquivos)

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

@app.route('/limpar_uploads', methods=['POST'])
def limpar_uploads():
    pasta = app.config['UPLOAD_FOLDER']
    if os.path.exists(pasta):
        for arquivo in os.listdir(pasta):
            caminho = os.path.join(pasta, arquivo)
            if os.path.isfile(caminho):
                os.remove(caminho)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
