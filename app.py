from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
import os
import time
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
TIPOS = ['Inicial', 'Informativos', 'Litispendência']

# --- Funções auxiliares ---

def classificar_informativo(texto):
    """
    Analisa o conteúdo do texto do PDF e identifica o tipo de informativo
    Retorna: 'Informativo Extratão', 'Informativo SPPREV', 'Informativo CAF' ou None se não identificado
    """
    # Critérios para Informativo Extratão
    criterios_extratao = ["OR / UO / UD", "Início de Provimento", "folhadepagamento.sp.gov.br"]
    for criterio in criterios_extratao:
        if criterio in texto:
            return "Informativo Extratão"

    # Critérios para Informativo SPPREV - removido SPPREV para evitar falsos positivos
    criterios_spprev = ["Benefício", "BENEFÍCIO", "Composição", "spprev.sp.gov.br"]
    for criterio in criterios_spprev:
        if criterio in texto:
            return "Informativo SPPREV"

    # Critérios para Informativo CAF
    criterios_caf = ["Processo SF", "Período Abrangente"]
    for criterio in criterios_caf:
        if criterio in texto:
            return "Informativo CAF"

    # Se nenhum critério foi atendido, retorna None
    return None

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
    nao_classificados = []

    for arquivo in arquivos:
        caminho = os.path.join(pasta, arquivo)

        if '__' not in arquivo:
            continue

        tipo, nome_original = arquivo.split('__', 1)
        tipo = tipo.replace("_", " ")
        caminho_para_processar = caminho

        # Se não for Inicial ou Litispendência, recorta as 2 primeiras páginas para agilizar
        if tipo not in ['Inicial', 'Litispendência', 'Nao Classificado']:
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
                dado["arquivo_completo"] = arquivo  # Nome completo com prefixo
                dado["paginas"] = extrair_paginas_arquivo(nome_original)
                dado["tipo"] = "Extratão"
                dado["encontrado_match"] = False  # Será atualizado durante o cruzamento
                extratao_encontrados.append(dado)
        elif tipo == 'Informativo CAF':
            registros = parse_caf(texto)
            for dado in registros:
                dado["arquivo"] = nome_original
                dado["arquivo_completo"] = arquivo  # Nome completo com prefixo
                dado["paginas"] = extrair_paginas_arquivo(nome_original)
                dado["tipo"] = "CAF"
                dado["encontrado_match"] = False  # Será atualizado durante o cruzamento
                caf_encontrados.append(dado)
        elif tipo == 'Informativo SPPREV':
            registros = parse_spprev(texto)
            for dado in registros:
                dado["arquivo"] = nome_original
                dado["arquivo_completo"] = arquivo  # Nome completo com prefixo
                dado["paginas"] = extrair_paginas_arquivo(nome_original)
                dado["tipo"] = "SPPREV"
                dado["encontrado_match"] = False  # Será atualizado durante o cruzamento
                spprev_encontrados.append(dado)
        elif tipo == 'Nao Classificado':
            # Armazena dados de arquivos não classificados para exibir na interface
            nao_classificados.append({
                "arquivo": nome_original,
                "arquivo_completo": arquivo,  # Nome completo com prefixo
                "caminho": caminho,
                "paginas": extrair_paginas_arquivo(nome_original)
            })

    # Cria DataFrame base com dados da Inicial
    if not dados_inicial:
        return []

    df = pd.DataFrame(dados_inicial)
    df.insert(0, 'Id', range(1, len(df) + 1))  # Alterado para 'Id' para manter consistência com o resto do código

    for inf in ['Extratão', 'CAF', 'SPPREV']:
        df[f'Informativo {inf}'] = 'Não'
        df[f'Página {inf}'] = ''

    # Cruzamento com Extratão (por CPF)
    for idx, row in df.iterrows():
        cpf_df = str(row['cpf']).replace('.', '').replace('-', '').strip()
        for i, registro in enumerate(extratao_encontrados):
            cpf_extra = str(registro['cpf']).replace('.', '').replace('-', '').strip()
            if cpf_df == cpf_extra:
                df.at[idx, 'Informativo Extratão'] = 'Sim'
                df.at[idx, 'Página Extratão'] = registro.get('paginas', '')
                extratao_encontrados[i]['encontrado_match'] = True
                # Removido o break para processar todos os informativos com o mesmo CPF

    # Cruzamento com CAF por RG (com tolerância) ou, em último caso, por nome
    for idx, row in df.iterrows():
        rg_df = str(row.get('rg', '')).replace('.', '').replace('-', '').upper().lstrip('0')
        nome_df = normalizar_nome(row.get('nome', ''))

        encontrou = False

        for i, registro in enumerate(caf_encontrados):
            rg_caf = str(registro['rg']).replace('.', '').replace('-', '').upper().lstrip('0')
            print(f"Comparando RGs → Inicial: {rg_df} | CAF: {rg_caf}")

            if (
                rg_df[:6] == rg_caf[:6] or
                rg_df[:7] == rg_caf[:7] or
                rg_df[:8] == rg_caf[:8]
            ):
                df.at[idx, 'Informativo CAF'] = 'Sim'
                df.at[idx, 'Página CAF'] = registro.get('paginas', '')
                caf_encontrados[i]['encontrado_match'] = True
                encontrou = True
                # Removido o break para processar todos os informativos com o mesmo RG

        if not encontrou:
            for i, registro in enumerate(caf_encontrados):
                nome_caf = normalizar_nome(registro.get('nome', ''))
                print(f"Cruzando por nome: {nome_df} vs {nome_caf}")

                if nome_df in nome_caf or nome_caf in nome_df:
                    df.at[idx, 'Informativo CAF'] = 'Sim'
                    df.at[idx, 'Página CAF'] = registro.get('paginas', '')
                    caf_encontrados[i]['encontrado_match'] = True
                    # Removido o break para processar todos os informativos com nomes correspondentes

    # Cruzamento com SPPREV (por CPF, RG ou nome)
    for idx, row in df.iterrows():
        cpf_df = str(row.get('cpf', '')).replace('.', '').replace('-', '').strip()
        rg_df = str(row.get('rg', '')).replace('.', '').replace('-', '').upper().lstrip('0')
        nome_df = normalizar_nome(row.get('nome', ''))

        encontrou = False

        # Primeiro tenta por CPF
        for i, registro in enumerate(spprev_encontrados):
            cpf_spprev = str(registro.get('cpf', '')).replace('.', '').replace('-', '').strip()
            if cpf_df and cpf_spprev and cpf_df == cpf_spprev:
                df.at[idx, 'Informativo SPPREV'] = 'Sim'
                df.at[idx, 'Página SPPREV'] = registro.get('paginas', '')
                spprev_encontrados[i]['encontrado_match'] = True
                encontrou = True
                # Removido o break para processar todos os informativos com o mesmo CPF

        # Se não encontrou por CPF, tenta por RG
        if not encontrou:
            for i, registro in enumerate(spprev_encontrados):
                rg_spprev = str(registro.get('rg', '')).replace('.', '').replace('-', '').upper().lstrip('0')
                if rg_df and rg_spprev and (
                    rg_df[:6] == rg_spprev[:6] or
                    rg_df[:7] == rg_spprev[:7] or
                    rg_df[:8] == rg_spprev[:8]
                ):
                    df.at[idx, 'Informativo SPPREV'] = 'Sim'
                    df.at[idx, 'Página SPPREV'] = registro.get('paginas', '')
                    spprev_encontrados[i]['encontrado_match'] = True
                    encontrou = True
                    # Removido o break para processar todos os informativos com o mesmo RG

        # Se ainda não encontrou, tenta por nome
        if not encontrou:
            for i, registro in enumerate(spprev_encontrados):
                nome_spprev = normalizar_nome(registro.get('nome', ''))
                if nome_df and nome_spprev and (nome_df in nome_spprev or nome_spprev in nome_df):
                    df.at[idx, 'Informativo SPPREV'] = 'Sim'
                    df.at[idx, 'Página SPPREV'] = registro.get('paginas', '')
                    spprev_encontrados[i]['encontrado_match'] = True
                    # Removido o break para processar todos os informativos com nomes correspondentes

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

    # Combina todos os informativos em uma única lista para exibição na tabela de detalhes
    todos_informativos = []
    todos_informativos.extend(extratao_encontrados)
    todos_informativos.extend(caf_encontrados)
    todos_informativos.extend(spprev_encontrados)

    # Normaliza os nomes das chaves para melhor exibição
    for info in todos_informativos:
        # Garante que todos tenham campos nome e cpf
        if 'nome' not in info and 'nome' not in info:
            info['nome'] = info.get('nome', "NÃO ENCONTRADO")

        # Normaliza as keys
        if 'rg' in info:
            info['RG'] = info['rg']
        if 'cpf' in info:
            info['CPF'] = info['cpf']
        if 'nome' in info:
            info['Nome'] = info['nome']
        if 'beneficio' in info:
            info['Benefício'] = info['beneficio']
        if 'matricula' in info:
            info['Matrícula'] = info['matricula']

    # Retorna tanto os resultados da análise quanto os documentos não classificados
    resultados = {
        'autores': df.to_dict(orient='records'),
        'nao_classificados': nao_classificados,
        'informativos': todos_informativos
    }

    return resultados

