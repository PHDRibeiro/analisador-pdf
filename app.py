from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
import os
from werkzeug.utils import secure_filename
import pdfplumber
from pypdf import PdfReader, PdfWriter
import pandas as pd
import unicodedata
import re
import json
from datetime import datetime
import openai
from dotenv import load_dotenv

print("==== TODAS AS VARIÁVEIS DE AMBIENTE ====")
import os
for k, v in os.environ.items():
    if 'KEY' in k:
        print(f"{k}: {v[:10]}...")  # Mostra apenas os primeiros 10 caracteres por segurança
print("=======================================")

# Caminho absoluto para o arquivo .env
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
RESULTS_FOLDER = 'resultados'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['RESULTS_FOLDER'] = RESULTS_FOLDER

# Configuração da API OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

# Assegura que as pastas existam
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULTS_FOLDER, exist_ok=True)

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
            texto_pagina = pagina.extract_text()
            if texto_pagina:  # Verifica se o texto não é None
                texto += texto_pagina + "\n"
    return texto

def parse_inicial(texto):
    import re

    resultados = []

    texto = texto.replace('\n', ' ').replace('\r', ' ')
    
    # Captura nomes seguidos de "RG n" e "CPF n"
    padrao = re.compile(
        r'([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]{5,})[,;\s]+RG\s*n[ºo]?\s*(\d{1,3}[.\dXx-]*)[,;\s]+.*?CPF\s*n[ºo]?\s*(\d{3}\.\d{3}\.\d{3}-\d{2})',
        re.IGNORECASE
    )

    matches = padrao.findall(texto)

    for nome, rg, cpf in matches:
        nome = nome.strip().upper()
        rg = rg.strip()
        cpf = cpf.strip()
        print(f"[DEBUG] Nome capturado: {nome} | RG: {rg} | CPF: {cpf}")
        resultados.append({
            "nome": nome,
            "rg": rg,
            "cpf": cpf
        })

    print(f"[DEBUG] Total de autores encontrados: {len(resultados)}")
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

def parse_spprev(texto):
    import re
    
    resultado = {
        "nome": "NOME NÃO ENCONTRADO",
        "cpf": "CPF NÃO ENCONTRADO",
        "rg": "RG NÃO ENCONTRADO",
        "matricula": "MATRÍCULA NÃO ENCONTRADA",
        "beneficio": "BENEFÍCIO NÃO ENCONTRADO"
    }
    
    texto = texto.replace('\n', ' ')
    
    # Captura CPF
    match_cpf = re.search(r'\b(\d{3}\.\d{3}\.\d{3}-\d{2})\b', texto)
    if match_cpf:
        resultado["cpf"] = match_cpf.group(1)
    
    # Captura nome
    match_nome = re.search(r'Nome:\s*([A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{3,}?)(?:\s*CPF|\s*RG|\s*Mat)', texto, re.IGNORECASE)
    if match_nome:
        resultado["nome"] = match_nome.group(1).strip()
    
    # Captura RG
    match_rg = re.search(r'RG\s*[:\s]?\s*(\d[\d\.\-xX]+)', texto, re.IGNORECASE)
    if match_rg:
        resultado["rg"] = match_rg.group(1).strip()
    
    # Captura matrícula
    match_matricula = re.search(r'Matrícula\s*[:\s]?\s*(\d+)', texto, re.IGNORECASE)
    if match_matricula:
        resultado["matricula"] = match_matricula.group(1).strip()
    
    # Captura tipo de benefício
    match_beneficio = re.search(r'(?:Benefício|Beneficio)\s*[:\s]?\s*([A-Z0-9 ]+)', texto, re.IGNORECASE)
    if match_beneficio:
        resultado["beneficio"] = match_beneficio.group(1).strip()
    
    return [resultado]

def extrair_paginas_arquivo(nome_arquivo):
    import re
    
    # Padrão 1: pag_123_456 ou pag 123 456
    padrao1 = re.search(r'pag[_ ]?(\d{2,5})\D+(\d{2,5})', nome_arquivo)
    if padrao1:
        return f"{padrao1.group(1)}–{padrao1.group(2)}"
    
    # Padrão 2: (pag 620 - 751) ou similar
    padrao2 = re.search(r'\(pag[s]?\s*(\d{2,5})\s*[-–]\s*(\d{2,5})\)', nome_arquivo)
    if padrao2:
        return f"{padrao2.group(1)}–{padrao2.group(2)}"
    
    # Padrão 3: páginas 123-456 ou similar
    padrao3 = re.search(r'p[áa]gina[s]?\s*(\d{2,5})\s*[-–]\s*(\d{2,5})', nome_arquivo, re.IGNORECASE)
    if padrao3:
        return f"{padrao3.group(1)}–{padrao3.group(2)}"
    
    # Se nenhum padrão for encontrado
    return ""

