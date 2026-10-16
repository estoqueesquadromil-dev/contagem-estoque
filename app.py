import json
import os
import pandas as pd
import gspread
import uuid
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

NOME_PLANILHA_PRODUTOS = 'contagem_semi_acabado'
NOME_PLANILHA_MOVIMENTACAO = 'MOVIMENTACAO_NOMUS'

produtos_cache = []
codigos_validos_set = set()

def conectar_google_sheets():
    try:
        if "GOOGLE_CREDENTIALS_JSON" in os.environ:
            creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
            gc = gspread.service_account_from_dict(creds_dict)
        else:
            gc = gspread.service_account(filename='credenciais.json')
        return gc
    except Exception as e:
        print(f"[ERRO] Falha ao autenticar com o Google Sheets: {e}")
        return None

def carregar_base_produtos():
    global produtos_cache, codigos_validos_set
    try:
        gc = conectar_google_sheets()
        if not gc:
            print("[ERRO] Não foi possível conectar ao Google Sheets para carregar produtos.")
            return
        
        planilha = gc.open(NOME_PLANILHA_PRODUTOS)
        sheet = planilha.worksheet('dados')
        dados = sheet.get_all_records()
        print(f"[DEBUG] Dados brutos lidos da aba 'dados': {len(dados)} linhas encontradas.")
        
        df = pd.DataFrame(dados)
        print(f"[DEBUG] Colunas encontradas no DataFrame: {df.columns.tolist()}")
        
        produtos_cache = []
        codigos_validos_set = set()
        for _, row in df.iterrows():
            codigo = str(row.get('PRODUTO', row.get('CODIGO', row.get('Produto', '')))).strip()
            descricao = str(row.get('DESCRICAO', row.get('Descrição', ''))).strip()
            if codigo and codigo.lower() != 'nan':
                produtos_cache.append({'codigo': codigo, 'descricao': descricao})
                codigos_validos_set.add(codigo)
                
        print(f"[INFO] {len(produtos_cache)} produtos carregados com sucesso para o cache!")
    except Exception as e:
        print(f"[ERRO CRITICO] Falha ao carregar produtos da planilha: {e}")

# Carrega a base ao iniciar
carregar_base_produtos()

@app.route('/')
def index_contagem():
    return render_template('contagem_estoque.html')

@app.route('/buscar_produtos', methods=['GET'])
def buscar_produtos():
    termo = request.args.get('termo', '').lower().strip()
    if not termo:
        return jsonify([])
    
    resultados = []
    for p in produtos_cache:
        if termo in p['codigo'].lower() or termo in p['descricao'].lower():
            resultados.append(p)
            if len(resultados) >= 30:
                break
    return jsonify(resultados)

@app.route('/salvar_lote', methods=['POST'])
def salvar_lote():
    dados = request.json
    data_lote = dados.get('data')
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Mês/Ano ou itens não informados!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            sheet_mov = planilha_mov.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            sheet_mov = planilha_mov.add_worksheet(title=data_lote, rows="1000", cols="7")
            sheet_mov.append_row(["ID", "Data", "Código", "Quantidade", "Tipo", "Status", "Inventario Feito"])

        linhas_para_adicionar = []
        for item in itens:
            codigo = item.get('CODIGO', '')
            quantidade = int(item.get('QUANTIDADE', 1))
            tipo = item.get('TIPO', 'Contagem')
            status = 'Não'
            inventario_feito = 'Não'
            
            row_id = str(uuid.uuid4())[:8]
            linhas_para_adicionar.append([row_id, data_lote, codigo, quantidade, tipo, status, inventario_feito])

        sheet_mov.append_rows(linhas_para_adicionar)
        return jsonify({'sucesso': True, 'mensagem': f'Sucesso! Itens guardados na aba {data_lote}.'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao processar: {str(e)}'})

@app.route('/consultar_movimentacao', methods=['GET'])
def consultar_movimentacao():
    data_consulta = request.args.get('data', '').strip()
    if not data_consulta:
        return jsonify({'sucesso': False, 'mensagem': 'Mês/Ano não informado!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        try:
            sheet_mov = planilha_mov.worksheet(data_consulta)
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({'sucesso': True, 'itens': [], 'mensagem': f'Nenhuma contagem encontrada para {data_consulta}.'})

        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': True, 'itens': [], 'mensagem': 'Aba vazia.'})

        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        itens_formatados = []
        for idx in range(1, len(linhas)):
            row = linhas[idx]
            if not any(row): continue
            
            row_id = row[cabecalhos.index("ID")] if "ID" in cabecalhos and len(row) > cabecalhos.index("ID") else f"linha_{idx+1}"
            data_val = row[cabecalhos.index("DATA")] if "DATA" in cabecalhos and len(row) > cabecalhos.index("DATA") else data_consulta
            codigo_val = row[cabecalhos.index("CÓDIGO")] if "CÓDIGO" in cabecalhos and len(row) > cabecalhos.index("CÓDIGO") else (row[cabecalhos.index("CODIGO")] if "CODIGO" in cabecalhos and len(row) > cabecalhos.index("CODIGO") else "")
            qtd_val = row[cabecalhos.index("QUANTIDADE")] if "QUANTIDADE" in cabecalhos and len(row) > cabecalhos.index("QUANTIDADE") else 1
            tipo_val = row[cabecalhos.index("TIPO")] if "TIPO" in cabecalhos and len(row) > cabecalhos.index("TIPO") else "Contagem"
            status_val = row[cabecalhos.index("STATUS")] if "STATUS" in cabecalhos and len(row) > cabecalhos.index("STATUS") else "Não"

            itens_formatados.append({"ID": row_id, "Data": data_val, "Código": codigo_val, "Quantidade": qtd_val, "Tipo": tipo_val, "Status": status_val})

        return jsonify({'sucesso': True, 'itens': itens_formatados})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

@app.route('/excluir_movimentacao', methods=['POST'])
def excluir_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    row_id = str(dados.get('id', '')).strip()
    try:
        gc = conectar_google_sheets()
        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        linhas = sheet_mov.get_all_values()
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        col_id_idx = cabecalhos.index("ID") + 1
        for idx in range(1, len(linhas)):
            if str(linhas[idx][col_id_idx - 1]).strip() == row_id:
                sheet_mov.delete_rows(idx + 1)
                return jsonify({'sucesso': True, 'mensagem': 'Item excluído com sucesso!'})
        return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado.'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

@app.route('/atualizar_status_lote_por_id', methods=['POST'])
def atualizar_status_lote_por_id():
    try:
        dados = request.get_json()
        data_consulta = str(dados.get('data', '')).strip()
        ids = [str(i).strip() for i in dados.get('ids', [])]
        novo_status = str(dados.get('status', 'Não')).strip()
        gc = conectar_google_sheets()
        aba = gc.open(NOME_PLANILHA_MOVIMENTACAO).worksheet(data_consulta)
        linhas = aba.get_all_values()
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        col_id = cabecalhos.index("ID") + 1
        col_st = cabecalhos.index("STATUS") + 1
        updates = []
        for idx, linha in enumerate(linhas[1:], start=2):
            if len(linha) >= col_id and str(linha[col_id-1]).strip() in ids:
                updates.append({'range': gspread.utils.rowcol_to_a1(idx, col_st), 'values': [[novo_status]]})
        if updates:
            aba.batch_update(updates)
        return jsonify({'sucesso': True, 'mensagem': 'Status atualizado com sucesso!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

if __name__ == '__main__':
    app.run(debug=True)
