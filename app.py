import os
import uuid
import json
import time
import gspread
from flask import Flask, render_template, request, jsonify
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# Nome oficial da planilha
NOME_PLANILHA = 'contagem_semi_acabado'

# --- CACHE EM MEMÓRIA PARA A BUSCA RÁPIDA ---
CACHE_PRODUTOS = []
ULTIMA_ATUALIZACAO_CACHE = 0
TEMPO_CACHE_SEGUNDOS = 600  # Atualiza o cache a cada 10 minutos (600s)

def conectar_google_sheets():
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        if "GOOGLE_CREDENTIALS_JSON" in os.environ:
            creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        else:
            creds = Credentials.from_service_account_file('credenciais.json', scopes=scopes)
            
        gc = gspread.authorize(creds)
        return gc
    except Exception as e:
        print(f"[ERRO] Falha ao autenticar: {e}")
        return None

def obter_produtos_cached():
    """Carrega os produtos na memória do servidor e atualiza apenas a cada 10 minutos."""
    global CACHE_PRODUTOS, ULTIMA_ATUALIZACAO_CACHE
    agora = time.time()
    
    # Se o cache estiver válido e com dados, usa a memória instantaneamente
    if CACHE_PRODUTOS and (agora - ULTIMA_ATUALIZACAO_CACHE < TEMPO_CACHE_SEGUNDOS):
        return CACHE_PRODUTOS

    try:
        print("[CACHE] Carregando produtos do Google Sheets...")
        gc = conectar_google_sheets()
        if not gc:
            return CACHE_PRODUTOS  # Se falhar, devolve o cache antigo
            
        planilha = gc.open(NOME_PLANILHA)
        sheet = planilha.worksheet('dados')
        dados = sheet.get_all_values()

        novos_produtos = []
        if len(dados) >= 2:
            for linha in dados[1:]:
                if len(linha) >= 1:
                    codigo = str(linha[0]).strip()
                    descricao = str(linha[1]).strip() if len(linha) > 1 else ''
                    if codigo:
                        novos_produtos.append({
                            'codigo': codigo,
                            'descricao': descricao,
                            'codigo_lower': codigo.lower(),
                            'descricao_lower': descricao.lower()
                        })

        CACHE_PRODUTOS = novos_produtos
        ULTIMA_ATUALIZACAO_CACHE = agora
        print(f"[CACHE] Carregados {len(CACHE_PRODUTOS)} produtos com sucesso!")
    except Exception as e:
        print(f"[ERRO CARREGAR CACHE]: {e}")

    return CACHE_PRODUTOS

# --- ROTA PRINCIPAL DO SITE ---
@app.route('/')
def index_contagem():
    return render_template('contagem_estoque.html')

# --- ROTA DE AUTOCOMPLETE (ULTRA RÁPIDA VIA CACHE) ---
@app.route('/buscar_produtos', methods=['GET'])
def buscar_produtos():
    termo = request.args.get('termo', '').lower().strip()
    if not termo:
        return jsonify([])

    produtos = obter_produtos_cached()
    resultados = []
    codigos_vistos = set()

    for p in produtos:
        if termo in p['codigo_lower'] or termo in p['descricao_lower']:
            cod_upper = p['codigo'].upper()
            if cod_upper not in codigos_vistos:
                codigos_vistos.add(cod_upper)
                resultados.append({'codigo': p['codigo'], 'descricao': p['descricao']})
                if len(resultados) >= 15:
                    break

    return jsonify(resultados)

# --- ROTA PARA FORÇAR ATUALIZAÇÃO DO CACHE (SE ALTERAR A PLANILHA) ---
@app.route('/atualizar_cache', methods=['GET'])
def atualizar_cache_manual():
    global ULTIMA_ATUALIZACAO_CACHE
    ULTIMA_ATUALIZACAO_CACHE = 0  # Força recarregar no próximo request
    obter_produtos_cached()
    return jsonify({"sucesso": True, "total_produtos": len(CACHE_PRODUTOS)})