def analisar_com_chatgpt(dados_autor, tipo_acao='', data_distribuicao=''):
    """
    Usa a API do OpenAI para analisar os dados do autor e fornecer insights
    """
    try:
        # Formata a data para o padrão brasileiro (dd/mm/YYYY)
        data_formatada = ''
        if data_distribuicao:
            try:
                # Supondo que a data venha no formato YYYY-mm-dd (HTML date input)
                partes = data_distribuicao.split('-')
                if len(partes) == 3:
                    ano, mes, dia = partes
                    data_formatada = f"{dia}/{mes}/{ano}"
                else:
                    # Se não conseguir separar, usa a string original
                    data_formatada = data_distribuicao
            except Exception as e:
                print(f"Erro ao formatar data: {e}")
                data_formatada = data_distribuicao    

        # Constrói informações adicionais para o prompt com base no tipo de ação
        info_adicional = ""
        if tipo_acao == 'Adicional APEOESP':
            info_adicional = """
            Tipo de Ação: Adicional APEOESP
            
            Regras para a Análise (siga com rigor):
            - Para que o processo esteja apto a prosseguir, é suficiente a presença de apenas um dos seguintes documentos: o Informativo do CAF ou o Informativo do Extratão.
            - A presença de qualquer um dos dois documentos já viabiliza os cálculos e permite o prosseguimento do processo, mesmo que o outro esteja ausente.
            - Caso nenhum dos dois esteja disponível (Informativo CAF e Informativo Extratão), será necessário solicitar nos autos que ao menos um deles seja providenciado.
            - Atenção: Se existir somente o Informativo do SPPREV o cálculo não está apto a seguir.
            """
        elif tipo_acao == 'Procedimento Ordinário':
            info_adicional = f"""
            Tipo de Ação: Procedimento Ordinário
            Data de Distribuição: {data_formatada}
            
            Regras para a Análise (siga com rigor):
            1. Se o Informativo do Extratão estiver disponível (valor "Sim"), o processo está automaticamente apto. Não é necessário verificar mais nada.  
            2. Se o Extratão NÃO estiver disponível (valor "Não"), a aptidão depende da data de distribuição:  
                a) Para processos distribuídos a partir de 01/05/2016, o Informativo do SPPREV, sozinho, é suficiente.  
                b) Para processos distribuídos antes de 30/04/2016, são obrigatórios tanto o Informativo do CAF quanto o do SPPREV.
            """
        
        prompt = f"""
        Analise os seguintes dados de processo judicial:
        
        Nome do autor: {dados_autor.get('Nome', 'Não informado')}
        CPF: {dados_autor.get('Cpf', 'Não informado')}
        RG: {dados_autor.get('Rg', 'Não informado')}
        
        Documentos encontrados:
        - Informativo CAF: {dados_autor.get('caf', 'Não')}
        - Informativo SPPREV: {dados_autor.get('spprev', 'Não')}
        - Informativo Extratão: {dados_autor.get('extratao', 'Não')}

        Para a análise leve em conta as seguintes considerações. Essas são as diretrizes principais para as análises, não as ignore:
        {info_adicional}
        
        Com base nessas informações, responda de forma objetiva (máximo 1 parágrafo):
        Considerando o tipo de ação e os informativos encontrados, responda de forma objetiva se o processo está apto a seguir para cálculo, e nada mais. Se a resposta for negativa, seja objetivo e indique de forma clara quais documentos estão faltando.
        → Se estiver apto, inicie a resposta com o ícone ✅.
        → Se não estiver apto, inicie a resposta com o ícone ❌.
        → IMPORTANTE: Não utilize ** e nenhuma outra forma de destacar o texto.
        """
        
        # Debug simples - apenas mostra o prompt
        print("\n----- PROMPT PARA API -----")
        print(prompt)
        print("---------------------------\n")
        
        # Inicialize o cliente com a chave explícita
        api_key = os.getenv('OPENAI_API_KEY')
        client = openai.OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": f"Você é um assistente especializado em processos judiciais, com foco em análise de documentação para ações do tipo {tipo_acao}."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=250
        )
        
        analise = response.choices[0].message.content.strip()
        return analise
    except Exception as e:
        print(f"Erro ao usar a API OpenAI: {e}")
        return "Não foi possível realizar a análise automatizada. Verifique sua chave de API."
    
