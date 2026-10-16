import os
import uuid
import json
import gspread
from flask import Flask, render_template, request, jsonify
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# Nome oficial da planilha
NOME_PLANILHA = 'contagem_semi_acabado'

def conectar_google_sheets():
    try:
        # Define os escopos necessários para ler o Google Sheets e Drive
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

# --- ROTA PRINCIPAL DO SITE ---
@app.route('/')
def index_contagem():
    return render_template('contagem_estoque.html')

# --- ROTA DE AUTOCOMPLETE (BUSCA) ---
@app.route('/buscar_produtos', methods=['GET'])
def buscar_produtos():
    termo = request.args.get('termo', '').lower().strip()
    if not termo:
        return jsonify([])
    
    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify([])
            
        planilha = gc.open(NOME_PLANILHA)
        sheet = planilha.worksheet('dados')
        dados = sheet.get_all_values()
        
        if len(dados) < 2:
            return jsonify([])

        resultados = []
        codigos_vistos = set()

        # Lê da linha 2 em diante (A = PRODUTO / Código, B = DESCRICAO)
        for linha in dados[1:]:
            if len(linha) >= 1:
                codigo = str(linha[0]).strip()
                descricao = str(linha[1]).strip() if len(linha) > 1 else ''

                if codigo and (termo in codigo.lower() or termo in descricao.lower()):
                    cod_upper = codigo.upper()
                    if cod_upper not in codigos_vistos:
                        codigos_vistos.add(cod_upper)
                        resultados.append({'codigo': codigo, 'descricao': descricao})
                        if len(resultados) >= 15:
                            break

        return jsonify(resultados)
    except Exception as e:
        print(f"[ERRO BUSCA]: {e}")
        return jsonify([])

# --- ROTA PARA SALVAR LOTE ---
@app.route('/salvar_lote', methods=['POST'])
def salvar_lote():
    dados = request.json
    data_lote = dados.get('data')
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
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
        return jsonify({'sucesso': False, 'mensagem': f'Erro: {str(e)}'})

# --- ROTA PARA CONSULTAR MOVIMENTAÇÃO ---
@app.route('/consultar_movimentacao', methods=['GET'])
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
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({"sucesso": False, "erro": "Falha na autenticação com o Google Sheets"})
        
        planilha = gc.open(NOME_PLANILHA)
        sheet = planilha.worksheet('dados')
        linhas = sheet.get_all_values()
        
        return jsonify({
            "sucesso": True,
            "nome_planilha": planilha.title,
            "total_linhas": len(linhas),
            "cabecalho": linhas[0] if len(linhas) > 0 else []
        })
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

if __name__ == '__main__':
    app.run(debug=True)