@app.route('/')
def index():
    arquivos = os.listdir(app.config['UPLOAD_FOLDER']) if os.path.exists(app.config['UPLOAD_FOLDER']) else []
    tipos_encontrados = [arq.split('__', 1)[0].replace("_", " ") for arq in arquivos if '__' in arq]
    # Atualizado para considerar que os informativos podem ser classificados automaticamente
    pode_analisar = 'Inicial' in tipos_encontrados and any(t in tipos_encontrados for t in
                                                           ['Informativo Extratão',
                                                            'Informativo CAF',
                                                            'Informativo SPPREV',
                                                            'Nao Classificado'])
    return render_template('index.html', tipos=TIPOS, pode_analisar=pode_analisar, arquivos=arquivos)

@app.route('/upload', methods=['POST'])
def upload():
    # Retorna erro se nenhum arquivo foi enviado
    if 'pdfs' not in request.files:
        return render_template('erro.html', mensagem="Nenhum arquivo foi enviado. Por favor, selecione arquivos para enviar.")

    arquivos = request.files.getlist('pdfs')
    tipo = request.form.get('tipo')

    # Verifica se arquivos foram realmente enviados
    if not arquivos or arquivos[0].filename == '':
        return render_template('erro.html', mensagem="Nenhum arquivo foi selecionado. Por favor, selecione arquivos para enviar.")

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    arquivos_processados = 0

    for arquivo in arquivos:
        if arquivo and tipo and arquivo.filename:
            nome_seguro = secure_filename(arquivo.filename)

            # Skip empty filenames
            if not nome_seguro:
                continue

            # Se for tipo "Informativos", tenta classificar automaticamente
            if tipo == "Informativos":
                try:
                    # Primeiro salva o arquivo com um prefixo temporário
                    nome_temp = f"temp__{nome_seguro}"
                    caminho_temp = os.path.join(app.config['UPLOAD_FOLDER'], nome_temp)
                    arquivo.save(caminho_temp)

                    # Corta o PDF para análise mais rápida
                    caminho_recorte = os.path.join(app.config['UPLOAD_FOLDER'], f"_recorte_{nome_seguro}")
                    cortar_pdf(caminho_temp, caminho_recorte)

                    # Extrai o texto e classifica
                    texto = extrair_texto(caminho_recorte)
                    tipo_classificado = classificar_informativo(texto)

                    # Se conseguiu classificar, salva com o tipo correto
                    if tipo_classificado:
                        tipo_limpo = tipo_classificado.replace(" ", "_")
                        nome_final = f"{tipo_limpo}__{nome_seguro}"
                        caminho_final = os.path.join(app.config['UPLOAD_FOLDER'], nome_final)
                        os.rename(caminho_temp, caminho_final)
                        arquivos_processados += 1

                        # Remove o recorte temporário após classificação
                        if os.path.exists(caminho_recorte):
                            os.remove(caminho_recorte)
                    else:
                        # Se não conseguiu classificar, mantém como "Não_Classificado"
                        nome_final = f"Nao_Classificado__{nome_seguro}"
                        caminho_final = os.path.join(app.config['UPLOAD_FOLDER'], nome_final)
                        os.rename(caminho_temp, caminho_final)
                        arquivos_processados += 1

                        # Remove o recorte temporário após tentativa de classificação
                        if os.path.exists(caminho_recorte):
                            os.remove(caminho_recorte)
                except Exception as e:
                    print(f"Erro ao processar arquivo {nome_seguro}: {e}")
                    # Se ocorrer erro, tenta salvar o arquivo diretamente
                    try:
                        tipo_limpo = "Nao_Classificado"
                        nome_final = f"{tipo_limpo}__{nome_seguro}"
                        caminho_final = os.path.join(app.config['UPLOAD_FOLDER'], nome_final)
                        arquivo.save(caminho_final)
                        arquivos_processados += 1
                    except Exception as e2:
                        print(f"Erro ao salvar arquivo {nome_seguro} após falha: {e2}")
            else:
                # Para os outros tipos, usa o comportamento original
                try:
                    tipo_limpo = tipo.replace(" ", "_")
                    nome_final = f"{tipo_limpo}__{nome_seguro}"
                    caminho = os.path.join(app.config['UPLOAD_FOLDER'], nome_final)
                    arquivo.save(caminho)
                    arquivos_processados += 1
                except Exception as e:
                    print(f"Erro ao salvar arquivo {nome_seguro}: {e}")

    if arquivos_processados == 0:
        return render_template('erro.html', mensagem="Nenhum arquivo pôde ser processado. Verifique se os arquivos são PDFs válidos.")

    return redirect(url_for('index'))