def processar_arquivos(tipo_acao='', data_distribuicao=''):
    pasta = 'uploads'
    arquivos = os.listdir(pasta)

    dados_inicial = []
    extratao_encontrados = []
    caf_encontrados = []
    spprev_encontrados = []

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
            print(f"\n[DEBUG] Texto da Inicial ({nome_original}):\n{texto}\n")
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
        elif tipo == 'Informativo SPPREV':
            registros = parse_spprev(texto)
            for dado in registros:
                dado["arquivo"] = nome_original
                dado["paginas"] = extrair_paginas_arquivo(nome_original)
                spprev_encontrados.append(dado)

    # Cria DataFrame base com dados da Inicial
    if not dados_inicial:
        return []
        
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
    
    # Cruzamento com SPPREV (por CPF, RG ou nome)
    for idx, row in df.iterrows():
        cpf_df = str(row.get('cpf', '')).replace('.', '').replace('-', '').strip()
        rg_df = str(row.get('rg', '')).replace('.', '').replace('-', '').upper().lstrip('0')
        nome_df = normalizar_nome(row.get('nome', ''))
        
        encontrou = False
        
        # Primeiro tenta por CPF
        for registro in spprev_encontrados:
            cpf_spprev = str(registro.get('cpf', '')).replace('.', '').replace('-', '').strip()
            if cpf_df and cpf_spprev and cpf_df == cpf_spprev:
                df.at[idx, 'Informativo SPPREV'] = 'Sim'
                df.at[idx, 'Página SPPREV'] = registro.get('paginas', '')
                encontrou = True
                break
        
        # Se não encontrou por CPF, tenta por RG
        if not encontrou:
            for registro in spprev_encontrados:
                rg_spprev = str(registro.get('rg', '')).replace('.', '').replace('-', '').upper().lstrip('0')
                if rg_df and rg_spprev and (
                    rg_df[:6] == rg_spprev[:6] or
                    rg_df[:7] == rg_spprev[:7] or
                    rg_df[:8] == rg_spprev[:8]
                ):
                    df.at[idx, 'Informativo SPPREV'] = 'Sim'
                    df.at[idx, 'Página SPPREV'] = registro.get('paginas', '')
                    encontrou = True
                    break
        
        # Se ainda não encontrou, tenta por nome
        if not encontrou:
            for registro in spprev_encontrados:
                nome_spprev = normalizar_nome(registro.get('nome', ''))
                if nome_df and nome_spprev and (nome_df in nome_spprev or nome_spprev in nome_df):
                    df.at[idx, 'Informativo SPPREV'] = 'Sim'
                    df.at[idx, 'Página SPPREV'] = registro.get('paginas', '')
                    break

    # Define nomes de colunas padronizados, preservando os nomes específicos para os informativos
    colunas_padronizadas = []
    for col in df.columns:
        if col.startswith('Informativo') or col.startswith('Página'):
            # Mantém o formato original para colunas de informativos e páginas
            colunas_padronizadas.append(col)
        else:
            # Capitaliza as demais colunas
            colunas_padronizadas.append(col.capitalize())
            
    df.columns = colunas_padronizadas
    
    # Adiciona as informações do tipo de ação
    df['Tipo Acao'] = tipo_acao
    if data_distribuicao:
        df['Data Distribuicao'] = data_distribuicao
    
    # Para debug - imprime os nomes das colunas após a padronização
    print("DEBUG - Nomes das colunas após padronização:")
    for col in df.columns:
        print(f"  {col}")
    
    # Adiciona coluna de análise usando a API do OpenAI
    if 'OPENAI_API_KEY' in os.environ and os.environ['OPENAI_API_KEY']:
        # Passamos os valores específicos para a função analisar_com_chatgpt
        def analisar_com_dados_especificos(row):
            # Criamos um dicionário com os dados necessários para a análise
            dados_para_analise = {
                'Nome': row['Nome'],
                'Cpf': row['Cpf'],
                'Rg': row['Rg'],
                'caf': row['Informativo CAF'],
                'spprev': row['Informativo SPPREV'], 
                'extratao': row['Informativo Extratão']
            }
            return analisar_com_chatgpt(dados_para_analise, tipo_acao, data_distribuicao)
        
        df['Análise'] = df.apply(analisar_com_dados_especificos, axis=1)
    else:
        df['Análise'] = 'Configure a chave de API do OpenAI no arquivo .env para habilitar a análise automática.'
    
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
    tipo_acao = request.args.get('tipo_acao', '')
    data_distribuicao = request.args.get('data_distribuicao', '')
    
    resultados = processar_arquivos(tipo_acao, data_distribuicao)
    if not resultados:
        return render_template('erro.html', mensagem="Nenhum autor encontrado na Petição Inicial. Verifique se o documento foi carregado corretamente.")
    
    # Salva os resultados em um arquivo Excel
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"analise_processos_{timestamp}.xlsx"
    caminho_excel = os.path.join(app.config['RESULTS_FOLDER'], nome_arquivo)
    
    # Converte para DataFrame e salva
    df = pd.DataFrame(resultados)
    df.to_excel(caminho_excel, index=False)
    
    return render_template('resultado.html', resultados=resultados, arquivo_excel=nome_arquivo, tipo_acao=tipo_acao, data_distribuicao=data_distribuicao)

@app.route('/baixar_excel/<filename>')
def baixar_excel(filename):
    return send_file(os.path.join(app.config['RESULTS_FOLDER'], filename), as_attachment=True)

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