# --- ROTA PARA SALVAR LOTE ---
# --- ROTA PARA SALVAR LOTE COM VALIDAÇÃO DE CÓDIGO ---
@app.route('/salvar_lote', methods=['POST'])
def salvar_lote():
    dados = request.json
    data_lote = dados.get('data')
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        # Obter a lista atualizada de produtos do cache
        produtos = obter_produtos_cached()
        codigos_validos = {p['codigo'].upper() for p in produtos}

        # Verificar se todos os itens possuem código válido na planilha 'dados'
        for item in itens:
            cod_item = str(item.get('CODIGO', '')).strip().upper()
            if not cod_item:
                return jsonify({'sucesso': False, 'mensagem': 'Existe um item com código em branco!'})
            if cod_item not in codigos_validos:
                return jsonify({
                    'sucesso': False, 
                    'mensagem': f'O código "{cod_item}" não existe na aba "dados" da planilha!'
                })

        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        
        try:
            sheet_mov = planilha.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            sheet_mov = planilha.add_worksheet(title=data_lote, rows="1000", cols="7")
            sheet_mov.append_row(["ID", "Data", "Código", "Quantidade", "Tipo", "Status", "Inventario Feito"])

        linhas_adicionar = []
        for item in itens:
            row_id = str(uuid.uuid4())[:8]
            linhas_adicionar.append([
                row_id,
                data_lote,
                item.get('CODIGO', ''),
                int(item.get('QUANTIDADE', 1)),
                'Contagem',
                'Não',
                'Não'
            ])

        sheet_mov.append_rows(linhas_adicionar)
        return jsonify({'sucesso': True, 'mensagem': f'Gravado com sucesso na aba {data_lote}!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar: {str(e)}'})





def consultar_movimentacao():
    data_consulta = request.args.get('data', '').strip()
    if not data_consulta:
        return jsonify({'sucesso': False, 'mensagem': 'Mês/Ano não informado!'})

    try:
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        try:
            sheet_mov = planilha.worksheet(data_consulta)
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({'sucesso': True, 'itens': []})

        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': True, 'itens': []})

        itens = []
        for idx in range(1, len(linhas)):
            row = linhas[idx]
            if any(row):
                itens.append({
                    "ID": row[0] if len(row) > 0 else '',
                    "Data": row[1] if len(row) > 1 else '',
                    "Código": row[2] if len(row) > 2 else '',
                    "Quantidade": row[3] if len(row) > 3 else '',
                    "Tipo": row[4] if len(row) > 4 else '',
                    "Status": row[5] if len(row) > 5 else 'Não'
                })

        return jsonify({'sucesso': True, 'itens': itens})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

# --- ROTA PARA EXCLUIR MOVIMENTAÇÃO ---
@app.route('/excluir_movimentacao', methods=['POST'])
def excluir_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    row_id = str(dados.get('id', '')).strip()
    try:
        gc = conectar_google_sheets()
        planilha = gc.open(NOME_PLANILHA)
        sheet_mov = planilha.worksheet(data_mov)
        linhas = sheet_mov.get_all_values()
        for idx in range(1, len(linhas)):
            if str(linhas[idx][0]).strip() == row_id:
                sheet_mov.delete_rows(idx + 1)
                return jsonify({'sucesso': True, 'mensagem': 'Item excluído!'})
        return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado.'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

# --- ROTA PARA ATUALIZAR STATUS ---
@app.route('/atualizar_status_lote_por_id', methods=['POST'])
def atualizar_status_lote_por_id():
    try:
        dados = request.get_json()
        data_consulta = str(dados.get('data', '')).strip()
        ids = [str(i).strip() for i in dados.get('ids', [])]
        novo_status = str(dados.get('status', 'Não')).strip()
        
        gc = conectar_google_sheets()
        aba = gc.open(NOME_PLANILHA).worksheet(data_consulta)
        linhas = aba.get_all_values()
        
        updates = []
        for idx, linha in enumerate(linhas[1:], start=2):
            if len(linha) > 0 and str(linha[0]).strip() in ids:
                updates.append({'range': f'F{idx}', 'values': [[novo_status]]})
        if updates:
            aba.batch_update(updates)
        return jsonify({'sucesso': True, 'mensagem': 'Status atualizado!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

# --- ROTA DE TESTE DIRETO ---
@app.route('/teste_direto_planilha', methods=['GET'])
def teste_direto_planilha():
    try:
        produtos = obter_produtos_cached()
        return jsonify({
            "sucesso": True,
            "total_linhas_cache": len(produtos),
            "exemplo": produtos[0] if produtos else {}
        })
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

if __name__ == '__main__':
    app.run(debug=True)