@app.route('/analisar')
def analisar():
    tipo_acao = request.args.get('tipo_acao', '')
    data_distribuicao = request.args.get('data_distribuicao', '')
    
    # Verifica se existe um arquivo Excel recente para carregar
    pasta_resultados = app.config['RESULTS_FOLDER']
    arquivos_excel = [f for f in os.listdir(pasta_resultados) if f.endswith('.xlsx')]
    
    use_previous_results = False
    arquivo_excel_anterior = None
    
    if arquivos_excel:
        # Obtém o arquivo mais recente
        arquivo_excel_anterior = max(arquivos_excel, key=lambda f: os.path.getmtime(os.path.join(pasta_resultados, f)))
        
        # Verifica se o arquivo é recente (menos de 1 hora)
        caminho_excel = os.path.join(pasta_resultados, arquivo_excel_anterior)
        tempo_modificacao = os.path.getmtime(caminho_excel)
        tempo_atual = time.time()
        
        # Se o arquivo foi modificado nos últimos 10 minutos, usa ele
        if tempo_atual - tempo_modificacao < 600:  # 10 minutos em segundos
            use_previous_results = True
            print(f"Usando arquivo Excel existente: {arquivo_excel_anterior}")
    
    if use_previous_results and arquivo_excel_anterior:
        # Carrega os resultados do Excel
        try:
            df = pd.read_excel(os.path.join(pasta_resultados, arquivo_excel_anterior))
            
            # Processa os informativos apenas para obter a lista completa
            resultados_temp = processar_arquivos(tipo_acao, data_distribuicao)
            
            # Marca os informativos como vinculados com base nos dados do Excel
            if 'informativos' in resultados_temp:
                for info in resultados_temp['informativos']:
                    info_tipo = info.get('tipo', '')
                    
                    if info_tipo:
                        coluna_verificar = f'Informativo {info_tipo}'
                        # Procura se algum autor tem esse informativo marcado como "Sim"
                        for _, row in df.iterrows():
                            if coluna_verificar in row and row[coluna_verificar] == 'Sim':
                                # Se o autor tem o informativo, marca como vinculado
                                info['encontrado_match'] = True
                                break
            
            # Cria o objeto de resultados usando os dados do Excel e os informativos processados
            resultados = {
                'autores': df.to_dict(orient='records'),
                'nao_classificados': resultados_temp.get('nao_classificados', []),
                'informativos': resultados_temp.get('informativos', [])
            }
            
            # Usa o mesmo arquivo Excel
            nome_arquivo = arquivo_excel_anterior
            
        except Exception as e:
            print(f"Erro ao carregar arquivo Excel existente: {e}")
            # Se falhar, processa normalmente
            resultados = processar_arquivos(tipo_acao, data_distribuicao)
            
            # Se não tivermos dados de autores, retornamos uma mensagem de erro
            if not resultados or not resultados.get('autores'):
                return render_template('erro.html', mensagem="Nenhum autor encontrado na Petição Inicial. Verifique se o documento foi carregado corretamente.")
            
            # Salva os resultados em um arquivo Excel (apenas os dados dos autores)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            nome_arquivo = f"analise_processos_{timestamp}.xlsx"
            caminho_excel = os.path.join(app.config['RESULTS_FOLDER'], nome_arquivo)
            
            # Converte para DataFrame e salva
            df = pd.DataFrame(resultados['autores'])
            df.to_excel(caminho_excel, index=False)
    else:
        # Processa os arquivos normalmente
        resultados = processar_arquivos(tipo_acao, data_distribuicao)
        
        # Se não tivermos dados de autores, retornamos uma mensagem de erro
        if not resultados or not resultados.get('autores'):
            return render_template('erro.html', mensagem="Nenhum autor encontrado na Petição Inicial. Verifique se o documento foi carregado corretamente.")
        
        # Salva os resultados em um arquivo Excel (apenas os dados dos autores)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = f"analise_processos_{timestamp}.xlsx"
        caminho_excel = os.path.join(app.config['RESULTS_FOLDER'], nome_arquivo)
        
        # Converte para DataFrame e salva
        df = pd.DataFrame(resultados['autores'])
        df.to_excel(caminho_excel, index=False)

    # Passa os dados dos autores, documentos não classificados e informativos para o template
    return render_template('resultado.html',
                          resultados=resultados['autores'],
                          nao_classificados=resultados.get('nao_classificados', []),
                          informativos=resultados.get('informativos', []),
                          arquivo_excel=nome_arquivo,
                          tipo_acao=tipo_acao,
                          data_distribuicao=data_distribuicao)

