# Sistema de Análise de Processos Judiciais

Este sistema permite o upload, processamento e análise de documentos judiciais, com funcionalidades específicas para diferentes tipos de documentos como Petição Inicial, informativos CAF, SPPREV e Extratão.

## Características

- Upload de múltiplos PDFs com classificação por tipo
- Extração de texto automatizada
- Parsers específicos para cada tipo de documento
- Cruzamento de dados entre documentos
- Análise inteligente via API do OpenAI
- Interface de usuário moderna e responsiva
- Exportação de resultados para Excel

## Instalação

1. Clone o repositório:
```bash
git clone https://github.com/seu-usuario/sistema-analise-processos.git
cd sistema-analise-processos
```

2. Crie um ambiente virtual e instale as dependências:
```bash
python -m venv venv
source venv/bin/activate  # No Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Configure o arquivo `.env` com sua chave da API OpenAI:
```bash
cp .env.example .env
# Edite o arquivo .env e adicione sua chave da API OpenAI
```

4. Execute o aplicativo:
```bash
flask run
```

5. Acesse o sistema no navegador:
```
http://localhost:5000
```

## Uso

1. **Upload de documentos**:
   - Selecione o tipo de documento (Inicial, Informativo CAF, etc.)
   - Arraste e solte os arquivos ou clique para selecionar
   - Clique em "Enviar Arquivos"

2. **Análise**:
   - Após enviar os documentos necessários, clique em "Iniciar Análise"
   - O sistema processará os documentos e exibirá os resultados
   - Você pode filtrar os resultados por nome ou status da documentação

3. **Exportação**:
   - Clique em "Baixar Relatório Excel" para exportar os resultados

## Tipos de Documentos Suportados

- **Inicial**: Documento com a petição inicial contendo informações dos autores
- **Informativo CAF**: Documentos do CAF com informações do autor
- **Informativo SPPREV**: Documentos da SPPREV com dados do beneficiário
- **Informativo Extratão**: Documentos do Extratão
- **Litispendência**: Documentos de litispendência

## Estrutura do Projeto

```
├── app.py                 # Aplicação principal
├── static/                # Arquivos estáticos
│   └── style.css          # Estilos CSS
├── templates/             # Templates HTML
│   ├── index.html         # Página inicial
│   ├── resultado.html     # Página de resultados
│   └── erro.html          # Página de erro
├── uploads/               # Pasta para arquivos enviados
├── resultados/            # Pasta para resultados exportados
├── .env                   # Variáveis de ambiente
└── requirements.txt       # Dependências do projeto
```

## Tecnologias Utilizadas

- **Backend**: Python, Flask
- **Extração de PDF**: pdfplumber, pypdf
- **Processamento de Dados**: pandas
- **IA para Análise**: OpenAI API (GPT)
- **Frontend**: HTML, CSS, JavaScript
- **Exportação**: pandas (Excel)

## Funcionamento Técnico

### Processamento de PDFs

1. **Seleção do Tipo**: Cada PDF é associado a um tipo (Inicial, CAF, etc.)
2. **Otimização**: Os documentos não-iniciais são recortados para as 2 primeiras páginas
3. **Extração**: O texto é extraído usando pdfplumber

### Parsers Específicos

- **Inicial**: Captura nomes, RGs e CPFs dos autores
- **CAF**: Extrai dados como nome e RG 
- **SPPREV**: Identifica informações do beneficiário
- **Extratão**: Localiza CPF e outras informações

### Cruzamento de Dados

O sistema cruza automaticamente as informações dos diferentes documentos:
- Por CPF (exato)
- Por RG (com tolerância para variações)
- Por nome (quando necessário)

### Análise via IA

Utiliza a API da OpenAI para analisar os documentos e fornecer insights sobre:
- Documentos faltantes
- Recomendações para o processo
- Outras observações relevantes

## Expansão Futura

Ideias para futuras versões:
- Suporte a OCR para documentos escaneados
- Dashboard com métricas e gráficos
- Sistema de acompanhamento de processos
- Integração com sistemas judiciais
- Análise comparativa de múltiplos processos

## Licença

Este projeto é licenciado sob a Licença MIT - veja o arquivo LICENSE para detalhes.

## Suporte

Para dúvidas ou problemas, abra uma issue no GitHub ou entre em contato pelo e-mail: seu-email@exemplo.com