@app.route('/baixar_excel/<filename>')
def baixar_excel(filename):
    return send_file(os.path.join(app.config['RESULTS_FOLDER'], filename), as_attachment=True)

@app.route('/visualizar_pdf/<filename>')
def visualizar_pdf(filename):
    """
    Endpoint para visualizar um arquivo PDF diretamente no navegador.
    """
    # Decodifica o nome do arquivo se necessário
    decoded_filename = filename
    
    # Pasta de uploads
    pasta = app.config['UPLOAD_FOLDER']
    
    # Lista todos os arquivos disponíveis na pasta para debug
    arquivos_disponiveis = os.listdir(pasta)
    print(f"Arquivos disponíveis na pasta: {arquivos_disponiveis}")
    print(f"Buscando arquivo: {decoded_filename}")
    
    # 1. Tenta o caminho direto
    caminho = os.path.join(pasta, decoded_filename)
    if os.path.exists(caminho):
        print(f"✅ Arquivo encontrado diretamente: {caminho}")
        return send_file(caminho, mimetype='application/pdf')
    
    # 2. Tenta verificar se é um arquivo com prefixo
    for prefixo in ["Informativo_CAF__", "Informativo_SPPREV__", "Informativo_Extratão__", "Informativo_Extratao__"]:
        arquivo_com_prefixo = f"{prefixo}{decoded_filename}"
        caminho_prefixado = os.path.join(pasta, arquivo_com_prefixo)
        if os.path.exists(caminho_prefixado):
            print(f"✅ Arquivo encontrado com prefixo: {caminho_prefixado}")
            return send_file(caminho_prefixado, mimetype='application/pdf')
    
    # 3. Tenta encontrar qualquer arquivo que contenha o nome fornecido
    for arquivo in arquivos_disponiveis:
        if decoded_filename in arquivo:
            caminho_parcial = os.path.join(pasta, arquivo)
            print(f"✅ Arquivo encontrado parcialmente: {caminho_parcial}")
            return send_file(caminho_parcial, mimetype='application/pdf')
    
    # 4. Tenta versão recortada
    recorte_nome = f"_recorte_{decoded_filename}"
    recorte_caminho = os.path.join(pasta, recorte_nome)
    if os.path.exists(recorte_caminho):
        print(f"✅ Arquivo recortado encontrado: {recorte_caminho}")
        return send_file(recorte_caminho, mimetype='application/pdf')
    
    # Se chegou aqui, não encontrou o arquivo
    print(f"❌ Arquivo não encontrado: {decoded_filename}")
    return f"Arquivo PDF não encontrado: {decoded_filename}", 404

@app.route('/limpar_uploads', methods=['POST'])
def limpar_uploads():
    # Limpa a pasta de uploads
    pasta_uploads = app.config['UPLOAD_FOLDER']
    if os.path.exists(pasta_uploads):
        for arquivo in os.listdir(pasta_uploads):
            caminho = os.path.join(pasta_uploads, arquivo)
            if os.path.isfile(caminho):
                os.remove(caminho)
    
    # Limpa a pasta de resultados
    pasta_resultados = app.config['RESULTS_FOLDER']
    if os.path.exists(pasta_resultados):
        for arquivo in os.listdir(pasta_resultados):
            caminho = os.path.join(pasta_resultados, arquivo)
            if os.path.isfile(caminho):
                os.remove(caminho)
    
    return redirect(url_for('index'))

@app.route('/classificar_documento', methods=['POST'])
def classificar_documento():
    """
    Reclassifica um documento não classificado para um tipo específico.
    """
    dados = request.json
    arquivo = dados.get('arquivo')
    novo_tipo = dados.get('tipo')

    if not arquivo or not novo_tipo:
        return jsonify({'success': False, 'error': 'Arquivo ou tipo não fornecido'})

    try:
        # Encontra o arquivo não classificado
        pasta = app.config['UPLOAD_FOLDER']
        caminho_atual = os.path.join(pasta, f"Nao_Classificado__{arquivo}")

        # Verifica se o arquivo existe
        if not os.path.exists(caminho_atual):
            return jsonify({'success': False, 'error': 'Arquivo não encontrado'})

        # Caso especial: Se for para ignorar o arquivo, exclui-o
        if novo_tipo == "Ignorar Arquivo":
            try:
                # Remove o arquivo do sistema
                os.remove(caminho_atual)

                # Limpa quaisquer arquivos de recorte relacionados também
                recorte_path = os.path.join(pasta, f"_recorte_{arquivo}")
                if os.path.exists(recorte_path):
                    os.remove(recorte_path)

                return jsonify({'success': True, 'ignored': True})
            except Exception as e:
                print(f"Erro ao remover arquivo: {e}")
                # Retorna sucesso mesmo se ocorrer erro, pois queremos que o arquivo seja ignorado de qualquer forma
                return jsonify({'success': True, 'ignored': True})

        # Caso normal: reclassifica o arquivo
        tipo_limpo = novo_tipo.replace(" ", "_")
        novo_nome = f"{tipo_limpo}__{arquivo}"
        caminho_novo = os.path.join(pasta, novo_nome)

        # Renomeia o arquivo
        os.rename(caminho_atual, caminho_novo)

        return jsonify({'success': True})
    except Exception as e:
        print(f"Erro ao classificar documento: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/vincular_informativo', methods=['POST'])
def vincular_informativo():
    """
    Vincula um informativo (CAF, SPPREV ou Extratão) a um autor específico.
    Atualiza as colunas de status indicando que o autor possui o informativo.
    """
    try:
        dados = request.json
        arquivo = dados.get('arquivo')
        autor_id = dados.get('autor_id')
        tipo_informativo = dados.get('tipo_informativo')

        # Validação dos dados recebidos
        if not arquivo or not autor_id or not tipo_informativo:
            return jsonify({
                'success': False, 
                'error': 'Dados incompletos. Arquivo, ID do autor e tipo do informativo são obrigatórios.'
            })

        # Verifica se o arquivo existe
        pasta = app.config['UPLOAD_FOLDER']
        if not os.path.exists(os.path.join(pasta, arquivo)):
            return jsonify({'success': False, 'error': f'Arquivo não encontrado: {arquivo}'})

        # Extrai informações do arquivo
        paginas = extrair_paginas_arquivo(arquivo.split('__', 1)[1] if '__' in arquivo else arquivo)
        
        # Obtém o tipo do arquivo (CAF, SPPREV, Extratão)
        tipo_arquivo = tipo_informativo
        
        # Tratamento para garantir consistência do nome do tipo
        if tipo_arquivo.lower() == 'extratao' or tipo_arquivo.lower() == 'extratão':
            tipo_arquivo = 'Extratão'
        elif tipo_arquivo.lower() == 'caf':
            tipo_arquivo = 'CAF'
        elif tipo_arquivo.lower() == 'spprev':
            tipo_arquivo = 'SPPREV'

        # Obtém o caminho do arquivo temporário de resultados (usa o mais recente)
        pasta_resultados = app.config['RESULTS_FOLDER']
        arquivos_resultados = os.listdir(pasta_resultados)
        
        # Filtra apenas os arquivos Excel e ordena pelo mais recente
        arquivos_excel = [f for f in arquivos_resultados if f.endswith('.xlsx')]
        if not arquivos_excel:
            return jsonify({'success': False, 'error': 'Nenhum arquivo de resultados encontrado'})
        
        arquivo_mais_recente = max(arquivos_excel, key=lambda f: os.path.getmtime(os.path.join(pasta_resultados, f)))
        caminho_excel = os.path.join(pasta_resultados, arquivo_mais_recente)

        # Lê o arquivo Excel
        df = pd.read_excel(caminho_excel)

        # Para debug
        print(f"Colunas disponíveis no DataFrame: {df.columns.tolist()}")
        
        # Encontra o autor pelo ID
        try:
            id_autor = int(autor_id)
        except ValueError:
            # Se não for possível converter para int, usa a string original
            id_autor = autor_id
            print(f"Usando ID no formato string: {id_autor}")
        
        # Default para a coluna de ID mais comum baseado na análise
        id_column = 'Id'  # Este é o nome real da coluna conforme verificamos
        
        # Verifica se existe coluna de ID no DataFrame
        if 'Id' not in df.columns:
            # Tenta outras variações
            if 'ID' in df.columns:
                id_column = 'ID'
            else:
                # Busca por colunas com nome que contém "id" independente da capitalização
                id_columns = [col for col in df.columns if col.lower() == 'id']
                if id_columns:
                    id_column = id_columns[0]
        
        if not id_column:
            # Busca por qualquer coluna que tenha 'id' no nome
            id_columns = [col for col in df.columns if 'id' in col.lower()]
            if id_columns:
                id_column = id_columns[0]
                print(f"Usando coluna alternativa com ID: {id_column}")
            else:
                print("Nenhuma coluna de ID encontrada")
                return jsonify({'success': False, 'error': 'Coluna de ID não encontrada no DataFrame'})
        
        print(f"Usando coluna de ID: {id_column}, buscando valor: {id_autor}, tipo: {type(id_autor)}")
        print(f"Valores na coluna {id_column}: {df[id_column].tolist()[:10]}")
        
        # Busca o autor pelo ID - tenta com diferentes tipos
        autor_idx = df.index[(df[id_column] == id_autor) | 
                             (df[id_column] == str(id_autor)) | 
                             (df[id_column].astype(str) == str(id_autor))].tolist()
        
        if not autor_idx:
            return jsonify({'success': False, 'error': f'Autor com ID {id_autor} não encontrado'})
        
        autor_idx = autor_idx[0]  # Pega o primeiro índice (deve ser único)

        # Atualiza o status do informativo para esse autor
        coluna_informativo = f'Informativo {tipo_arquivo}'
        coluna_pagina = f'Página {tipo_arquivo}'
        
        # Garantir que as colunas existem (verificar diferentes possíveis capitalizações)
        colunas_info = [col for col in df.columns if col.lower() == coluna_informativo.lower()]
        colunas_pagina = [col for col in df.columns if col.lower() == coluna_pagina.lower()]
        
        if colunas_info:
            coluna_informativo = colunas_info[0]  # Usa o nome exato da coluna
            df.at[autor_idx, coluna_informativo] = 'Sim'
            
            if colunas_pagina and paginas:
                coluna_pagina = colunas_pagina[0]  # Usa o nome exato da coluna
                df.at[autor_idx, coluna_pagina] = paginas
        else:
            return jsonify({'success': False, 'error': f'Coluna para o tipo {tipo_arquivo} não encontrada'})

        # Atualiza a coluna de análise se existir
        if 'Análise' in df.columns or 'análise' in [col.lower() for col in df.columns]:
            coluna_analise = 'Análise' if 'Análise' in df.columns else [col for col in df.columns if col.lower() == 'análise'][0]
            
            # Obtém os dados do autor
            autor_data = df.iloc[autor_idx].to_dict()
            
            # Se o usuário definiu um tipo de ação e data de distribuição, passa para a análise
            tipo_acao = autor_data.get('Tipo Acao', '')
            data_distribuicao = autor_data.get('Data Distribuicao', '')
            
            # Cria um dicionário apenas com os dados necessários para a análise
            # Encontra as colunas corretas para cada tipo de campo
            nome_cols = [k for k in autor_data.keys() if k.lower() == 'nome']
            cpf_cols = [k for k in autor_data.keys() if k.lower() == 'cpf']
            rg_cols = [k for k in autor_data.keys() if k.lower() == 'rg']
            
            # Procura colunas de informativos com diferentes capitalizações
            caf_cols = [k for k in autor_data.keys() if 'informativo caf' in k.lower()]
            spprev_cols = [k for k in autor_data.keys() if 'informativo spprev' in k.lower()]
            extratao_cols = [k for k in autor_data.keys() if 'informativo extrat' in k.lower()]
            
            # Monta o dicionário com os valores encontrados
            dados_para_analise = {
                'Nome': autor_data.get(nome_cols[0] if nome_cols else 'Nome', ''),
                'Cpf': autor_data.get(cpf_cols[0] if cpf_cols else 'Cpf', ''),
                'Rg': autor_data.get(rg_cols[0] if rg_cols else 'Rg', ''),
                'caf': autor_data.get(caf_cols[0] if caf_cols else 'Informativo CAF', 'Não'),
                'spprev': autor_data.get(spprev_cols[0] if spprev_cols else 'Informativo SPPREV', 'Não'),
                'extratao': autor_data.get(extratao_cols[0] if extratao_cols else 'Informativo Extratão', 'Não')
            }
            
            # Atualiza a análise com base nos novos dados
            nova_analise = analisar_com_chatgpt(dados_para_analise, tipo_acao, data_distribuicao)
            df.at[autor_idx, coluna_analise] = nova_analise

        # Salva as alterações no arquivo Excel
        df.to_excel(caminho_excel, index=False)

        # Para contornar o problema de recarregar a página e perder as alterações,
        # vamos usar uma abordagem diferente: em vez de recarregar a página,
        # retornamos os dados atualizados para o JavaScript atualizar diretamente a UI
        
        # Como esta é uma chamada POST via AJAX, não podemos pegar diretamente da query string
        # Vamos pegar os parâmetros do referer ou usar os do último processamento salvo
        referer = request.headers.get('Referer', '')
        
        # O problema principal é que precisamos usar a mesma URL que o usuário estava vendo
        # para que a página seja recarregada com os mesmos parâmetros
        
        # Extraímos tipo_acao e data_distribuicao da URL do referer
        import urllib.parse
        tipo_acao_atual = ''
        data_distribuicao_atual = ''
        
        try:
            # Parseia o referer para extrair os parâmetros
            if 'analisar' in referer:
                query_string = referer.split('?', 1)[1] if '?' in referer else ''
                params = urllib.parse.parse_qs(query_string)
                
                tipo_acao_atual = params.get('tipo_acao', [''])[0]
                data_distribuicao_atual = params.get('data_distribuicao', [''])[0]
        except Exception as e:
            print(f"Erro ao extrair parâmetros do referer: {e}")
        
        redirect_url = f"/analisar"
        
        # Somente adiciona os parâmetros se estiverem presentes
        if tipo_acao_atual or data_distribuicao_atual:
            redirect_params = []
            if tipo_acao_atual:
                redirect_params.append(f"tipo_acao={urllib.parse.quote(tipo_acao_atual)}")
            if data_distribuicao_atual:
                redirect_params.append(f"data_distribuicao={urllib.parse.quote(data_distribuicao_atual)}")
            
            if redirect_params:
                redirect_url += "?" + "&".join(redirect_params)

        return jsonify({
            'success': True,
            'redirect': redirect_url
        })
    except Exception as e:
        print(f"Erro ao vincular informativo: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True)